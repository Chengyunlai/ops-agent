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

不要在公开 Issue 中提交漏洞利用步骤、权限绕过细节或可用凭据。请使用
[GitHub 私密漏洞报告](SECURITY.md) 提交脱敏的影响范围和复现条件。

## 提交 Pull Request

### 开发前

- 对非简单修复，先创建或关联 Issue，确认目标和范围；
- 从最新的 `main` 创建短生命周期分支；
- 推荐使用 `feat/<description>`、`fix/<description>` 或
  `docs/<description>` 作为分支名。
- 禁止直接向 `main` 推送日常变更；所有代码、配置、CI 和文档变化都通过 PR。

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

修改 Kubernetes 诊断 Reader、规则或资源关系时，还应在一次性 kind 集群运行
`make test-kubernetes-integration`。所需环境变量和安全限制见 README；禁止把
该测试指向开发、测试或生产等共享集群。

### 自动合并门禁

PR 必须通过以下自动检查：

- `Repository policy`：PR 标题和 GitHub Action commit pin；
- `Dependency review`：拒绝新引入的高危或严重漏洞依赖；
- `Secret scan`：使用 Gitleaks 检查提交中的凭据和敏感信息；
- `Python 3.12` 与 `Python 3.14`：Ruff、格式、测试和 Python 分发包构建；
- `Kubernetes diagnostics integration`：一次性 kind 集群真实验证。

Secret Scanning 和 Push Protection 会在提交进入仓库前拦截已知凭据。机器人或
AI Review 的评论不能替代测试，也不能批准、合并、推送代码或触发发布。仓库
当前未配置 AI Reviewer；未来若启用，也只允许评论和提出建议。

### 人工审核

CODEOWNER 必须审核实现是否符合 Issue/设计、模块 interface、安全边界和文档。
以下变化必须在 PR 中明确说明风险，不能仅凭机器人结论合并：

- Agent Capability、工具注册或只读约束；
- Pod Shell、文件传输、PVC 管理或其他人工写能力；
- Kubernetes RBAC、namespace、代理、TLS、证书或 kubeconfig；
- Pydantic 配置兼容性和迁移；
- GitHub Actions 权限、依赖、打包和发布链路；
- TUI 行为或视觉变化。

所有讨论必须处理完成。使用 Squash Merge，PR 标题会成为 `main` 上的提交记录；
合并后删除短生命周期分支。

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

## 发布审核

版本只能通过独立 PR 使用 `make bump-version VERSION=x.y.z` 更新。版本 PR 合并且
required checks 全绿后，维护者才可在 `main` 提交上创建 SemVer tag。三平台构建
与冒烟测试自动执行；GitHub Release、provenance 和 Homebrew 更新必须等待
`release` Environment 的人工批准。
