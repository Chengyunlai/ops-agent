from pathlib import Path

from ops_agent_cli.bootstrap import create_runtime
from ops_agent_cli.tui.app import OpsAgentTui


def run_tui(config_path: Path) -> None:
    runtime = create_runtime(config_path)
    OpsAgentTui(
        agent=runtime.agent,
        environment=runtime.environment,
        namespace=runtime.namespace,
    ).run()


__all__ = ["OpsAgentTui", "run_tui"]
