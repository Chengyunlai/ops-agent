"""Parse read-only Kubernetes Pod logs without discarding source text."""

import re
from dataclasses import dataclass, replace
from datetime import datetime

from ops_agent.monitoring.models import KubernetesLogLevel, KubernetesLogRecord

_TIMESTAMP_PREFIX = re.compile(r"^(?P<timestamp>\S+)\s(?P<message>.*)$")
_ERROR_LEVEL = re.compile(
    r"\b(error|fatal|critical|panic|exception|traceback)\b"
    r"|\b[A-Za-z_]\w*(?:Error|Exception)\b",
    re.IGNORECASE,
)
_EXCEPTION_STACK_START = re.compile(
    r"\b(traceback|exception|panic)\b"
    r"|^(?:[A-Za-z_][\w.]*?)?(?:Error|Exception):",
    re.IGNORECASE,
)
_EXCEPTION_STACK_CONTINUATION = re.compile(
    r"^(?:\s+|File\s|at\s|Caused by:|During handling\s|\.\.\. \d+ more)"
    r"|^[A-Za-z_][\w.]*?(?:Error|Exception):"
    r"|^goroutine \d+ \[.*\]:$"
    r"|^created by\s"
    r"|^\d+:\s"
    r"|^[^\s].*\([^)]*\)$",
    re.IGNORECASE,
)
_WARNING_LEVEL = re.compile(r"\bwarn(?:ing)?\b", re.IGNORECASE)
_INFO_LEVEL = re.compile(r"\binfo\b", re.IGNORECASE)
_DEBUG_LEVEL = re.compile(r"\b(?:debug|trace)\b", re.IGNORECASE)
_HTTP_STATUS = re.compile(r"\bHTTP/\d(?:\.\d)?[\"']?\s+(?P<status>[1-5]\d{2})\b")


def parse_kubernetes_log_records(
    content: str,
    *,
    container: str | None,
) -> tuple[KubernetesLogRecord, ...]:
    parser = KubernetesLogParser(container=container)
    return tuple(parser.parse(line) for line in content.splitlines())


@dataclass
class KubernetesLogParser:
    """Classify a stream while retaining exception stack context."""

    container: str | None
    _in_exception_stack: bool = False

    def parse(self, line: str) -> KubernetesLogRecord:
        record = parse_kubernetes_log_record(line, container=self.container)
        if _EXCEPTION_STACK_START.search(record.message):
            self._in_exception_stack = True
        elif self._in_exception_stack and _EXCEPTION_STACK_CONTINUATION.search(
            record.message
        ):
            record = replace(record, level=KubernetesLogLevel.ERROR)
        else:
            self._in_exception_stack = False
        return record


def parse_kubernetes_log_record(
    line: str,
    *,
    container: str | None,
) -> KubernetesLogRecord:
    timestamp = None
    message = line
    match = _TIMESTAMP_PREFIX.match(line)
    if match is not None:
        timestamp = _parse_timestamp(match.group("timestamp"))
        if timestamp is not None:
            message = match.group("message")
    return KubernetesLogRecord(
        container=container,
        timestamp=timestamp,
        message=message,
        raw=line,
        level=_classify_level(message),
    )


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _classify_level(message: str) -> KubernetesLogLevel:
    if _ERROR_LEVEL.search(message):
        return KubernetesLogLevel.ERROR
    status_match = _HTTP_STATUS.search(message)
    if status_match is not None:
        status = int(status_match.group("status"))
        if status >= 500:
            return KubernetesLogLevel.ERROR
        if status >= 400:
            return KubernetesLogLevel.WARNING
    if _WARNING_LEVEL.search(message):
        return KubernetesLogLevel.WARNING
    if _DEBUG_LEVEL.search(message):
        return KubernetesLogLevel.DEBUG
    if _INFO_LEVEL.search(message):
        return KubernetesLogLevel.INFO
    return KubernetesLogLevel.UNKNOWN
