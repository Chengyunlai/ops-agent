# ops_agent

面向 Kubernetes 场景的智能运维 Agent。项目目标是把告警、集群状态和运维知识转化为可解释、可审计、可审批的诊断与处置流程，帮助运维人员缩短故障定位和恢复时间。

> [!IMPORTANT]
> 项目目前处于早期开发阶段。当前已打通 Kubernetes TOML 配置、
> Pod 只读工具、LangGraph Agent 和 CLI 自然语言查询链路；
> 日志、事件、告警接入和自动处置等能力仍在规划中。

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
| 配置错误处理 | 已完成 | 识别文件缺失、TOML 格式错误、区块或字段缺失 |
| 不可变配置模型 | 已完成 | 使用冻结的 `KubernetesSettings` 数据类 |
| Kubernetes Pod 只读查询 | 已完成 | 隔离加载 kubeconfig，按 namespace 查询并转换 Pod 摘要 |
| Kubernetes 其他只读查询 | 规划中 | 查询 Deployment、Event、日志和资源指标 |
| 告警接入与诊断 | 规划中 | 接入告警并生成带证据的诊断报告 |
| 审批与处置执行 | 规划中 | 执行扩缩容、重启、回滚等受控动作 |
| LLM / Agent 编排 | 基础已完成 | 使用 LangChain `create_agent` 构建 LangGraph 工具调用循环 |
| CLI 自然语言入口 | 已完成 | 使用 `ops_agent ask` 查询真实集群状态 |
| 审计与可观测性 | 规划中 | 结构化日志、Tracing、指标和操作审计 |

## 技术栈

- Python 3.14+
- LangChain / LangGraph
- Kubernetes Python Client
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
| `environment` | string | 环境标识，例如 `dev`、`test`、`prod` |
| `namespace` | string | Agent 默认访问的 Kubernetes 命名空间 |
| `kubeconfig_path` | string | kubeconfig 路径，推荐使用绝对路径 |
| `request_timeout_seconds` | integer | Kubernetes API 请求超时时间（秒） |
| `model.provider` | string | LangChain 模型适配器；DeepSeek 兼容接口使用 `openai` |
| `model.model` | string | 供应商提供的、支持工具调用的模型名称 |
| `model.base_url` | string | 可选的模型接口地址 |
| `model.api_key_env` | string | 保存 API Key 的环境变量名称 |

当前加载器校验配置文件、TOML 格式、`[kubernetes]` / `[model]`
区块及模型可选字符串；尚未完整校验 Kubernetes 字段类型、路径有效性或
超时取值范围。

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

### 5. 运行测试

```bash
uv run pytest
```

## 项目结构

```text
ops_agent/
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
│       │       └── main.py
│       ├── tests/
│       │   └── unit/
│       │       └── test_main.py
│       └── pyproject.toml
├── packages/
│   └── harness/
│       ├── src/
│       │   └── ops_agent/
│       │       ├── __init__.py
│       │       ├── agent.py
│       │       ├── bootstrap.py
│       │       ├── kubernetes.py
│       │       ├── settings.py
│       │       └── tools.py
│       ├── tests/
│       │   └── unit/
│       │       ├── test_bootstrap.py
│       │       ├── test_kubernetes.py
│       │       ├── test_settings.py
│       │       └── test_tools.py
│       └── pyproject.toml
├── pyproject.toml
└── README.md
```

根项目使用 uv workspace 管理两个成员：`ops_agent_cli` 是命令行应用，
`ops_agent_harness` 是可复用的 Agent 核心包。依赖方向固定为 CLI 指向
harness，harness 不依赖任何具体入口。

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
- [ ] Pod 日志、Event 与其他只读工具
- [ ] Pod 异常、发布失败和资源压力诊断
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
