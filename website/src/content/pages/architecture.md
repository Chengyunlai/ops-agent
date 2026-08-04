---
title: 架构与安全边界
description: Ops Agent 的应用、runtime、受控图、专业 Agent 和人工操作 seam。
---

Ops Agent 把终端应用与可复用 runtime 分开。`apps/cli` 负责配置、组合、TUI 和人工访问；`packages/runtime` 负责 Kubernetes 读取、确定性诊断与受控 Agent 编排。

## 请求链路

```text
用户请求
  → Interaction Context（可信）
  → Intent Proposal（不可信）
  → Policy Decision（纯代码）
  → 已注册的只读 Capability
  → Kubernetes 专业 Agent
  → 工具证据校验与回答
```

模型只能提出结构化意图，不能授予能力。Policy 使用固定 environment、namespace、资源类型和实际注册工具进行校验；没有成功工具证据时，系统不会输出实时集群结论。

## 确定性诊断

Kubernetes Reader 只负责结构化读取。诊断层消费 Pod、Deployment、Service、Endpoint、Event 和资源声明，输出带稳定 code 的 Finding，不依赖 LLM。

CrashLoop、OOM 等 Finding 会触发受控 Evidence Collection，固定读取关联 Event 与 previous logs。模型只解释采集结果，不决定是否执行这些必要取证。

## 人工操作 seam

Pod Shell、Pod/PVC 文件传输位于 CLI 的 `manual_access` 模块。它们不属于 Agent Capability，也不会因为模型提示词或图路由而执行。

## 零集群部署

应用只读取 kubeconfig 指向的接口，不安装 Operator、CRD、DaemonSet、Exporter、Prometheus 或 metrics-server。可选数据源缺失时局部降级。

架构决策记录保留在 [仓库 ADR](https://github.com/Chengyunlai/ops-agent/tree/main/docs/adr)，实现计划位于 [docs/plans](https://github.com/Chengyunlai/ops-agent/tree/main/docs/plans)。
