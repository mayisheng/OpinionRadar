# Mac 环境搭建手册

适用：Apple Silicon（M1/M2/M3）macOS，本机常驻运行。已在 M1 Pro / 32GB 验证。

## 1. 前置检查

```bash
# 需要 Homebrew（https://brew.sh）
brew --version
# Ollama（已安装可跳过）
ollama --version || brew install --cask ollama
```

## 2. 安装 uv 与项目依赖

```bash
brew install uv
cd OpinionRadar

make install
# 等价于：uv sync --extra audio --extra ocr && uv run pre-commit install
```

- Python 3.12 由 uv 自动下载到虚拟环境 `.venv/`，不影响系统 Python。
- `audio` extra 含 yt-dlp 与 mlx-whisper（首次安装稍慢）；`ocr` 含 RapidOCR。
- 若只想跑最小环境：`uv sync`（不含转写/OCR）。

## 3. 配置博主与密钥

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

编辑 `config/config.yaml`：

- `sources[].uid`：博主主页 URL 中的数字 UID，如 `https://space.bilibili.com/123456` → `123456`。
- 其余默认值在 M1 Pro 32GB 上可直接用；内存更小可把模型改为 `qwen3:8b`。

编辑 `.env`：

- `OPINION_RADAR_SERVERCHAN_SENDKEY`：打开 <https://sct.ftqq.com/> 微信扫码登录，
  复制 SendKey。
- `OPINION_RADAR_BILIBILI_SESSDATA`：迭代 001 只处理公开视频，**可留空**；
  迭代 002 需要时按[运维手册](operations.md#获取-b站-sessdata)填写。

> `.env` 与 `config/config.yaml` 均已在 `.gitignore`，不会被提交。

## 4. 拉取并验证本地模型

```bash
make ollama-pull            # ollama pull qwen3:14b，约 9GB，仅首次
ollama list                 # 确认 qwen3:14b 在列表中
ollama run qwen3:14b "用一句话解释什么是市盈率" /exit
```

确保 Ollama 应用处于运行状态（菜单栏有图标），模型服务默认监听 `http://localhost:11434`。

## 5. 自检与演练

```bash
make doctor                 # 配置、博主、Ollama 三项均 PASS
make dry-run                # 打印当前配置对应的 5 阶段管线计划
```

doctor 输出示例：

```text
[PASS] 配置文件: config/config.yaml
[PASS] 监控博主: 已启用 1 个（xxx）
[PASS] Ollama: 在线，已安装模型：qwen3:14b,...
```

## 6. 安装定时任务

```bash
make launchd-install
```

脚本会：同步依赖 → 生成两个 plist 到 `~/Library/LaunchAgents/` → 加载并立即跑一次。

| 任务 | 行为 |
|---|---|
| com.opinionradar.collect | 每小时执行一次 `opinion-radar run`，加载时立即执行 |
| com.opinionradar.digest | 每天 20:00 执行 `opinion-radar digest`（迭代 004 起生效） |

stdout/stderr 在 `data/logs/launchd-*.out`，应用日志在 `data/logs/opinion_radar.log`。

验证：

```bash
launchctl print gui/$(id -u)/com.opinionradar.collect | head -20
tail -f data/logs/opinion_radar.log
```

## 7. 更新与卸载

```bash
git pull && uv sync --extra audio --extra ocr   # 更新代码与依赖
make launchd-install                            # 重装（幂等，会刷新 plist）

make launchd-uninstall                          # 停止并移除两个定时任务
```

数据库升级（迭代 001 引入）：更新前先 `cp data/opinion_radar.db data/backup.db`，
安装脚本会执行 `alembic upgrade head`。

## 8. 常见安装问题

- **doctor 报 Ollama 不可达**：确认 Ollama.app 已启动；`curl http://localhost:11434/api/tags`。
- **mlx-whisper 安装失败**：确认是 Apple Silicon 与较新 macOS；Intel 机型改用
  `faster-whisper`（迭代 001 评估，写新 ADR）。
- **推送收不到**：先用浏览器访问
  `https://sct.ftqq.com/<你的KEY>.send?title=test&desp=hello` 验证 Key 与微信关注通道。
- **休眠后任务没跑**：属预期，唤醒后下一轮增量会补采；如长时间合盖建议插电并在
  系统设置中允许网络唤醒。
