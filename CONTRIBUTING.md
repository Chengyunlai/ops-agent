# 贡献指南

感谢你参与 Ops Agent。为了让 Issue 能够被快速判断、让 Pull Request 能够被
安全审查，请先阅读并遵循以下约定。

## 提交 Issue

提交前请先搜索现有 Issue，确认没有相同问题。一个 Issue 只描述一个独立问题
或需求，标题应直接说明影响，例如：

- `Pod 日志视图只显示第一行`
- `为 PVC 文件浏览增加大小排序`

### Bug 报告

Bug Issue 至少应包含：

1. Ops Agent 版本、安装方式、操作系统与终端类型；
2. 已脱敏的环境和配置说明；
3. 可以重复执行的复现步骤；
4. 期望结果与实际结果；
5. 必要的日志、截图或错误堆栈；
6. 是否涉及 Kubernetes 写权限、Pod Shell 或文件传输。

请使用
[Bug 报告模板](.github/ISSUE_TEMPLATE/bug-report.md)。如果问题无法稳定复现，
请说明出现频率和最近一次发生时间。

### 功能建议

功能 Issue 应说明要解决的用户问题，而不仅是指定一种实现。至少包含：

1. 使用场景和当前障碍；
2. 期望的用户体验和验收结果；
3. 明确不在本次需求范围内的内容；
4. 对配置兼容性、Kubernetes 权限和安全边界的影响。

请使用
[功能建议模板](.github/ISSUE_TEMPLATE/feature-request.md)。

### 脱敏要求

Issue、日志、截图和附件中不得包含：

- API Key、Token、证书、Cookie 或 kubeconfig 凭据；
- 真实公网地址、内部域名或不应公开的集群地址；
- 未脱敏的用户名、邮箱、namespace、Pod 名称或业务数据。

推荐使用 `demo`、`sample-app` 等虚构名称替换真实信息。安全问题中也不要提交
可用凭据或生产数据。

### 安全漏洞

不要在公开 Issue 中提交漏洞利用步骤、权限绕过细节或可用凭据。请先创建一个
标题为 `[Security] Request private reporting channel` 的联络 Issue，其中只
说明受影响的能力类别并请求私密联系方式；维护者建立私密渠道后，再提供完整
报告和复现材料。

## 提交 Pull Request

### 开发前

- 对非简单修复，先创建或关联 Issue，确认目标和范围；
- 从最新的 `main` 创建短生命周期分支；
- 推荐使用 `feat/<description>`、`fix/<description>` 或
  `docs/<description>` 作为分支名。

### 变更要求

- 一个 PR 只处理一个主题，避免混入无关重构；
- 新增或修改行为时补充相应测试；
- 配置字段变化必须同步更新示例配置、校验模型和 README；
- TUI 视觉变化应附脱敏前后截图；
- Agent 工具默认保持只读；人工 Pod Shell 和文件传输必须独立于 Agent 图及
  工具注册。若要改变该边界，必须提供独立安全设计并获得明确审查；
- Kubernetes 访问应保持最小 RBAC 权限，并校验 namespace、资源类型和目标
  资源范围；
- 不得提交真实凭据、集群配置、生产地址或敏感日志。

提交前运行：

```bash
make check
```

### 提交与 PR 标题

提交信息和 PR 标题使用以下格式：

```text
<type>: <short description>
```

常用类型：

- `feat`：新增功能；
- `fix`：修复缺陷；
- `docs`：仅文档变化；
- `refactor`：不改变外部行为的重构；
- `test`：测试变化；
- `chore`、`ci`、`build`：维护、CI 或构建变化。

示例：

```text
feat: add namespace resource filter
fix: preserve pod log line breaks
docs: clarify Homebrew installation
```

### PR 描述

PR 描述应使用
[Pull Request 模板](.github/pull_request_template.md)，并包含：

- 变更目的与实现范围；
- 关联 Issue，例如 `Closes #123`；
- 实际执行过的验证命令和结果；
- 兼容性、权限及安全影响；
- 尚未解决的问题或后续工作。

维护者会重点审查正确性、范围、安全边界、测试覆盖和文档一致性。
