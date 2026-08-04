# 界面语言规范

Ops Agent 由中文社区优先维护，面向操作员的界面以简体中文为默认语言。当前阶段
不引入完整 i18n 框架；文案保留在对应的 Textual Screen 或 Widget 附近，便于
界面行为与提示同步维护。

## 中文化范围

- 操作、状态、帮助、错误、风险说明和表格字段使用简体中文。
- Pod、Deployment、Service、PVC、Namespace、Kubeconfig、CPU、Memory、
  HTTP、AI 等 Kubernetes 或技术固有名词保留英文。
- INFO、DEBUG、WARN、ERROR 等日志级别保留英文，避免与日志原文和检索条件混淆。
- Python 标识、CLI 命令、快捷键、TOML 配置字段和值不翻译。
- Agent Prompt、路由、安全策略和 Kubernetes 行为不受界面文案调整影响。

## 常用界面术语

| 英文概念 | 中文界面文案 |
| --- | --- |
| Settings | 设置 |
| Overview | 总览 |
| Refresh | 刷新 |
| Describe | 资源详情 / 详情 |
| Health | 健康诊断 / 健康 |
| Logs | 日志 |
| Follow | 实时跟随 |
| Log Focus | 日志聚焦 |
| Search | 搜索 |
| Regex | 正则 |
| Copy Mode | 复制模式 |
| Unavailable | 不可用 |
| Project Profile（界面） | 项目配置 |
| Interactive Pod Session（界面） | 交互式 Pod 会话 |

## 维护要求

新增界面能力时，应通过对应 Screen 或 Widget 的公共渲染结果测试关键文案。
README 截图需要使用 `scripts/capture_readme_screenshots.py` 重新生成并检查中文
宽字符没有重叠。领域文档仍可使用 `CONTEXT.md` 定义的英文术语；本规范只约束
操作员看到的界面和使用说明。
