---
title: 快速开始
description: 安装 Ops Agent、初始化 Project Profile 并连接第一个 Kubernetes namespace。
---

## 前置条件

- macOS 或 Linux；
- 可读取目标 namespace 的 kubeconfig；
- 支持工具调用的 OpenAI-compatible 模型；
- 可选的 `kubectl`，仅 Pod Shell 与文件传输需要。

Ops Agent 不会向集群安装 Operator、CRD、DaemonSet、Exporter 或监控服务。

## 安装

```bash
brew tap Chengyunlai/tap
brew install ops-agent
ops-agent --version
```

也可以从 [GitHub Releases](https://github.com/Chengyunlai/ops-agent/releases) 下载 macOS arm64、macOS amd64 或 Linux amd64 压缩包。每个 Release 都提供 `SHA256SUMS` 和 GitHub build provenance。

## 初始化配置

```bash
ops-agent init
ops-agent config path
```

默认配置位于 `~/.config/ops-agent/config.toml`。编辑生成的 Project Profile，确认 kubeconfig、environment 与 namespace 都指向预期目标。

模型密钥只通过环境变量提供，不要写入 TOML：

```bash
export OPENAI_API_KEY="..."
```

使用 DeepSeek 等 OpenAI-compatible 接口时，在配置中填写对应 `base_url`、模型名和密钥环境变量名。

## 启动前诊断

```bash
ops-agent doctor
```

`doctor` 检查配置、kubeconfig、模型密钥环境变量、Pod 读取权限和 `kubectl`。缺少 `kubectl` 不影响只读监盘，但会禁用需要它的人工操作。

## 打开终端工作台

```bash
ops-agent tui
```

左侧显示固定 namespace 的资源与确定性 Finding，右侧提供受控诊断对话。按 `?` 查看帮助，按 `Esc` 或 `q` 退出。

如果暂时不需要 TUI，也可以直接提问：

```bash
ops-agent ask "检查所有 Pod，指出异常和最近重启"
```
