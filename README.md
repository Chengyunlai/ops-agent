# Ops Agent

[![Release](https://img.shields.io/github/v/release/Chengyunlai/ops-agent)](https://github.com/Chengyunlai/ops-agent/releases/latest)
[![CI](https://github.com/Chengyunlai/ops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Chengyunlai/ops-agent/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Chengyunlai/ops-agent)](LICENSE)

本地运行的 Kubernetes 终端工作台：左侧实时监盘与人工操作，右侧基于真实集群证据进行只读诊断对话。

![Ops Agent 资源总览与受控对话](docs/images/tui-overview.svg)

Ops Agent 面向需要直接观察工作负载、排查日志和存储问题，同时保留人工控制权的运维人员。它不会向目标集群安装 Operator、CRD、DaemonSet、Exporter、Prometheus 或 metrics-server。

> [!IMPORTANT]
> 项目仍处于早期阶段。当前重点是只读监控与诊断；告警接入、审批、变更执行和审计仍在规划中。

## 核心能力

- **浏览 Kubernetes 资源**：查看固定 namespace 内的工作负载、Service、Ingress、PVC、Event 与可选 Metrics API 指标。
- **排查 Pod 日志**：按容器、行数或时间范围读取日志，支持长行显示、Follow、搜索和操作员维护的噪声隐藏规则。
- **解释确定性证据**：将 Pod、Deployment、Service、Endpoint、Event 与 previous logs 交给受控 Agent 解释。
- **浏览与下载文件**：查看 PVC/PV/StorageClass/挂载关系，通过已有 Pod 浏览目录并下载普通文件。
- **进入 Pod Shell**：经人工确认进入选定容器；默认关闭，不注册为 AI 工具。
- **隔离项目配置**：使用 TOML 固定 environment、namespace、kubeconfig、模型、主题和人工操作策略。

## 安装

macOS 与 Linux 优先使用 Homebrew。安装包已包含 Python 运行时：

```bash
brew tap Chengyunlai/tap
brew install ops-agent
ops-agent --version
```

也可以从 [GitHub Releases](https://github.com/Chengyunlai/ops-agent/releases) 下载 macOS arm64、macOS amd64 或 Linux amd64 压缩包。每个 Release 都包含 `SHA256SUMS` 和 GitHub build provenance。

## 5 分钟快速开始

创建默认 Project Profile：

```bash
ops-agent init
ops-agent config path
```

编辑生成的 TOML，确认 kubeconfig、environment 与 namespace 指向预期目标。模型密钥只通过环境变量提供：

```bash
export OPENAI_API_KEY="..."
ops-agent doctor
ops-agent tui
```

`doctor` 检查配置、kubeconfig、模型密钥环境变量、Pod 读取权限和 `kubectl`。缺少 `kubectl` 只影响 Pod Shell 与文件传输，不影响只读资源监盘。

脚本化查询可以直接使用：

```bash
ops-agent ask "检查所有 Pod，指出异常和最近重启"
```

完整安装与配置说明见 [公开文档站](https://chengyunlai.github.io/ops-agent/)。

## 权限与安全边界

| 能力 | 执行者 | 权限边界 |
| --- | --- | --- |
| 资源、日志与 Event 查询 | Agent / 操作员 | 固定 kubeconfig 与 namespace，只读工具 |
| Metrics API | 操作员启用 | 只读取集群已经存在的 `metrics.k8s.io`；失败时局部降级 |
| 日志聚焦与搜索 | 操作员 | 本地处理；AI 不能生成或应用隐藏规则 |
| Pod/PVC 文件下载 | 操作员 | 写入受限本机目录，不新增 Kubernetes 写权限 |
| Pod Shell | 操作员 | 默认关闭，显式确认，不经过 Agent 图 |

模型只能提出结构化意图，不能授予自己能力、切换环境或 namespace。Policy 使用实际注册的 Capability 做代码校验；没有成功工具证据时，系统不会输出实时集群结论。

安全问题请使用 [GitHub 私密漏洞报告](SECURITY.md)，不要在公开 Issue 中提交凭据、生产数据或可用漏洞细节。

## 界面

### Pods 实时监盘

![Ops Agent Pods 实时监盘](docs/images/tui-pods.svg)

### Pod 日志工作台

![Ops Agent Pod 日志工作台](docs/images/tui-logs.svg)

### 项目与界面设置

![Ops Agent 项目与界面设置](docs/images/tui-settings.svg)

常用快捷键：

| 按键 | 操作 |
| --- | --- |
| `Ctrl+K` | 聚焦左侧监盘 |
| `0`，`1`～`7` | 返回总览或切换资源 |
| `Enter` / `h` | 查看 Finding 与 Evidence |
| `d` | 查看详情与 Event |
| `l` | 打开 Pod 日志工作台 |
| `f` | 浏览 PVC；日志页切换 Follow |
| `x` | 进入人工 Pod Shell |
| `i` | 聚焦对话输入 |
| `?` | 打开帮助 |
| `Esc` / `q` | 退出或关闭当前视图 |

完整资源、日志、PVC 和 Pod Shell 操作见 [TUI 使用指南](https://chengyunlai.github.io/ops-agent/docs/tui/)。

## 文档

- [快速开始](https://chengyunlai.github.io/ops-agent/docs/getting-started/)
- [配置参考](https://chengyunlai.github.io/ops-agent/docs/configuration/)
- [TUI 使用指南](https://chengyunlai.github.io/ops-agent/docs/tui/)
- [架构与安全边界](https://chengyunlai.github.io/ops-agent/docs/architecture/)
- [源码开发](https://chengyunlai.github.io/ops-agent/docs/development/)
- [发布流程](https://chengyunlai.github.io/ops-agent/docs/releasing/)
- [架构决策记录](docs/adr/)
- [贡献指南](CONTRIBUTING.md)

文档站基于 [AstroPaper](https://github.com/satnaing/astro-paper)，主题许可声明见 [website/THIRD_PARTY_NOTICES.md](website/THIRD_PARTY_NOTICES.md)。

## 源码开发

需要 Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/Chengyunlai/ops-agent.git
cd ops-agent
uv sync
make check
```

运行本地 TUI：

```bash
mkdir -p config/local
cp config/examples/test.toml config/local/test.toml
make tui CONFIG=config/local/test.toml
```

站点使用独立的 Node/pnpm 模块，不进入 Python workspace：

```bash
make website-sync
make website
make website-check
```

## 项目状态

已完成 Kubernetes 资源监盘、确定性诊断、受控主/子 Agent、Metrics API、日志工作台、PVC 浏览、文件下载、人工 Pod Shell、独立安装包与 Homebrew 分发。

下一项已排期工作是 [Issue #14：日志快照与过滤结果的安全导出](https://github.com/Chengyunlai/ops-agent/issues/14)。其他方向通过 [GitHub Issues](https://github.com/Chengyunlai/ops-agent/issues) 维护，避免 README 中的任务清单与实际状态漂移。

## 贡献与许可证

欢迎提交 Issue 和 Pull Request。开始前请阅读 [贡献指南](CONTRIBUTING.md)，完成脱敏并运行 `make check`。

Ops Agent 采用 [Apache License 2.0](LICENSE)。
