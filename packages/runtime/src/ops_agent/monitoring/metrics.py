"""SDK-independent contracts for optional current resource metrics."""

from collections.abc import Callable
from time import monotonic
from typing import Protocol

from ops_agent.monitoring.models import KubernetesPodMetricsSnapshot


class KubernetesMetricsUnavailable(Exception):
    """An optional metrics source cannot provide trustworthy current data."""


class KubernetesMetricsSource(Protocol):
    def snapshot(self, namespace: str) -> KubernetesPodMetricsSnapshot: ...


class CachedKubernetesMetricsSource:
    """Keep optional metrics reads bounded independently from resource refreshes."""

    def __init__(
        self,
        source: KubernetesMetricsSource,
        *,
        cache_ttl_seconds: float,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self._source = source
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = monotonic_clock or monotonic
        self._cached: tuple[str, float, KubernetesPodMetricsSnapshot] | None = None

    def snapshot(self, namespace: str) -> KubernetesPodMetricsSnapshot:
        now = self._clock()
        cached = self._cached
        if (
            cached is not None
            and cached[0] == namespace
            and now - cached[1] < self._cache_ttl_seconds
        ):
            return cached[2]
        snapshot = self._source.snapshot(namespace)
        self._cached = (namespace, now, snapshot)
        return snapshot
