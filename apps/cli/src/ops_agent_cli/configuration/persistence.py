import os
import tempfile
import tomllib
from pathlib import Path

import tomli_w
from pydantic import ValidationError

from ops_agent_cli.configuration.models import Settings


class SettingsError(Exception):
    """应用配置无效。"""


def load_settings(config_path: Path) -> Settings:
    data = _read_toml(config_path)
    try:
        return Settings.model_validate(data)
    except ValidationError as error:
        validation_message = _format_validation_error(error)
    raise SettingsError(validation_message)


def save_settings(config_path: Path, settings: Settings) -> None:
    data = settings.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            tomli_w.dump(data, temporary_file)
        os.replace(temporary_path, config_path)
    except (OSError, TypeError) as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise SettingsError(f"配置文件无法写入: {config_path}") from error


def _read_toml(config_path: Path) -> dict[str, object]:
    try:
        with config_path.open("rb") as config_file:
            return tomllib.load(config_file)
    except FileNotFoundError as error:
        raise SettingsError(f"配置文件不存在: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"配置文件格式错误: {config_path}") from error
    except OSError as error:
        raise SettingsError(f"配置文件无法读取: {config_path}") from error


def _format_validation_error(error: ValidationError) -> str:
    messages = [
        _format_validation_issue(issue)
        for issue in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    ]
    return "配置校验失败: " + "; ".join(messages)


def _format_validation_issue(issue: dict[str, object]) -> str:
    location = tuple(str(part) for part in issue["loc"])
    issue_type = str(issue["type"])
    section = location[0] if location else "settings"

    if len(location) == 1:
        if issue_type == "missing":
            return f"缺少 [{section}] 配置区块"
        if issue_type == "extra_forbidden":
            return f"未知配置区块: [{section}]"
        return f"[{section}] 配置区块无效"

    field_name = ".".join(location[1:])
    if issue_type == "missing":
        return f"[{section}] 缺少必填配置项: {field_name}"

    issue_message = {
        "extra_forbidden": "未知配置项",
        "greater_than": "配置项必须是正整数",
        "int_type": "配置项必须是正整数",
        "string_too_short": "配置项必须是非空字符串",
        "string_type": "配置项必须是字符串",
        "url_parsing": "配置项必须是有效的 HTTP(S) URL",
        "url_scheme": "配置项必须是 HTTP(S) URL",
        "url_type": "配置项必须是 HTTP(S) URL 字符串",
    }.get(issue_type, "配置项无效")
    return f"[{section}] {issue_message}: {field_name}"
