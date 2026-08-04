---
title: 关于 Ops Agent
description: Ops Agent 的定位、当前阶段与维护原则。
---

Ops Agent 是一个本地运行的 Kubernetes 终端工作台，面向需要直接观察资源、排查日志并保留人工控制权的运维人员。

项目当前聚焦只读监控和诊断。告警接入、审批、变更执行与审计仍在规划中，未完成的能力不会在界面或文档中描述为已经可用。

## 设计原则

- 真实工具证据优先于模型推断；
- AI 只读能力与人工 Pod 操作保持独立；
- 不向目标集群安装常驻工作负载；
- 配置、权限和 namespace 在应用启动时固定；
- 功能变化通过 Issue、PR、自动检查和人工审核交付。

源码、Issue 和发布包位于 [GitHub 仓库](https://github.com/Chengyunlai/ops-agent)。
