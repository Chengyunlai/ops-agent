from pathlib import Path

from ops_agent.agent import (
    CapabilityScope,
    InteractionChannel,
    InteractionContext,
)

from ops_agent_cli.bootstrap import create_runtime
from ops_agent_cli.tui.app import OpsAgentTui


def run_tui(config_path: Path) -> None:
    runtime = create_runtime(config_path)
    session = runtime.agent.open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment=runtime.environment,
            namespace=runtime.namespace,
        )
    )
    OpsAgentTui(
        conversation=session,
        monitor=runtime.monitor,
        environment=runtime.environment,
        namespace=runtime.namespace,
    ).run(mouse=True)


__all__ = ["OpsAgentTui", "run_tui"]
