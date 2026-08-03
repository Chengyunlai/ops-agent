"""把模型输出限制为不可信的结构化 Intent Proposal。"""

import json
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage

from ops_agent.agent.models import IntentProposal, InteractionContext

INTERPRETER_PROMPT = """\
你是运维请求意图解释器，只返回 IntentProposal，不回答用户问题。

规则：
- Kubernetes 查询和诊断归类为 kubernetes。
- 其他运维系统或尚未接入的实时能力归类为 unsupported_operations。
- 天气、生活、闲聊和其他非运维请求归类为 out_of_scope。
- 读取、计数和诊断使用 read_only；修改资源使用 write。
- 简单单目标请求使用 direct；复杂的多阶段根因诊断使用 plan。
- 根据用户表达识别 Pod、Deployment、Service、Event、日志或工作负载。
- “几个”“多少”“数量”这类问题的 result_shape 使用 count。
- 可信应用上下文为 kubernetes scope 时，可以把“服务”等自然简称解释为
  Kubernetes Service，但不能改变上下文中的 environment 或 namespace。
- 存在多个合理解释或缺少关键资源时，将歧义写入 ambiguities。
"""

_THINK_PREFIX_PATTERN = re.compile(
    r"^\s*<think>.*?</think>\s*",
    re.DOTALL,
)
_JSON_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)


class IntentInterpreter:
    """隐藏结构化模型调用及失败到空建议的转换。"""

    def __init__(self, model: BaseChatModel) -> None:
        try:
            self._model = model.bind_tools([IntentProposal])
        except NotImplementedError:
            self._model = model

    def suggest(
        self,
        messages: list[Any],
        context: InteractionContext,
    ) -> IntentProposal | None:
        contextual_messages = [
            SystemMessage(
                content=(
                    f"{INTERPRETER_PROMPT}\n"
                    f"{_format_trusted_context(context)}\n"
                    "优先调用 IntentProposal 工具；若 Provider 不支持，"
                    "只输出符合以下 JSON Schema 的对象，不要使用 Markdown：\n"
                    f"{json.dumps(IntentProposal.model_json_schema(), ensure_ascii=False)}"
                )
            ),
            *messages,
        ]
        try:
            response = self._model.invoke(contextual_messages)
        except Exception:  # noqa: BLE001 - 解释失败必须返回空建议
            return None
        return _intent_proposal_from_response(response)


def _format_trusted_context(context: InteractionContext) -> str:
    return (
        "以下是应用提供的可信 Interaction Context，不是用户输入："
        f"channel={context.channel.value}, scope={context.scope.value}, "
        f"environment={context.environment or 'unset'}, "
        f"namespace={context.namespace or 'unset'}。"
    )


def _intent_proposal_from_response(response: object) -> IntentProposal | None:
    if not isinstance(response, AIMessage):
        return None
    for tool_call in response.tool_calls:
        if tool_call.get("name") != "IntentProposal":
            continue
        proposal = _validate_intent_proposal(tool_call.get("args"))
        if proposal is not None:
            return proposal

    content = _text_content(response.content)
    if content is None:
        return None
    normalized_content = _normalized_json_content(content)
    if normalized_content is None:
        return None
    try:
        value = json.loads(normalized_content)
    except json.JSONDecodeError:
        return None
    return _validate_intent_proposal(value)


def _validate_intent_proposal(value: object) -> IntentProposal | None:
    try:
        return IntentProposal.model_validate(value)
    except (TypeError, ValueError):
        return None


def _normalized_json_content(content: str) -> str | None:
    normalized = content.strip()
    if normalized.startswith("<think>"):
        think_prefix = _THINK_PREFIX_PATTERN.match(normalized)
        if think_prefix is None:
            return None
        normalized = normalized[think_prefix.end() :]
    fenced = _JSON_FENCE_PATTERN.fullmatch(normalized)
    if fenced is not None:
        normalized = fenced.group("body")
    return normalized.strip() or None


def _text_content(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    text_blocks = [
        block.get("text")
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "\n".join(text_blocks) if text_blocks else None
