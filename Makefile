.PHONY: help install lint format test run dry-run doctor bilibili-login discover ollama-pull launchd-install launchd-uninstall

help: ## 显示所有可用目标
	@grep -E "^[a-z-]+:.*##" $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## 安装全部依赖（含浏览器/音频/OCR 可选依赖）并启用 pre-commit
	uv sync --extra browser --extra audio --extra ocr
	uv run playwright install chromium
	uv run pre-commit install

lint: ## 静态检查（ruff + mypy）
	uv run ruff check .
	uv run mypy src/opinion_radar

format: ## 自动格式化与安全修复
	uv run ruff format .
	uv run ruff check --fix .

test: ## 运行测试
	uv run pytest

run: ## 执行一次完整管线
	uv run opinion-radar run

dry-run: ## 演练模式（不发起外部请求/写库）
	uv run opinion-radar run --dry-run

doctor: ## 环境自检
	uv run opinion-radar doctor

bilibili-login: ## 有头浏览器扫码登录 B 站（持久化登录态）
	uv run --extra browser opinion-radar bilibili-login

discover: ## 探测：真实拉取空间动态流并打印（不写库/不推送）
	uv run --extra browser opinion-radar discover

ollama-pull: ## 拉取本地模型（约 9GB）
	ollama pull qwen3:14b

launchd-install: ## 安装/更新本机 launchd 定时任务
	bash scripts/install_mac.sh install

launchd-uninstall: ## 卸载本机 launchd 定时任务
	bash scripts/install_mac.sh uninstall
