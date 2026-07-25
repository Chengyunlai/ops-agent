from dataclasses import dataclass
from pathlib import Path

from langchain.chat_models import init_chat_model

from ops_agent.agent import AgentRunner, create_ops_agent
from ops_agent.kubernetes import create_kubernetes_reader
from ops_agent.settings import Settings, load_settings
from ops_agent.tools import create_kubernetes_tools


class BootstrapError(Exception):
    """应用依赖组装失败。"""


class ApplicationError(Exception):
    """Agent 执行失败或返回了无效结果。"""


@dataclass(frozen=True)
class OpsApplication:
    settings: Settings
    agent: AgentRunner

    def ask(self, question: str) -> str:
        if not question.strip():
            raise ApplicationError("问题不能为空")

        try:
            result = self.agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": question,
                        }
                    ]
                }
            )
        except Exception as error:
            raise ApplicationError(f"Agent 执行失败: {error}") from error

        messages = result.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ApplicationError("Agent 未返回消息")

        content = getattr(messages[-1], "content", None)
        if isinstance(content, str) and content:
            return content
        raise ApplicationError("Agent 未返回文本回答")


def create_application(config_path: Path) -> OpsApplication:
    settings = load_settings(config_path)
    reader = create_kubernetes_reader(settings.kubernetes)
    tools = create_kubernetes_tools(
        reader,
        namespace=settings.kubernetes.namespace,
    )

    try:
        model = init_chat_model(
            model=settings.model.name,
            model_provider=settings.model.provider,
            temperature=0,
        )
    except (ImportError, ValueError) as error:
        raise BootstrapError(
            "模型初始化失败，请检查 [model] 配置和对应的 "
            f"LangChain provider 包: {error}"
        ) from error

    agent = create_ops_agent(model, tools)
    return OpsApplication(settings=settings, agent=agent)
