"""Deterministic local Focus and search for immutable Kubernetes log records."""

import re
from collections.abc import Sequence

from ops_agent.monitoring.models import (
    KubernetesLogFocus,
    KubernetesLogFocusResult,
    KubernetesLogLevel,
    KubernetesLogRecord,
    KubernetesLogSearch,
    KubernetesLogSearchMatch,
    KubernetesLogSearchResult,
)

_HEALTH_CHECK = re.compile(
    r"/(?:healthz?|readyz?|livez?|metrics)(?:[/?\s\"']|$)",
    re.IGNORECASE,
)
_ACCESS_LOG = re.compile(
    r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+\s+HTTP/\d(?:\.\d)?",
    re.IGNORECASE,
)


def apply_kubernetes_log_focus(
    records: Sequence[KubernetesLogRecord],
    focus: KubernetesLogFocus,
) -> KubernetesLogFocusResult:
    visible = tuple(record for record in records if not _is_hidden(record, focus))
    return KubernetesLogFocusResult(
        records=visible,
        hidden_count=len(records) - len(visible),
    )


def search_kubernetes_log_records(
    records: Sequence[KubernetesLogRecord],
    search: KubernetesLogSearch,
) -> KubernetesLogSearchResult:
    if not search.text:
        return KubernetesLogSearchResult(matches=())
    flags = 0 if search.case_sensitive else re.IGNORECASE
    pattern_text = search.text if search.regex else re.escape(search.text)
    try:
        pattern = re.compile(pattern_text, flags)
    except re.error as error:
        position = f"（位置 {error.pos}）" if error.pos is not None else ""
        return KubernetesLogSearchResult(
            matches=(),
            error=f"正则表达式无效：{error.msg}{position}",
        )
    matches = tuple(
        KubernetesLogSearchMatch(
            record_index=record_index,
            spans=(
                ((match.start(), match.end()),) if match.start() != match.end() else ()
            ),
        )
        for record_index, record in enumerate(records)
        for match in pattern.finditer(record.raw)
    )
    return KubernetesLogSearchResult(matches=matches)


def _is_hidden(
    record: KubernetesLogRecord,
    focus: KubernetesLogFocus,
) -> bool:
    if focus.hide_info and record.level is KubernetesLogLevel.INFO:
        return True
    if focus.hide_debug and record.level is KubernetesLogLevel.DEBUG:
        return True
    if focus.hide_health_checks and _HEALTH_CHECK.search(record.message):
        return True
    if focus.hide_access_logs and _ACCESS_LOG.search(record.message):
        return True
    content = record.raw.casefold()
    return any(term.casefold() in content for term in focus.hidden_text)
