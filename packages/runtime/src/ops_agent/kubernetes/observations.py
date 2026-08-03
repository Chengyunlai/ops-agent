from dataclasses import dataclass
from enum import StrEnum


class KubernetesWatchOutcome(StrEnum):
    """A stable runtime result for one bounded Kubernetes Watch request."""

    CHANGED = "changed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"


@dataclass(frozen=True)
class KubernetesWatchResult:
    """The outcome of waiting for a namespace resource change."""

    outcome: KubernetesWatchOutcome
    unavailable_reason: str | None = None
