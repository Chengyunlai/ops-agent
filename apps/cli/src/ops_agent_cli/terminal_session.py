from __future__ import annotations

import errno
import fcntl
import json
import os
import selectors
import signal
import subprocess
import sys
import termios
import tty
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

_MAX_DOWNLOAD_PATH_BYTES = 16 * 1024
_MAX_DOWNLOAD_LENGTH_DIGITS = len(str(_MAX_DOWNLOAD_PATH_BYTES))


class InteractiveTerminalError(Exception):
    """本机终端无法承载 Interactive Pod Session。"""


@dataclass(frozen=True)
class _DownloadRequest:
    remote_path: str


class _DownloadProtocol:
    def __init__(self, token: str) -> None:
        self._prefix = b"\x1b]777;ops-agent-download;" + token.encode("ascii") + b";"
        self._buffer = bytearray()
        self._discard_bytes_remaining = 0
        self._drop_until_finish = False

    def feed(self, chunk: bytes) -> Iterator[bytes | _DownloadRequest]:
        if self._drop_until_finish:
            return
        self._buffer.extend(chunk)
        while self._buffer:
            if self._discard_bytes_remaining:
                discarded = min(
                    len(self._buffer),
                    self._discard_bytes_remaining,
                )
                del self._buffer[:discarded]
                self._discard_bytes_remaining -= discarded
                if self._discard_bytes_remaining:
                    return
                continue
            marker_start = self._buffer.find(self._prefix)
            if marker_start < 0:
                pending_length = _matching_prefix_suffix_length(
                    self._buffer,
                    self._prefix,
                )
                safe_length = len(self._buffer) - pending_length
                if safe_length:
                    yield bytes(self._buffer[:safe_length])
                    del self._buffer[:safe_length]
                return
            if marker_start:
                yield bytes(self._buffer[:marker_start])
                del self._buffer[:marker_start]
            length_end = self._buffer.find(b";", len(self._prefix))
            if length_end < 0:
                length_payload = bytes(self._buffer[len(self._prefix) :])
                if not length_payload or (
                    length_payload.isdigit()
                    and len(length_payload) <= _MAX_DOWNLOAD_LENGTH_DIGITS
                ):
                    return
                self._buffer.clear()
                self._drop_until_finish = True
                yield b"\r\n[OPS AGENT] download request is invalid\r\n"
                return
            length_payload = bytes(self._buffer[len(self._prefix) : length_end])
            if (
                not length_payload
                or not length_payload.isdigit()
                or len(length_payload) > _MAX_DOWNLOAD_LENGTH_DIGITS
            ):
                self._buffer.clear()
                self._drop_until_finish = True
                yield b"\r\n[OPS AGENT] download request is invalid\r\n"
                return
            path_length = int(length_payload)
            if path_length > _MAX_DOWNLOAD_PATH_BYTES:
                del self._buffer[: length_end + 1]
                self._discard_bytes_remaining = path_length
                yield b"\r\n[OPS AGENT] download request is too large\r\n"
                continue
            path_end = length_end + 1 + path_length
            if len(self._buffer) < path_end:
                return
            path_payload = bytes(self._buffer[length_end + 1 : path_end])
            del self._buffer[:path_end]
            try:
                remote_path = path_payload.decode("utf-8")
            except UnicodeDecodeError:
                yield b"\r\n[OPS AGENT] download request is invalid\r\n"
                continue
            if not remote_path or not remote_path.isprintable():
                yield b"\r\n[OPS AGENT] download request is invalid\r\n"
                continue
            yield _DownloadRequest(remote_path=remote_path)

    def finish(self) -> bytes:
        if self._drop_until_finish or self._discard_bytes_remaining:
            self._buffer.clear()
            self._discard_bytes_remaining = 0
            self._drop_until_finish = False
            return b""
        if self._buffer.startswith(self._prefix):
            self._buffer.clear()
            return b"\r\n[OPS AGENT] incomplete download request discarded\r\n"
        remainder = bytes(self._buffer)
        self._buffer.clear()
        return remainder


def run_interactive_terminal(
    command: Sequence[str],
    *,
    environment: dict[str, str],
    download_token: str,
    download_file: Callable[[str], str],
) -> int:
    """代理一个真实 PTY，并处理 Shell 内的本机下载请求。"""
    input_fd = sys.stdin.fileno()
    output_fd = sys.stdout.fileno()
    if not os.isatty(input_fd) or not os.isatty(output_fd):
        raise InteractiveTerminalError("当前输入输出不是交互式终端")

    original_terminal = termios.tcgetattr(input_fd)
    master_fd, slave_fd = os.openpty()
    process: subprocess.Popen[bytes] | None = None
    previous_resize_handler = signal.getsignal(signal.SIGWINCH)
    selector = selectors.DefaultSelector()

    def resize_terminal(*_: object) -> None:
        try:
            window_size = fcntl.ioctl(
                input_fd,
                termios.TIOCGWINSZ,
                b"\0" * 8,
            )
            fcntl.ioctl(
                master_fd,
                termios.TIOCSWINSZ,
                window_size,
            )
        except OSError:
            return

    try:
        resize_terminal()
        process = subprocess.Popen(
            list(command),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=environment,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        tty.setraw(input_fd)
        signal.signal(signal.SIGWINCH, resize_terminal)
        selector.register(input_fd, selectors.EVENT_READ, "input")
        selector.register(master_fd, selectors.EVENT_READ, "remote")
        protocol = _DownloadProtocol(download_token)
        remote_open = True

        while remote_open:
            for key, _ in selector.select():
                if key.data == "input":
                    data = os.read(input_fd, 64 * 1024)
                    if data:
                        _write_all(master_fd, data)
                    else:
                        selector.unregister(input_fd)
                    continue
                try:
                    data = os.read(master_fd, 64 * 1024)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    data = b""
                if not data:
                    selector.unregister(master_fd)
                    remote_open = False
                    break
                for event in protocol.feed(data):
                    if isinstance(event, bytes):
                        _write_all(output_fd, event)
                        continue
                    if not _confirm_download(
                        input_fd,
                        output_fd,
                        event.remote_path,
                    ):
                        _write_all(
                            output_fd,
                            b"[OPS AGENT] download cancelled\r\n",
                        )
                        continue
                    try:
                        message = download_file(event.remote_path)
                    except Exception as error:  # noqa: BLE001 - 回显下载边界错误
                        message = f"下载失败：{error}"
                    _write_all(
                        output_fd,
                        f"\r\n[OPS AGENT] {message}\r\n".encode(),
                    )

        remainder = protocol.finish()
        if remainder:
            _write_all(output_fd, remainder)
        return process.wait()
    except OSError as error:
        raise InteractiveTerminalError(
            f"Interactive Pod Session 终端代理失败: {error}"
        ) from error
    finally:
        _attempt_cleanup(selector.close)
        _attempt_cleanup(
            lambda: signal.signal(signal.SIGWINCH, previous_resize_handler)
        )
        _attempt_cleanup(
            lambda: termios.tcsetattr(
                input_fd,
                termios.TCSADRAIN,
                original_terminal,
            )
        )
        if slave_fd >= 0:
            _attempt_cleanup(lambda: os.close(slave_fd))
        _attempt_cleanup(lambda: os.close(master_fd))
        if process is not None and process.poll() is None:
            _stop_process(process)


def _confirm_download(
    input_fd: int,
    output_fd: int,
    remote_path: str,
) -> bool:
    display_path = _terminal_safe_display(remote_path)
    prompt = (
        f"\r\n[OPS AGENT] 确认下载 {display_path}？按 y 确认，其他键取消: "
    ).encode()
    _write_all(output_fd, prompt)
    response = os.read(input_fd, 1)
    approved = response.lower() == b"y"
    _write_all(output_fd, b"y\r\n" if approved else b"\r\n")
    return approved


def _terminal_safe_display(value: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    visible: list[str] = []
    for character in encoded:
        if character.isprintable():
            visible.append(character)
            continue
        code_point = ord(character)
        if code_point <= 0xFFFF:
            visible.append(f"\\u{code_point:04x}")
        else:
            visible.append(f"\\U{code_point:08x}")
    return "".join(visible)


def _matching_prefix_suffix_length(
    payload: bytearray,
    prefix: bytes,
) -> int:
    maximum = min(len(payload), len(prefix) - 1)
    for length in range(maximum, 0, -1):
        if payload[-length:] == prefix[:length]:
            return length
    return 0


def _attempt_cleanup(operation: Callable[[], object]) -> None:
    try:
        operation()
    except (OSError, ValueError):
        pass


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    _attempt_cleanup(process.terminate)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _attempt_cleanup(process.kill)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return


def _write_all(file_descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(file_descriptor, remaining)
        remaining = remaining[written:]
