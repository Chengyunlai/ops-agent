---
title: 配置参考
description: Project Profile、Kubernetes、模型、下载与主题配置的职责和安全约束。
---

Ops Agent 使用 TOML Project Profile。配置路径优先级为命令行 `--config`、`OPS_AGENT_CONFIG`、`$XDG_CONFIG_HOME/ops-agent/config.toml`，最后是 `~/.config/ops-agent/config.toml`。

## 最小示例

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
model = "gpt-5-mini"
api_key_env = "OPENAI_API_KEY"

[tui]
theme = "ops-dark"
```

模型名称必须支持工具调用。密钥只通过 `api_key_env` 指向的环境变量读取，配置文件中不得保存 API Key。

## Kubernetes

| 字段                      | 作用                               |
| ------------------------- | ---------------------------------- |
| `environment`             | 界面与证据中的环境标识             |
| `namespace`               | 监盘和 Agent 工具的固定 namespace  |
| `kubeconfig_path`         | kubeconfig 绝对路径                |
| `request_timeout_seconds` | Kubernetes 请求超时                |
| `proxy_url`               | Kubernetes API 使用的 HTTP(S) 代理 |

`proxy_url` 在创建 Kubernetes Client 前注入，因此不依赖启动 Shell 是否继承 macOS 系统代理。

## Watch 与 Metrics API

`kubernetes.watch` 控制只读 Watch 和轮询兜底。Watch 禁止、断开或超时时，资源监盘继续使用完整快照轮询。

`kubernetes.metrics` 只读取集群已经存在的 `metrics.k8s.io`。缺失、403 或临时失败时显示“不可用”，不会安装 metrics-server，也不会阻断资源清单。

## 人工 Pod 访问与下载

`kubernetes.interactive_exec.enabled` 默认是 `false`。开启后，操作员可以从左侧监盘进入选定 Pod/容器；该能力不注册为 Agent Tool。

`kubernetes.downloads.directory` 是 Pod/PVC 文件下载的本机根目录。`kubernetes.pod_transfer` 控制传输策略和单文件上限，目标路径不能逃逸下载根目录。

## 日志聚焦

`project.log_focus` 保存操作员选择的 INFO、DEBUG、健康检查、访问日志和明确文本隐藏规则。原始 Log Snapshot 始终保留，AI 不能生成或应用隐藏规则。

## 主题

内置主题为 `ops-dark`、`light` 与 `high-contrast`。`tui.colors` 可覆盖主色、强调色、背景、前景和警告色，颜色使用 `#RRGGBB`。

所有字段由冻结的 Pydantic 模型完成类型、范围、未知字段和跨字段校验。配置保存使用原子替换，失败不会留下半写文件。
