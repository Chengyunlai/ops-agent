import base64
import os

from ops_agent_cli.terminal_session import (
    _confirm_download,
    _DownloadProtocol,
    _DownloadRequest,
)


def test_terminal_download_protocol_handles_chunked_marker_without_displaying_it() -> (
    None
):
    token = "abc123"
    remote_path = "/workspace/reports/daily report.log"
    marker = (
        b"\x1b]777;ops-agent-download;"
        + token.encode()
        + b";"
        + base64.b64encode(remote_path.encode())
        + b"\x07"
    )
    protocol = _DownloadProtocol(token)
    events: list[bytes | _DownloadRequest] = []

    for byte in b"before\r\n" + marker + b"after\r\n":
        events.extend(protocol.feed(bytes([byte])))
    events.append(protocol.finish())

    visible = b"".join(event for event in events if isinstance(event, bytes))
    requests = [
        event.remote_path for event in events if isinstance(event, _DownloadRequest)
    ]
    assert visible == b"before\r\nafter\r\n"
    assert requests == [remote_path]


def test_terminal_download_protocol_requires_session_token() -> None:
    protocol = _DownloadProtocol("expected")
    foreign_marker = (
        b"\x1b]777;ops-agent-download;foreign;"
        + base64.b64encode(b"/etc/passwd")
        + b"\x07"
    )

    visible = b"".join(
        [
            *(
                event
                for event in protocol.feed(foreign_marker)
                if isinstance(event, bytes)
            ),
            protocol.finish(),
        ]
    )

    assert visible == foreign_marker


def test_terminal_download_protocol_does_not_delay_plain_shell_prompt() -> None:
    protocol = _DownloadProtocol("expected")

    events = list(protocol.feed(b"root@pod:/workspace# "))

    assert events == [b"root@pod:/workspace# "]
    assert protocol.finish() == b""


def test_terminal_download_confirmation_requires_explicit_y() -> None:
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    try:
        os.write(input_write, b"y")

        assert _confirm_download(
            input_read,
            output_write,
            "/workspace/report.log",
        )

        output = os.read(output_read, 4096).decode()
        assert "/workspace/report.log" in output
        assert "按 y 确认" in output
    finally:
        os.close(input_read)
        os.close(input_write)
        os.close(output_read)
        os.close(output_write)


def test_terminal_download_confirmation_escapes_control_characters() -> None:
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    try:
        os.write(input_write, b"n")

        assert not _confirm_download(
            input_read,
            output_write,
            "/workspace/real\r\x1b[2Jfake.log",
        )

        output = os.read(output_read, 4096)
        assert b"/workspace/real" in output
        assert b"\\r\\u001b[2Jfake.log" in output
        assert b"\x1b[2J" not in output
    finally:
        os.close(input_read)
        os.close(input_write)
        os.close(output_read)
        os.close(output_write)


def test_terminal_download_confirmation_escapes_del_and_c1_controls() -> None:
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    try:
        os.write(input_write, b"n")

        assert not _confirm_download(
            input_read,
            output_write,
            "/workspace/real\x7f\x9bfake.log",
        )

        output = os.read(output_read, 4096)
        assert b"\\u007f\\u009b" in output
        assert b"\x7f" not in output
        assert b"\xc2\x9b" not in output
    finally:
        os.close(input_read)
        os.close(input_write)
        os.close(output_read)
        os.close(output_write)


def test_terminal_download_protocol_bounds_unterminated_request() -> None:
    token = "expected"
    protocol = _DownloadProtocol(token)
    forged = b"\x1b]777;ops-agent-download;expected;" + b"a" * (17 * 1024)

    visible = b"".join(
        event
        for event in [*protocol.feed(forged), protocol.finish()]
        if isinstance(event, bytes)
    )

    assert visible == forged
