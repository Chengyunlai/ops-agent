import argparse
import sys
from pathlib import Path

from ops_agent.agent import ApplicationError
from ops_agent.kubernetes import KubernetesError
from ops_agent.settings import SettingsError, load_settings

from ops_agent_cli import __version__
from ops_agent_cli.bootstrap import BootstrapError, create_application
from ops_agent_cli.installation import (
    InstallationError,
    diagnose_installation,
    initialize_config,
    resolve_config_path,
)
from ops_agent_cli.tui import run_tui


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops-agent",
        description="本地 Kubernetes 运维 Agent",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="TOML 配置文件路径",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
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
    subparsers.add_parser(
        "tui",
        help="启动交互式终端界面",
    )
    subparsers.add_parser(
        "init",
        help="创建安装后的初始配置文件",
    )
    subparsers.add_parser(
        "doctor",
        help="检查配置、凭据和 Kubernetes 连接",
    )
    config_parser = subparsers.add_parser(
        "config",
        help="查看安装配置",
    )
    config_commands = config_parser.add_subparsers(
        dest="config_command",
        required=True,
    )
    config_commands.add_parser(
        "path",
        help="显示实际使用的配置文件路径",
    )
    args = parser.parse_args(argv)
    config_path = resolve_config_path(args.config)

    try:
        if args.command == "config" and args.config_command == "path":
            print(config_path)
            return 0
        if args.command == "init":
            created_path = initialize_config(config_path)
            print(f"配置文件已创建: {created_path}")
            print("请填写 Kubernetes 目标和模型设置后运行 ops-agent doctor。")
            return 0
        if args.command == "doctor":
            report = diagnose_installation(config_path)
            for check in report.checks:
                print(f"{check.status.value:<5} {check.name}: {check.detail}")
            print("诊断通过" if report.passed else "诊断未通过")
            return 0 if report.passed else 1
        if args.command == "ask":
            application = create_application(config_path)
            print(application.ask(args.question))
            return 0
        if args.command == "tui":
            run_tui(config_path)
            return 0

        settings = load_settings(config_path)
    except (
        SettingsError,
        KubernetesError,
        BootstrapError,
        ApplicationError,
        InstallationError,
    ) as error:
        print(f"启动失败: {error}", file=sys.stderr)
        return 1

    print(
        "启动成功，Kubernetes 环境: "
        f"{settings.kubernetes.environment}, "
        f"命名空间: {settings.kubernetes.namespace}"
    )
    return 0
