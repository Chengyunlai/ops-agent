from datetime import UTC, datetime

import pytest
from kubernetes.client.exceptions import ApiException
from ops_agent.kubernetes.metrics import KubernetesMetricsApiSource
from ops_agent.monitoring import (
    KubernetesContainerMetrics,
    KubernetesPodMetrics,
    KubernetesPodMetricsSnapshot,
)
from ops_agent.monitoring.metrics import (
    CachedKubernetesMetricsSource,
    KubernetesMetricsUnavailable,
)


class FakeCustomObjectsApi:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def list_namespaced_custom_object(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_metrics_api_source_reads_and_aggregates_pod_container_usage() -> None:
    api = FakeCustomObjectsApi(
        {
            "items": [
                {
                    "metadata": {"name": "sample-api"},
                    "timestamp": "2026-08-04T08:15:30Z",
                    "window": "30s",
                    "containers": [
                        {
                            "name": "api",
                            "usage": {"cpu": "125000000n", "memory": "64Mi"},
                        },
                        {
                            "name": "sidecar",
                            "usage": {"cpu": "25m", "memory": "32Mi"},
                        },
                    ],
                }
            ]
        }
    )
    source = KubernetesMetricsApiSource(api, request_timeout_seconds=3)

    snapshot = source.snapshot("sample")

    assert snapshot.observed_at == datetime(2026, 8, 4, 8, 15, 30, tzinfo=UTC)
    assert snapshot.pods == (
        KubernetesPodMetrics(
            name="sample-api",
            observed_at=datetime(2026, 8, 4, 8, 15, 30, tzinfo=UTC),
            window_seconds=30.0,
            containers=(
                KubernetesContainerMetrics(
                    name="api",
                    cpu_nano_cores=125_000_000,
                    memory_bytes=64 * 1024 * 1024,
                ),
                KubernetesContainerMetrics(
                    name="sidecar",
                    cpu_nano_cores=25_000_000,
                    memory_bytes=32 * 1024 * 1024,
                ),
            ),
        ),
    )
    assert snapshot.pods[0].cpu_nano_cores == 150_000_000
    assert snapshot.pods[0].memory_bytes == 96 * 1024 * 1024
    assert api.calls == [
        {
            "group": "metrics.k8s.io",
            "version": "v1beta1",
            "namespace": "sample",
            "plural": "pods",
            "_request_timeout": 3,
        }
    ]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, "没有读取权限"),
        (404, "未安装或未注册"),
        (503, "暂时不可用"),
    ],
)
def test_metrics_api_source_translates_optional_api_failures(
    status: int,
    expected: str,
) -> None:
    source = KubernetesMetricsApiSource(
        FakeCustomObjectsApi(ApiException(status=status, reason="failure")),
        request_timeout_seconds=3,
    )

    with pytest.raises(KubernetesMetricsUnavailable, match=expected):
        source.snapshot("sample")


def test_metrics_api_source_rejects_invalid_payload_without_faking_zero_usage() -> None:
    source = KubernetesMetricsApiSource(
        FakeCustomObjectsApi(
            {
                "items": [
                    {
                        "metadata": {"name": "sample-api"},
                        "timestamp": "2026-08-04T08:15:30Z",
                        "window": "30s",
                        "containers": [
                            {
                                "name": "api",
                                "usage": {"cpu": "invalid", "memory": "64Mi"},
                            }
                        ],
                    }
                ]
            }
        ),
        request_timeout_seconds=3,
    )

    with pytest.raises(KubernetesMetricsUnavailable, match="返回格式无效"):
        source.snapshot("sample")


@pytest.mark.parametrize(
    "response",
    [
        {},
        {
            "items": [
                {
                    "metadata": {"name": "sample-api"},
                    "window": "30s",
                    "containers": [
                        {
                            "name": "api",
                            "usage": {"cpu": "25m", "memory": "64Mi"},
                        }
                    ],
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"name": "sample-api"},
                    "timestamp": "2026-08-04T08:15:30Z",
                    "window": "30s",
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"name": "sample-api"},
                    "timestamp": "2026-08-04T08:15:30Z",
                    "window": "30s",
                    "containers": [],
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"name": "sample-api"},
                    "timestamp": "2026-08-04T08:15:30Z",
                    "containers": [
                        {
                            "name": "api",
                            "usage": {"cpu": "25m", "memory": "64Mi"},
                        }
                    ],
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"name": "sample-api"},
                    "timestamp": "2026-08-04T08:15:30Z",
                    "window": "0s",
                    "containers": [
                        {
                            "name": "api",
                            "usage": {"cpu": "25m", "memory": "64Mi"},
                        }
                    ],
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"name": "sample-api"},
                    "timestamp": "2026-08-04T08:15:30Z",
                    "window": "recent",
                    "containers": [
                        {
                            "name": "api",
                            "usage": {"cpu": "25m", "memory": "64Mi"},
                        }
                    ],
                }
            ]
        },
    ],
)
def test_metrics_api_source_rejects_missing_required_observation_fields(
    response: object,
) -> None:
    source = KubernetesMetricsApiSource(
        FakeCustomObjectsApi(response),
        request_timeout_seconds=3,
    )

    with pytest.raises(KubernetesMetricsUnavailable, match="返回格式无效"):
        source.snapshot("sample")


def test_metrics_snapshot_uses_oldest_timestamp() -> None:
    def item(name: str, timestamp: str) -> dict[str, object]:
        return {
            "metadata": {"name": name},
            "timestamp": timestamp,
            "window": "1m500ms",
            "containers": [
                {
                    "name": "app",
                    "usage": {"cpu": "25m", "memory": "64Mi"},
                }
            ],
        }

    source = KubernetesMetricsApiSource(
        FakeCustomObjectsApi(
            {
                "items": [
                    item("newer", "2026-08-04T08:15:30Z"),
                    item("older", "2026-08-04T08:14:45Z"),
                ]
            }
        ),
        request_timeout_seconds=3,
    )

    snapshot = source.snapshot("sample")

    assert snapshot.observed_at == datetime(2026, 8, 4, 8, 14, 45, tzinfo=UTC)
    assert all(pod.window_seconds == 60.5 for pod in snapshot.pods)


def test_empty_metrics_snapshot_does_not_manufacture_an_observation_time() -> None:
    source = KubernetesMetricsApiSource(
        FakeCustomObjectsApi({"items": []}),
        request_timeout_seconds=3,
    )

    snapshot = source.snapshot("sample")

    assert snapshot.observed_at is None
    assert snapshot.pods == ()


def test_cached_metrics_source_reuses_successful_snapshot_until_ttl_expires() -> None:
    class Source:
        def __init__(self) -> None:
            self.calls = 0

        def snapshot(self, namespace: str) -> KubernetesPodMetricsSnapshot:
            self.calls += 1
            return KubernetesPodMetricsSnapshot(
                observed_at=datetime(2026, 8, 4, 8, self.calls, tzinfo=UTC),
                pods=(
                    KubernetesPodMetrics(
                        name=namespace,
                        observed_at=datetime(
                            2026,
                            8,
                            4,
                            8,
                            self.calls,
                            tzinfo=UTC,
                        ),
                        window_seconds=30.0,
                        containers=(
                            KubernetesContainerMetrics(
                                name="app",
                                cpu_nano_cores=self.calls,
                                memory_bytes=self.calls,
                            ),
                        ),
                    ),
                ),
            )

    source = Source()
    now = [100.0]
    cached = CachedKubernetesMetricsSource(
        source,
        cache_ttl_seconds=10.0,
        monotonic_clock=lambda: now[0],
    )

    first = cached.snapshot("sample")
    now[0] = 109.9
    second = cached.snapshot("sample")
    now[0] = 110.0
    third = cached.snapshot("sample")

    assert first is second
    assert third is not first
    assert source.calls == 2
