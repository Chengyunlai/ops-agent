from dataclasses import dataclass
from enum import StrEnum

from ops_agent.kubernetes.models import KubernetesResourceKind


class KubernetesWatchOutcome(StrEnum):
    """A stable runtime result for one bounded Kubernetes Watch request."""

    CHANGED = "changed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"


@dataclass(frozen=True)
class KubernetesChangeSignal:
    """A resource invalidation signal without exposing Kubernetes SDK objects."""

    resource_kind: KubernetesResourceKind
    event_type: str
    resource_name: str | None


@dataclass(frozen=True)
class KubernetesWatchResult:
    """The outcome of waiting for a namespace resource change."""

    outcome: KubernetesWatchOutcome
    change: KubernetesChangeSignal | None = None
    unavailable_reason: str | None = None
