import os
from dataclasses import dataclass
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from ops_agent.agent import OpsAgent, create_ops_agent
from ops_agent.kubernetes import create_kubernetes_reader
from ops_agent.monitoring import KubernetesMonitor
from ops_agent.settings import ModelSettings, Settings, load_settings
from ops_agent.tools import create_kubernetes_tools


class BootstrapError(Exception):
    """CLI 应用依赖组装失败。"""


@dataclass(frozen=True)
class ApplicationRuntime:
    agent: OpsAgent
    monitor: KubernetesMonitor
    settings: Settings
    environment: str
    namespace: str


def create_runtime(config_path: Path) -> ApplicationRuntime:
    settings = load_settings(config_path)
    model = _create_model(settings.model)
    reader = create_kubernetes_reader(settings.kubernetes)
    tools = create_kubernetes_tools(
        reader,
        namespace=settings.kubernetes.namespace,
    )
    return ApplicationRuntime(
        agent=create_ops_agent(model, tools),
        monitor=KubernetesMonitor(
            reader,
            namespace=settings.kubernetes.namespace,
        ),
        settings=settings,
        environment=settings.kubernetes.environment,
        namespace=settings.kubernetes.namespace,
    )


def create_application(config_path: Path) -> OpsAgent:
    return create_runtime(config_path).agent


def _create_model(settings: ModelSettings) -> BaseChatModel:
    model_kwargs: dict[str, object] = {
        "model": settings.name,
        "model_provider": settings.provider,
        "temperature": 0,
    }
    if settings.base_url is not None:
        model_kwargs["base_url"] = settings.base_url
    if settings.api_key_env is not None:
        api_key = os.getenv(settings.api_key_env)
        if not api_key:
            raise BootstrapError(f"缺少模型密钥环境变量: {settings.api_key_env}")
        model_kwargs["api_key"] = api_key

    try:
        return init_chat_model(**model_kwargs)
    except (ImportError, ValueError) as error:
        raise BootstrapError(
            "模型初始化失败，请检查 [model] 配置和对应的 "
            f"LangChain provider 包: {error}"
        ) from error
