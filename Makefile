.DEFAULT_GOAL := help

UV ?= uv
CONFIG ?= config/local/test.toml
ARGS ?=
TARGET ?= local
RELEASE_DIR ?= release

.PHONY: help sync lint format format-check test test-cli test-harness check cli tui bump-version package release

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
	$(UV) run ops-agent $(ARGS)

tui: ## 启动交互式终端；使用 CONFIG='...' 指定配置
	$(UV) run ops-agent --config "$(CONFIG)" tui

bump-version: ## 同步发布版本；使用 VERSION=x.y.z
	@test -n "$(VERSION)" || (echo "VERSION is required, for example VERSION=0.2.0" >&2; exit 2)
	$(UV) run python scripts/bump_version.py "$(VERSION)"
	$(UV) lock

package: ## 构建当前平台的独立 ops-agent 应用包
	$(UV) sync --locked --group build
	$(UV) run pyinstaller --clean --noconfirm packaging/ops-agent.spec

release: package ## 生成当前平台发布压缩包和 SHA-256；可设置 TARGET
	$(UV) run python scripts/create_release_archive.py \
		--bundle dist/ops-agent \
		--version "$$($(UV) run ops-agent --version | awk '{print $$2}')" \
		--target "$(TARGET)" \
		--output-directory "$(RELEASE_DIR)"
	$(UV) run python scripts/write_release_checksums.py "$(RELEASE_DIR)"
