
import argparse
from pathlib import Path
import sys

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
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
    except SettingsError as error:
        print(f"启动失败: {error}", file=sys.stderr)
        return 1
    print(f"启动成功，Kubernetes 环境: {settings.environment}, 命名空间: {settings.namespace}")
    return 0
