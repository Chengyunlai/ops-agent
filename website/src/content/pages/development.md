---
title: 源码开发
description: 使用 uv 开发、检查和测试 Ops Agent。
---

## 准备环境

源码开发需要 Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/Chengyunlai/ops-agent.git
cd ops-agent
uv sync
```

复制 `config/examples/` 中的模板到已被 Git 忽略的 `config/local/`，再填写本机 kubeconfig。不要提交真实密钥、集群地址或业务数据。

## 常用命令

```bash
make check
make test-cli
make test-runtime
make tui CONFIG=config/local/test.toml
```

`make check` 运行仓库策略、Ruff、格式和不依赖外部集群的测试。Kubernetes 集成测试只允许显式选择的一次性 kind context，不能指向共享环境。

站点开发使用 Node 22.12+ 与固定 pnpm：

```bash
make website-sync
make website
make website-check
```

完整 Issue、PR 与人工审核规则见 [CONTRIBUTING.md](https://github.com/Chengyunlai/ops-agent/blob/main/CONTRIBUTING.md)。
