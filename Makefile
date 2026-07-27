.DEFAULT_GOAL := help

UV ?= uv
CONFIG ?= config/local/test.toml
ARGS ?=

.PHONY: help sync lint format format-check test test-cli test-harness check cli

help: ## 显示可用的开发命令
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## 同步 workspace 依赖
	$(UV) sync

lint: ## 运行 Ruff 静态检查
	$(UV) run ruff check .

format: ## 使用 Ruff 自动修复并格式化代码
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

format-check: ## 检查代码格式但不修改文件
	$(UV) run ruff format --check .

test: ## 运行全部测试
	$(UV) run pytest -q

test-cli: ## 运行 CLI 测试
	$(UV) run pytest apps/cli/tests -q

test-harness: ## 运行 harness 测试
	$(UV) run pytest packages/harness/tests -q

check: lint format-check test ## 运行提交前完整检查

cli: ## 运行 CLI；使用 ARGS='...' 传递参数
	$(UV) run ops_agent $(ARGS)
