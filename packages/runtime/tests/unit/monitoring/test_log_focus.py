from datetime import UTC, datetime

import pytest
from ops_agent.monitoring import (
    KubernetesLogFocus,
    KubernetesLogLevel,
    KubernetesLogRecord,
    KubernetesLogSearch,
    apply_kubernetes_log_focus,
    search_kubernetes_log_records,
)


def _record(
    message: str,
    *,
    level: KubernetesLogLevel = KubernetesLogLevel.INFO,
) -> KubernetesLogRecord:
    return KubernetesLogRecord(
        container="api",
        timestamp=datetime(2026, 8, 4, 5, 0, tzinfo=UTC),
        message=message,
        raw=f"2026-08-04T05:00:00Z {message}",
        level=level,
    )


def test_log_focus_defaults_to_complete_unchanged_evidence() -> None:
    records = (
        _record("INFO request completed"),
        _record("DEBUG cache lookup", level=KubernetesLogLevel.DEBUG),
    )

    result = apply_kubernetes_log_focus(records, KubernetesLogFocus())

    assert result.records == records
    assert result.hidden_count == 0
    assert records[0].raw == "2026-08-04T05:00:00Z INFO request completed"


@pytest.mark.parametrize(
    ("focus", "expected_messages"),
    [
        (
            KubernetesLogFocus(hide_info=True),
            ("DEBUG cache lookup", "WARN queue slow"),
        ),
        (
            KubernetesLogFocus(hide_debug=True),
            (
                "INFO request completed",
                'INFO "GET /healthz HTTP/1.1" 200',
                'INFO "GET /api/tasks HTTP/1.1" 200',
                "WARN queue slow",
            ),
        ),
        (
            KubernetesLogFocus(hide_health_checks=True),
            (
                "INFO request completed",
                "DEBUG cache lookup",
                'INFO "GET /api/tasks HTTP/1.1" 200',
                "WARN queue slow",
            ),
        ),
        (
            KubernetesLogFocus(hide_access_logs=True),
            (
                "INFO request completed",
                "DEBUG cache lookup",
                "WARN queue slow",
            ),
        ),
    ],
)
def test_log_focus_applies_explicit_operator_categories(
    focus: KubernetesLogFocus,
    expected_messages: tuple[str, ...],
) -> None:
    records = (
        _record("INFO request completed"),
        _record("DEBUG cache lookup", level=KubernetesLogLevel.DEBUG),
        _record('INFO "GET /healthz HTTP/1.1" 200'),
        _record('INFO "GET /api/tasks HTTP/1.1" 200'),
        _record("WARN queue slow", level=KubernetesLogLevel.WARNING),
    )

    result = apply_kubernetes_log_focus(records, focus)

    assert tuple(record.message for record in result.records) == expected_messages
    assert result.hidden_count == len(records) - len(expected_messages)


def test_log_focus_applies_case_insensitive_literal_rules() -> None:
    records = (
        _record("INFO noisy scheduler heartbeat"),
        _record("ERROR scheduler unavailable", level=KubernetesLogLevel.ERROR),
    )

    result = apply_kubernetes_log_focus(
        records,
        KubernetesLogFocus(hidden_text=("HEARTBEAT",)),
    )

    assert result.records == (records[1],)
    assert result.hidden_count == 1


def test_log_search_is_case_insensitive_and_returns_record_spans() -> None:
    records = (
        _record("ERROR Database unavailable", level=KubernetesLogLevel.ERROR),
        _record("INFO request completed"),
        _record("WARN database latency", level=KubernetesLogLevel.WARNING),
    )

    result = search_kubernetes_log_records(
        records,
        KubernetesLogSearch(text="database"),
    )

    assert result.error is None
    assert tuple(match.record_index for match in result.matches) == (0, 2)
    first_start = records[0].raw.lower().index("database")
    second_start = records[2].raw.lower().index("database")
    assert result.matches[0].spans == ((first_start, first_start + 8),)
    assert result.matches[1].spans == ((second_start, second_start + 8),)


def test_log_search_returns_each_occurrence_as_a_navigable_match() -> None:
    records = (_record("ERROR database unavailable; database retry failed"),)

    result = search_kubernetes_log_records(
        records,
        KubernetesLogSearch(text="database"),
    )

    first_start = records[0].raw.index("database")
    second_start = records[0].raw.index("database", first_start + 1)
    assert tuple(match.record_index for match in result.matches) == (0, 0)
    assert tuple(match.spans for match in result.matches) == (
        ((first_start, first_start + 8),),
        ((second_start, second_start + 8),),
    )


def test_log_search_supports_regex_and_reports_invalid_patterns() -> None:
    records = (
        _record("request_id=demo-001 status=503"),
        _record("request_id=demo-002 status=200"),
    )

    result = search_kubernetes_log_records(
        records,
        KubernetesLogSearch(text=r"status=5\d\d", regex=True),
    )
    invalid = search_kubernetes_log_records(
        records,
        KubernetesLogSearch(text="[", regex=True),
    )

    assert tuple(match.record_index for match in result.matches) == (0,)
    match_start = records[0].raw.index("status=503")
    assert result.matches[0].spans == ((match_start, match_start + 10),)
    assert invalid.matches == ()
    assert invalid.error is not None
    assert "正则表达式无效" in invalid.error
