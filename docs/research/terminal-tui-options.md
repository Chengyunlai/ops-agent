# 类 k9s 终端 TUI 技术选型

> 调研日期：2026-07-27
> 范围：只讨论现有 Python `ops_agent` 的终端交互层，不改变 Agent、
> Kubernetes 访问和安全边界。资料均来自项目官方仓库、官方文档、发布页或
> PyPI。

## 结论

使用 **Textual** 实现第一版 TUI，并把它放在 `apps/cli` 的内部模块中；
继续由 `bootstrap.py` 组装 `OpsAgent`，不要让 TUI 直接依赖 LangGraph、
Kubernetes SDK 或工具实现。

应借鉴 k9s 的交互模型，而不是复刻其 Go 内部架构：

- 常驻的集群/命名空间上下文；
- 键盘优先、可见的快捷键提示和 `?` 帮助；
- 资源列表、详情/日志与命令输入之间的稳定焦点切换；
- `/` 搜索、命令面板和明确的只读状态；
- 后台刷新不阻塞键盘输入，错误显示在当前界面而不是破坏终端。

当前 `OpsAgent.ask()` 是同步、一次性返回最终文本的接口。MVP 应通过
Textual Worker 在线程中调用它，先提供“提问—运行状态—最终回答”的完整链路。
不要伪造 token 流式输出；以后若核心层提供稳定的结构化事件接口，再显示
路由、计划步骤和工具证据。

## 候选框架

以下版本和发布时间来自各项目官方最新 Release 与 PyPI，均在调研日核实。

| 框架 | 活跃度与许可证 | 并发/后台任务 | 表格、树与键盘 | 测试与打包 | 判断 |
| --- | --- | --- | --- | --- | --- |
| [Textual](https://github.com/Textualize/textual) | [8.2.8（2026-06-30）](https://github.com/Textualize/textual/releases/tag/v8.2.8)，MIT；[PyPI 要求 Python >=3.9](https://pypi.org/project/textual/) | 原生 [Worker API](https://textual.textualize.io/guide/workers/) 支持异步任务和线程任务，适合包装当前同步的 `ask()` | 原生 [DataTable](https://textual.textualize.io/widgets/data_table/)、[Tree](https://textual.textualize.io/widgets/tree/)，通过 Binding/Action 声明键盘行为 | `run_test()` 与 Pilot 支持无真实终端的 [async 测试](https://textual.textualize.io/guide/testing/)；有官方 [Hatch 打包指南](https://textual.textualize.io/how-to/package-with-hatch/)，与本项目 Hatchling/uv 组合一致 | **推荐**。应用框架完整，最少自建基础设施，且 Python 3.14 落在声明范围内 |
| [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) | [3.0.53（2026-07-26）](https://github.com/prompt-toolkit/python-prompt-toolkit/releases/tag/3.0.53)，BSD-3-Clause；[PyPI 要求 Python >=3.10](https://pypi.org/project/prompt-toolkit/) | `Application.run_async()`、后台协程和线程执行器可与 asyncio 集成，见官方 [异步提示](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html#prompt-in-an-asyncio-application) | [全屏应用](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/full_screen_apps.html)、布局与 [Key bindings](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/advanced_topics/key_bindings.html) 很强，但复杂表格/树需自行建立模型、渲染和选择行为 | 官方仓库包含测试；可作为普通 Python 包随 Hatchling 安装 | 适合 REPL、补全和命令栏；做 k9s 式多面板资源浏览器时需要自行补齐太多 UI 组件，**不作为主框架** |
| [Urwid](https://github.com/urwid/urwid) | [4.0.8（2026-07-26）](https://github.com/urwid/urwid/releases/tag/4.0.8)，LGPL-2.1-only；[PyPI 要求 Python >=3.9](https://pypi.org/project/urwid/) | 支持 [asyncio/Tornado/Twisted 等事件循环](https://urwid.org/manual/mainloop.html)，后台任务的生命周期需要应用自行组织 | 提供 `ListBox`、`TreeWalker`、`Columns`、`Pile`，并以 `keypress()`/signals 处理交互，见官方 [Widget 参考](https://urwid.org/reference/widget.html) | 官方仓库包含单元测试；普通 Python 包可随现有构建系统安装 | 稳定、底层可控，但表格、样式、任务与测试体验的应用层工作更多；LGPL 也增加许可证审查成本，**不优先** |

Textual 的优势不是“能画终端”，而是 Worker、组件、焦点/Binding 和可测试的
应用生命周期已经组成一个框架。prompt_toolkit 可在以后需要高级补全的独立
REPL 时重新评估；不建议为了复用它的输入框而同时引入两套终端事件循环。

## 可借鉴的 Kubernetes / 运维 TUI

### k9s

[k9s](https://github.com/derailed/k9s) 持续 watch Kubernetes 资源，并在资源
变化后更新界面；这正是它与“一次运行一个命令”的普通 CLI 的核心差异。
官方 [Commands 文档](https://k9scli.io/topics/commands/) 展示了值得继承的
交互语言：`:pod`/`:ns` 切换视图，`/` 过滤，`Esc` 退出当前模式，`?` 查看
快捷键，以及 describe/view/log 等面向选中资源的动作。

其源码也明确分开了
[client](https://github.com/derailed/k9s/tree/master/internal/client)、
[dao](https://github.com/derailed/k9s/tree/master/internal/dao)、
[model](https://github.com/derailed/k9s/tree/master/internal/model)、
[watch](https://github.com/derailed/k9s/tree/master/internal/watch) 和
[view](https://github.com/derailed/k9s/tree/master/internal/view)。可借鉴的是
“数据获取、长期运行模型、展示/输入分层”，不是照搬包名。k9s 使用 Go，
以多平台独立二进制、Homebrew、系统包和容器发布，详见
[官方安装说明](https://github.com/derailed/k9s#installation)；它不是现有
Python 项目的可嵌入框架。

维护状态良好：Apache-2.0，
[最新发布 v0.51.0（2026-06-06）](https://github.com/derailed/k9s/releases/tag/v0.51.0)。

### KDash

[KDash](https://github.com/kdash-rs/kdash) 是 Rust/Ratatui Kubernetes
dashboard。其 [README](https://github.com/kdash-rs/kdash#keybindings)
展示了资源 tab、列表下钻、过滤、日志视图、主题、可配置快捷键和确认对话框；
端口转发在后台运行并随应用退出而清理。这提醒本项目：长期任务必须由应用拥有
生命周期，危险动作必须有显式确认，不能仅靠 Agent 文本说明。

它通过 crates.io、Homebrew、系统包、安装脚本、Docker 和多平台二进制发布，
详见 [安装说明](https://github.com/kdash-rs/kdash#installation)。MIT，
[最新发布 v2.1.1（2026-07-22）](https://github.com/kdash-rs/kdash/releases/tag/v2.1.1)，
维护活跃。由于是 Rust 应用，只适合作为交互和生命周期参考。

### kubetui

[kubetui](https://github.com/sarub0b0/kubetui) 同样基于 Rust/Ratatui。官方
[功能说明](https://github.com/sarub0b0/kubetui#features) 包括资源实时 watch、
Pod 日志、Event、任意资源、可调整分栏、列配置、列感知过滤、多 namespace、
上下文切换、鼠标和增量搜索。它比 k9s 更适合作为“可配置表格与详情面板”的
直接参考，也说明服务端 label selector 与本地文本过滤应当分开。

它通过 Cargo、Homebrew、多个系统包和预编译二进制发布，详见
[安装说明](https://github.com/sarub0b0/kubetui#installation)。MIT，
[最新发布 v1.14.0（2026-06-05）](https://github.com/sarub0b0/kubetui/releases/tag/v1.14.0)，
维护活跃；同样不适合直接嵌入 Python 工程。

## 放入当前工程的边界

建议先保持一个发行包和一个可执行文件：

```text
apps/cli/src/ops_agent_cli/
├── main.py                 # argparse；保留 ask，增加 tui 入口
├── bootstrap.py            # 唯一组合根
└── tui/
    ├── app.py              # Textual App、Screen 与生命周期
    ├── state.py            # 纯 UI 状态，不是 LangGraph State
    └── widgets/            # 对话、状态栏、证据/结果展示
```

依赖方向应保持：

```text
Textual UI -> OpsAgent.ask() -> harness 主图/专业 Agent
```

TUI 不应导入 `agent.orchestration`、LangGraph state、Kubernetes reader 或
具体 tool。UI state 只描述输入、选中项、busy/error/result 等显示状态；
Graph state 仍只属于 harness。这样未来 API、Web UI 或不同 TUI 布局都能复用
核心能力。

## 最小 tracer-bullet MVP

目标不是立即复制资源浏览器，而是验证真实调用可以安全穿过完整 TUI 生命周期：

1. `ops_agent tui --config ...` 启动一个 Screen，显示环境、固定 namespace 和
   “只读诊断”标识。
2. 一个提问输入框、提交快捷键、只读结果面板、状态栏和 `q`/`?` 键。
3. 提交后禁用重复提交，通过 Textual thread worker 调用现有同步
   `OpsAgent.ask()`；界面持续响应，并显示 running/success/error。
4. Worker 完成后只显示真实最终结果；取消或退出时忽略迟到结果，不让后台回调
   修改已卸载的 Screen。
5. 使用 `run_test()`/Pilot 测试启动、焦点、提交、防重复提交、成功、异常、
   退出；`OpsAgent` 使用 fake，不访问模型或集群。
6. 保留原有 `ops_agent ask`，并运行原 CLI 测试，证明新增 TUI 没有改变
   非交互用法。

这个切片能验证框架、组合根、后台任务、错误边界和测试策略。资源表、watch、
日志流、命令面板、会话历史和 Agent 过程事件应在这个切片稳定后逐个增加；它们
需要新的结构化核心接口，不应从最终回答字符串中反向解析。

## 不推荐的方向

- 不 fork 或嵌入 k9s/KDash/kubetui：语言、发布模型和现有 Python 核心不匹配。
- 不把 TUI 放入 `packages/harness`：展示生命周期会污染可复用 Agent 能力。
- 不让 UI 直接调用 Kubernetes SDK：会绕过当前固定 namespace、只读工具和
  证据校验边界。
- 不在 MVP 同时实现资源 watch 与 Agent 对话：两类长期任务一起落地会掩盖
  线程、取消和退出清理问题。
- 不同时采用 Textual 与 prompt_toolkit：两套焦点、输入和事件循环会增加
  终端恢复及测试复杂度。
