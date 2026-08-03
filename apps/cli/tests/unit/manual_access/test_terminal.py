import os

from ops_agent_cli.manual_access.terminal import (
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
        + str(len(remote_path.encode())).encode()
        + b";"
        + remote_path.encode()
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


def test_terminal_download_protocol_accepts_utf8_path() -> None:
    token = "abc123"
    remote_path = "/workspace/报告/每日结果.log"
    marker = (
        b"\x1b]777;ops-agent-download;"
        + token.encode()
        + b";"
        + str(len(remote_path.encode())).encode()
        + b";"
        + remote_path.encode()
    )

    events = list(_DownloadProtocol(token).feed(marker))

    assert events == [_DownloadRequest(remote_path=remote_path)]


def test_terminal_download_protocol_requires_session_token() -> None:
    protocol = _DownloadProtocol("expected")
    foreign_marker = b"\x1b]777;ops-agent-download;foreign;11;/etc/passwd"

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


def test_terminal_download_protocol_rejects_invalid_utf8() -> None:
    protocol = _DownloadProtocol("expected")
    marker = b"\x1b]777;ops-agent-download;expected;2;\xff\xfeafter"

    events = list(protocol.feed(marker))

    assert events == [
        b"\r\n[OPS AGENT] download request is invalid\r\n",
        b"after",
    ]


def test_terminal_download_protocol_rejects_control_characters() -> None:
    protocol = _DownloadProtocol("expected")
    path = b"/workspace/real\x07fake.log"
    marker = (
        b"\x1b]777;ops-agent-download;expected;"
        + str(len(path)).encode()
        + b";"
        + path
        + b"after"
    )

    events = list(protocol.feed(marker))

    assert events == [
        b"\r\n[OPS AGENT] download request is invalid\r\n",
        b"after",
    ]


def test_terminal_download_protocol_rejects_oversized_declared_path() -> None:
    protocol = _DownloadProtocol("expected")
    path = b"\x1b]52;c;forged\x07" + b"x" * (16385 - 14)
    header = b"\x1b]777;ops-agent-download;expected;16385;"

    events = [
        *protocol.feed(header + path[:100]),
        *protocol.feed(path[100:] + b"safe prompt"),
    ]

    assert events == [
        b"\r\n[OPS AGENT] download request is too large\r\n",
        b"safe prompt",
    ]
    assert protocol.finish() == b""


def test_terminal_download_protocol_does_not_replay_truncated_frame() -> None:
    protocol = _DownloadProtocol("expected")
    truncated = (
        b"\x1b]777;ops-agent-download;expected;100;"
        b"/workspace/report\x1b]52;c;forged\x07"
    )

    events = list(protocol.feed(truncated))
    remainder = protocol.finish()

    assert events == []
    assert remainder == (b"\r\n[OPS AGENT] incomplete download request discarded\r\n")
    assert b"\x1b]52" not in remainder


def test_terminal_download_protocol_bounds_unterminated_length_header() -> None:
    token = "expected"
    protocol = _DownloadProtocol(token)
    forged = b"\x1b]777;ops-agent-download;expected;" + b"1" * 32

    visible = b"".join(
        event
        for event in [*protocol.feed(forged), protocol.finish()]
        if isinstance(event, bytes)
    )

    assert visible == b"\r\n[OPS AGENT] download request is invalid\r\n"
