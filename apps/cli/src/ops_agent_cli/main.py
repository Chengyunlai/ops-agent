
import argparse
from pathlib import Path
import sys

from ops_agent.bootstrap import (
    ApplicationError,
    BootstrapError,
    create_application,
)
from ops_agent.kubernetes import KubernetesError
from ops_agent.settings import SettingsError, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="本地 Kubernetes 运维 Agent",
        )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/test.toml"),
        help="TOML 配置文件路径",
        )
    subparsers = parser.add_subparsers(dest="command")
    ask_parser = subparsers.add_parser(
        "ask",
        help="向运维 Agent 提问",
    )
    ask_parser.add_argument(
        "question",
        help="需要 Agent 分析的运维问题",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "ask":
            application = create_application(args.config)
            print(application.ask(args.question))
            return 0

        settings = load_settings(args.config)
    except (
        SettingsError,
        KubernetesError,
        BootstrapError,
        ApplicationError,
    ) as error:
        print(f"启动失败: {error}", file=sys.stderr)
        return 1

    print(
        "启动成功，Kubernetes 环境: "
        f"{settings.kubernetes.environment}, "
        f"命名空间: {settings.kubernetes.namespace}"
    )
    return 0
