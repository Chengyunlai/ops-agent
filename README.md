# ops_agent

面向 Kubernetes 场景的智能运维 Agent。项目目标是把告警、集群状态和运维知识转化为可解释、可审计、可审批的诊断与处置流程，帮助运维人员缩短故障定位和恢复时间。

> [!IMPORTANT]
> 项目目前处于早期开发阶段。当前已打通 Kubernetes TOML 配置、
> 七项 Kubernetes 只读工具、LangGraph 主/子 Agent 和 CLI 自然语言查询链路；
> 指标、告警接入和自动处置等能力仍在规划中。

## 项目目标

- **统一运维入口**：通过自然语言查询集群、工作负载、事件和告警。
- **辅助故障诊断**：汇总上下文，形成带证据的根因假设与处理建议。
- **安全执行变更**：高风险操作必须经过审批，并支持预检、超时和回滚。
- **全程可审计**：记录输入、工具调用、决策依据、执行结果和操作人员。
- **环境相互隔离**：开发、测试和生产环境使用独立配置及最小权限凭据。

## 当前能力

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| TOML 配置加载 | 已完成 | 从指定文件加载 Kubernetes 配置 |
| 配置错误处理 | 已完成 | 识别文件缺失、TOML 格式错误、区块、字段缺失或未知字段 |
| 不可变配置模型 | 已完成 | 使用冻结的 Pydantic 模型声明类型、别名和字段约束 |
| Kubernetes 只读查询 | 已完成 | 查询常用工作负载、网络入口、PVC、Pod 详情/日志和关联 Event |
| Kubernetes 基础诊断 | 已完成 | Agent 可调用确定性规则识别 Pod 阶段/就绪异常和 Deployment 副本不足 |
| 告警接入与诊断 | 规划中 | 接入告警并生成带证据的诊断报告 |
| 审批与处置执行 | 规划中 | 执行扩缩容、重启、回滚等受控动作 |
| LLM / Agent 编排 | 基础已完成 | 受控主图负责范围路由和诊断计划，Kubernetes 子图负责只读诊断 |
| CLI 自然语言入口 | 已完成 | 使用 `ops_agent ask` 查询真实集群状态 |
| 交互式终端 | 基础已完成 | TUI 提供可信 Kubernetes 上下文、多轮会话和受控进度事件 |
| 审计与可观测性 | 规划中 | 结构化日志、Tracing、指标和操作审计 |

## 技术栈

- Python 3.14+
- LangChain / LangGraph
- Kubernetes Python Client
- Pydantic
- Textual
- TOML（Python 标准库 `tomllib`）
- pytest
- uv（推荐的依赖与虚拟环境管理工具）

## 快速开始

### 1. 获取项目

```bash
git clone <repository-url>
cd ops_agent
```

### 2. 安装依赖

推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
```

项目使用 uv workspace 管理本地成员及其依赖，请在仓库根目录通过 uv
安装和运行。

### 3. 创建本地配置

仓库在 `config/examples/` 提供可提交的环境模板。复制需要的模板到
已被 Git 忽略的 `config/local/`，再填写本机 kubeconfig：

```bash
mkdir -p config/local
cp config/examples/test.toml config/local/test.toml
```

本地配置内容如下；不要在其中保存访问令牌、证书等敏感信息：

```toml
[project]
name = "Operations"

[kubernetes]
environment = "local"
namespace = "operations"
kubeconfig_path = "/absolute/path/to/.kube/config"
request_timeout_seconds = 10

[model]
provider = "openai"
model = "deepseek-v4-pro"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

[tui]
theme = "ops-dark"

[tui.colors]
# primary = "#1FB5AD"
# accent = "#FFCC66"
```

这里使用 DeepSeek 官方提供的 OpenAI-compatible 接口，所以
`provider` 填写 LangChain 适配器名称 `openai`。模型密钥只通过环境变量提供：

```bash
export DEEPSEEK_API_KEY="..."
```

`model` 必须支持工具调用。配置文件中不要保存 API Key。

配置项说明：

| 配置项 | 类型 | 说明 |
| --- | --- | --- |
| `project.name` | string | Project Profile 的显示名称 |
| `environment` | string | 环境标识，例如 `dev`、`test`、`prod` |
| `namespace` | string | Agent 默认访问的 Kubernetes 命名空间 |
| `kubeconfig_path` | string | kubeconfig 路径，推荐使用绝对路径 |
| `request_timeout_seconds` | integer | Kubernetes API 请求超时时间（秒） |
| `proxy_url` | HTTP(S) URL | 可选；访问 Kubernetes API 使用的代理，例如 `http://127.0.0.1:7897` |
| `model.provider` | string | LangChain 模型适配器；DeepSeek 兼容接口使用 `openai` |
| `model.model` | string | 供应商提供的、支持工具调用的模型名称 |
| `model.base_url` | string | 可选的模型接口地址 |
| `model.api_key_env` | string | 保存 API Key 的环境变量名称 |
| `tui.theme` | enum | `ops-dark`、`light` 或 `high-contrast` |
| `tui.colors.primary` | `#RRGGBB` | 可选的主题主色覆盖 |
| `tui.colors.accent` | `#RRGGBB` | 可选的主题强调色覆盖 |
| `tui.colors.background` | `#RRGGBB` | 可选的主题背景色覆盖 |
| `tui.colors.foreground` | `#RRGGBB` | 可选的主题文字色覆盖 |
| `tui.colors.warning` | `#RRGGBB` | 可选的主题警告色覆盖 |

当前加载器校验配置文件、TOML 格式、`[kubernetes]` / `[model]`
区块、字段类型、正整数超时、可选代理 URL、主题名称和十六进制颜色；
kubeconfig 是否存在由创建 Kubernetes Client 时检查。`proxy_url` 由配置
模型校验并在 Kubernetes Client 创建前注入，因此不依赖启动 Shell 是否
继承 macOS 系统代理。

当前可通过 Python API 加载配置：

```python
from pathlib import Path

from ops_agent.settings import load_settings

settings = load_settings(Path("config/local/test.toml"))
print(
    settings.kubernetes.environment,
    settings.kubernetes.namespace,
)
```

### 4. 查询真实集群

```bash
uv run ops_agent \
  --config config/local/test.toml \
  ask "检查所有 Pod，指出非 Running 或发生过重启的 Pod"
```

Agent 使用配置中固定的 kubeconfig 和 namespace。模型不能通过工具参数
切换环境或 namespace。

当前 Agent 可以按需调用以下只读工具：

| 工具 | 用途 | 查询边界 |
| --- | --- | --- |
| `diagnose_kubernetes_workloads` | 确定性诊断 Pod 和 Deployment 基础健康状态 | 配置中的 namespace |
| `list_kubernetes_pods` | 查看 Pod 阶段、就绪容器数和重启数 | 配置中的 namespace |
| `get_kubernetes_pod_details` | 查看 Pod 节点、IP、镜像和容器状态 | 指定 Pod |
| `get_kubernetes_pod_logs` | 读取 Pod 最近日志 | 最多 1000 行 |
| `list_kubernetes_events` | 查看 namespace 或指定 Pod 的 Event | 最多 200 条 |
| `list_kubernetes_deployments` | 查看 Deployment 期望与就绪副本数 | 配置中的 namespace |
| `list_kubernetes_services` | 查看 Service 类型、ClusterIP 和端口 | 配置中的 namespace |

这些工具没有接收环境或 namespace 参数，也没有暴露通用 `kubectl` 或
Shell 执行入口。这样可以把模型的活动范围限制在启动时选定的配置内。宽泛的
工作负载健康检查会先调用确定性诊断工具，再按 finding 查询详情、Event 和日志。

### 5. 启动交互式终端

```bash
uv run ops_agent \
  --config config/local/test.toml \
  tui
```

TUI 显示当前环境、固定 namespace 和只读标识。宽终端采用左右布局：左侧每
5 秒刷新资源目录，默认 Overview 明确列出 Pod、Deployment、StatefulSet、
DaemonSet、Service、ReplicaSet、Job、CronJob、Ingress 和 PVC 的数量与
状态；右侧保留本次运行中的用户消息和 Agent Markdown 回复。窄终端自动改为
上下布局。每类资源独立查询，单类 API 或 RBAC 失败会在该行显示
`Unavailable`，不会让 Pod、Service 等其他目录一起消失。

`Ctrl+K` 聚焦左侧监盘，`0` 返回 Overview，`1`～`6` 切换具体资源；
Overview 中可用方向键和 `Enter` 进入任意资源类型。选中资源后按
`Enter`/`d` 读取对象详情与关联 Event；Pod 上按 `l` 读取每个容器最近
200 行日志。TUI 启用鼠标以支持点击聚焦；复制终端内容时按住
`Shift`/`Option` 再拖选，不局限于整块内容。

顶部 `Settings`（或 `Ctrl+,`）编辑当前 Project Profile、主题和颜色。主题
选择与有效颜色会即时预览；保存后写回启动时使用的 TOML。项目名称、环境、
namespace、kubeconfig、代理和请求超时属于运行边界，保存后需要重启应用才会
生效；主题与颜色无需重启。内置 `ops-dark`、`light`、`high-contrast`
三个主题，也可恢复预设默认颜色。

这些固定操作直接使用绑定 namespace 的 Monitor/Reader，不经过 Agent，也不
读取或改变右侧 Conversation Session。`i` 返回聊天输入，`Ctrl+R` 立即刷新
监盘，`Ctrl+L` 清空右侧显示但保留会话上下文，`F1`/`?` 显示帮助，
`Ctrl+C` 在任意焦点下退出。

右侧虽然采用普通聊天交互，但仍将可信 `InteractionContext` 传给主图并消费
受控 `ConversationSession`，不是绕过 Policy 的裸模型入口。因此可以使用
“现在几个服务”这类上下文简称，无需反复声明 Kubernetes 和 namespace；
会话历史会传给 Planner 和专业 Agent，澄清确认与指代式追问不会在执行阶段
丢失上下文。原有 `ops_agent ask` 非交互入口保持不变，并继续采用保守的
自动 scope。

### 6. 开发命令

仓库根目录的 Makefile 统一封装 uv、Ruff 和 pytest：

```bash
make help
make sync
make format
make check
make test-cli
make test-harness
make tui CONFIG=config/local/test.toml
```

`make format` 自动修复 Ruff 问题并格式化代码，`make check` 运行静态检查、
格式检查和全量测试。

也可以直接运行测试：

```bash
uv run pytest
```

## 项目结构

```text
ops_agent/
├── CONTEXT.md
├── Makefile
├── config/
│   ├── examples/                  # 可提交的配置模板
│   │   ├── dev.toml
│   │   ├── prod.toml
│   │   └── test.toml
│   └── local/                     # 本机配置，Git 忽略
│       └── test.toml
├── apps/
│   └── cli/
│       ├── src/
│       │   └── ops_agent_cli/
│       │       ├── __init__.py
│       │       ├── __main__.py
│       │       ├── bootstrap.py
│       │       ├── main.py
│       │       └── tui/
│       │           ├── __init__.py
│       │           ├── app.py
│       │           ├── chat.py
│       │           └── monitor.py
│       ├── tests/
│       │   └── unit/
│       │       ├── test_bootstrap.py
│       │       ├── test_main.py
│       │       └── test_tui.py
│       └── pyproject.toml
├── docs/
│   └── research/
│       └── terminal-tui-options.md
├── packages/
│   └── harness/
│       ├── src/
│       │   └── ops_agent/
│       │       ├── __init__.py
│       │       ├── agent/
│       │       │   ├── __init__.py
│       │       │   ├── application.py
│       │       │   ├── models.py
│       │       │   ├── orchestration/
│       │       │   │   ├── __init__.py
│       │       │   │   ├── graph.py
│       │       │   │   └── routing.py
│       │       │   └── specialists/
│       │       │       ├── __init__.py
│       │       │       └── kubernetes/
│       │       │           ├── __init__.py
│       │       │           ├── agent.py
│       │       │           └── planning.py
│       │       ├── diagnostics/
│       │       │   ├── __init__.py
│       │       │   ├── kubernetes.py
│       │       │   └── models.py
│       │       ├── kubernetes/
│       │       │   ├── __init__.py
│       │       │   ├── models.py
│       │       │   └── reader.py
│       │       ├── monitoring/
│       │       │   ├── __init__.py
│       │       │   └── kubernetes.py
│       │       ├── settings/
│       │       │   ├── __init__.py
│       │       │   ├── loader.py
│       │       │   └── models.py
│       │       └── tools/
│       │           ├── __init__.py
│       │           └── kubernetes.py
│       ├── tests/
│       │   └── unit/
│       │       ├── test_agent.py
│       │       ├── test_graph.py
│       │       ├── test_kubernetes.py
│       │       ├── test_kubernetes_diagnostics.py
│       │       ├── test_kubernetes_resources.py
│       │       ├── test_settings.py
│       │       └── test_tools.py
│       └── pyproject.toml
├── pyproject.toml
└── README.md
```

根项目使用 uv workspace 管理两个成员：`ops_agent_cli` 是命令行应用，
`ops_agent_harness` 是可复用的 Agent 核心包。依赖方向固定为 CLI 指向
harness，harness 不依赖任何具体入口。

Kubernetes 相关代码采用三层边界：

- `kubernetes/` 是基础设施能力层。`reader.py` 封装 Kubernetes SDK，
  `models.py` 定义不依赖 SDK 的结构化查询结果。
- `diagnostics/` 是确定性诊断层。它消费查询结果，输出带证据的结构化
  finding，不访问集群，也不依赖 LLM 或 LangChain。
- `tools/` 是 Agent 适配层。它把 Kubernetes 能力转换成模型可调用的
  LangChain Tool，并在这里固定 namespace、限制日志行数和 Event 数量。
- `monitoring/` 用无参数 `snapshot()` 以及固定 namespace 的 `describe()` /
  `pod_logs()` 接口封装资源浏览能力。TUI 只消费这些稳定接口，不导入
  Kubernetes SDK，也不解析 Agent 文本；以后将轮询替换为 watch 时无需改动
  聊天与布局。

`apps/cli/bootstrap.py` 是终端应用的组合根，负责读取进程环境并选择具体的
模型、Reader、Monitor、Tool 和 Agent 适配器。`main.py` 保留脚本化命令，
`tui/` 只管理 Textual 的界面状态、键盘交互和后台任务。TUI 打开带固定
`InteractionContext` 的 `ConversationSession`，并消费稳定的 `AgentEvent`，
不依赖 LangGraph 节点名。以后增加 API 或其他应用入口时，各应用拥有自己的
组合根，harness 不依赖任何具体入口。

`agent/` 是 Agent 编排模块。`models.py` 定义应用可以依赖的冻结 Pydantic
契约：可信 Interaction Context、不可信 Intent Proposal、可信 Policy
Decision、注册 Capability 和稳定 Agent Event。`application.py` 通过
`OpsAgent` 与 `ConversationSession` 隐藏 LangGraph 调用、历史消息、事件映射、
响应解析和错误转换；CLI 不依赖模块内部的图、路由、计划或专业 Agent 实现。

`agent/orchestration/` 负责跨专业 Agent 的主图编排和全局策略。
`graph.py` 定义 State、Node 与 Edge；`routing.py` 让模型只提出结构化
Intent Proposal，再由纯代码 Policy 校验应用 scope、已注册只读 Capability、
未接入平台和写操作。模型不能授予能力或改变 environment/namespace。
Intent Interpreter 对模型只发起一次调用，同时兼容标准 tool call 和
OpenAI-compatible Provider 常见的 JSON 文本响应；两种响应都必须通过同一个
Pydantic `IntentProposal` 校验，避免结构化输出不兼容时进入 Agent 重试循环。
原始文本中明确出现 Service、Pod 等资源时，代码还会用它锁定资源
Capability；模型结构化输出中的 resource 不能把 Service 偷换成 Pod。
Capability Registry 由组合根实际传入的 Kubernetes 工具派生，而不是根据
Prompt 声称的能力生成；没有完整对应工具的资源查询不会进入专业 Agent。
直接执行时，主图只向子 Agent 暴露 Policy Decision 选中的 Capability 所绑定
的工具，其他已注册工具也不可见，因此 Service 回答不能用 Pod 证据冒充；
只有原始请求本身也包含根因、分析或失败等复杂诊断语义时，才允许进入
多阶段计划，并且必须获得包含全部所需只读工具的完整诊断 Capability。
自动 scope 下，没有明确 Kubernetes 语义的普通请求仍会拒绝；“服务”等有合理
多义性的表达会先澄清。TUI 的 Kubernetes scope 来自应用配置，因此可以安全
理解上下文简称；代码仍要求诊断/查询语义，并阻止用户文本切换固定的
environment 或 namespace。真正的硬约束仍是子图只持有固定 namespace 的
只读工具，并且没有与所选 Capability 匹配的成功工具证据就不输出实时结论。
Interaction Context 保持在类型化图状态和 Policy 中，不会被拼接成专业 Agent
收到的普通用户请求。

`agent/specialists/` 按专业能力隔离子 Agent，避免其工具、提示词和专属执行
协议散落在主图中。当前 `specialists/kubernetes/agent.py` 隐藏 Kubernetes
ReAct 子图、工具选择、响应解析和证据提取；`planning.py` 声明最多三步的顺序
Kubernetes 只读诊断计划。步骤只能从工作负载健康、补充证据和根因分析三个
受控目标中选择，不能携带自由文本工具指令；计划必须从工作负载健康开始，并
按取证到根因的顺序推进。每一步必须产生成功的真实工具消息，否则主图不会
输出实时诊断结论。

```text
CLI / TUI ──► Interaction Context（可信）
                    │
                    ▼
              Intent Proposal（不可信）
                    │
                    ▼
              Policy Decision（代码）
           ┌────────┼───────────┐
           │        │           │
         澄清      拒绝       已注册 Capability
                                ┌┴─────────┐
                              直接执行   诊断计划
                                └────┬─────┘
                                     ▼
                             Kubernetes 专业子图
                                     │
                                     ▼
                              工具证据校验与回答
```

新增指标或日志等第二个真实专业 Agent 时，在 `agent/specialists/` 增加独立
子目录，在 `agent/orchestration/graph.py` 扩展拓扑，并根据真实的共同点再
提取注册 seam。这样专业能力内部变化不会直接扰动全局编排，新增入口也继续
只依赖 `ops_agent.agent` 的稳定公开接口。审批、持久化和 interrupt/resume
属于有状态执行协议，不复用当前的只读诊断计划。

`settings/` 通过 `load_settings()` / `save_settings()` 提供 TOML 配置
边界：`loader.py` 负责文件读取、原子写回和统一错误转换，`models.py` 使用
Pydantic 模型声明配置结构、字段说明、不可变性和校验规则。

## 建议架构

后续功能建议按以下边界演进，避免让模型直接持有不受约束的集群权限：

```text
告警 / 用户请求
       │
       ▼
Agent 编排层 ──► 上下文与运维知识
       │
       ▼
策略与审批层 ──► 权限校验 / 风险分级 / 人工审批
       │
       ▼
运维工具层 ───► Kubernetes / 日志 / 指标 / 发布系统
       │
       ▼
结果验证与审计
```

推荐优先实现只读诊断闭环，再逐步开放可写操作：

1. 连接 Kubernetes，并提供资源、事件和日志的只读查询。
2. 接入 Prometheus、日志平台或告警系统，统一诊断上下文。
3. 输出引用原始证据的诊断结论，不把模型推断当作事实。
4. 引入操作白名单、风险分级、人工审批和 dry-run。
5. 增加变更后验证、失败回滚、审计日志和可观测性。

## 安全原则

运维 Agent 会接触生产基础设施，开发和部署时应至少遵循以下原则：

- 使用 Kubernetes RBAC 和独立 ServiceAccount，默认只读、最小权限。
- 不在代码、配置文件、日志或模型上下文中保存 Token、证书等密钥。
- 生产环境的写操作默认关闭；开启后必须经过策略校验和明确审批。
- 对删除、驱逐、扩缩容、重启和回滚等操作设置资源范围与速率限制。
- 执行前展示目标、影响范围和操作计划，执行后验证系统是否恢复。
- 为每次模型决策和工具调用生成可检索的审计记录。
- 对外部输入进行校验，防止提示词注入影响工具调用和权限边界。

## 开发约定

- 新功能应包含相应的单元测试或集成测试。
- 新增配置校验应统一抛出 `SettingsError`，并提供可定位问题的错误信息。
- 不提交真实 kubeconfig、访问令牌、生产地址或其他敏感数据。
- 提交前运行：

```bash
uv run pytest
```

## 路线图

- [x] Kubernetes 配置模型
- [x] 配置加载与基础校验
- [x] Kubernetes 客户端与 Pod 只读查询
- [x] Pod 详情、日志、Event、Deployment 与 Service 只读工具
- [x] Pod 和 Deployment 基础症状诊断及 Agent 工具链路
- [x] 请求范围路由、默认拒绝与实时证据校验
- [x] 最小 Kubernetes 只读诊断计划
- [ ] 发布失败根因和资源压力诊断
- [ ] Prometheus、日志与告警平台接入
- [x] LLM 工具调用与基础 Agent 编排
- [x] CLI 自然语言查询入口
- [ ] Agent 会话状态与持久化
- [ ] 风险策略、人工审批和 dry-run
- [ ] 自动处置、结果验证与回滚
- [ ] 审计日志、Tracing 和运行指标
- [ ] CLI / API 服务与容器化部署

## 贡献

欢迎通过 Issue 讨论需求或缺陷，并通过 Pull Request 提交改进。提交前请确保测试通过，且新增行为有相应测试覆盖。

## 许可证

本项目暂未声明开源许可证。在许可证明确之前，请勿将代码用于未获授权的分发或商业用途。
