# OpinionRadar · 财经观点雷达

定时采集关注的 **B 站财经博主**内容（视频、专栏图文、付费/充电内容、行情观点动态），
经**本地大模型**识别、抽取并总结观点，聚合后通过 **Server 酱推送到微信**，
解决「关注的博主多、付费内容分散、观点看过就忘」的问题。

> 状态：迭代 000（仓库地基）已完成，业务能力从 [迭代 001](docs/product/iterations/001-mvp.md) 开始。

## 工作管线

```
B站采集（增量轮询/鉴权限速） → SQLite 去重与状态机
  → 内容提取（字幕优先，兜底本地 Whisper；图文 OCR）
  → 本地 LLM 分析（Ollama + Qwen3，分类/摘要/观点结构化）
  → Server酱推送到微信（单条即时 + 每日 digest）
```

定时由 macOS **launchd** 触发（默认每小时采集、每日 20:00 汇总），Mac 休眠错过的任务由下次增量轮询自然补回。

## 快速开始（macOS / Apple Silicon）

```bash
# 1. 安装 uv（已装可跳过）
brew install uv

# 2. 安装依赖（含转写/OCR 可选依赖）
make install

# 3. 准备配置与密钥
cp config/config.example.yaml config/config.yaml   # 填入博主 UID
cp .env.example .env                               # 填入 Server酱 SendKey 等

# 4. 拉取本地模型（约 9GB，仅首次）
make ollama-pull

# 5. 自检与演练（不发起外部请求/写库）
make doctor
make dry-run

# 6. 安装为 launchd 定时任务
make launchd-install
```

完整步骤见 [Mac 环境搭建手册](docs/runbook/local-setup-mac.md)，日常运维见[运维手册](docs/runbook/operations.md)。

## 文档导航

| 主题 | 文档 |
|---|---|
| 架构总览 / 管线 / 数据模型 | [docs/architecture](docs/architecture/01-system-overview.md) |
| 重大决策记录（ADR） | [docs/architecture/adr](docs/architecture/adr/0001-record-architecture-decisions.md) |
| 开发工作流 / 编码规范 / 测试 | [docs/development](docs/development/workflow.md) |
| 产品需求 / 迭代流程 / 路线图 | [docs/product](docs/product/prd.md) |
| 环境搭建 / 运维手册 | [docs/runbook](docs/runbook/local-setup-mac.md) |
| 变更记录 | [CHANGELOG.md](CHANGELOG.md) |

## 技术栈

Python 3.12 · httpx · Pydantic v2 · SQLAlchemy + Alembic · Typer · loguru ·
Ollama（Qwen3-14B）· mlx-whisper · RapidOCR · Server酱 · launchd · uv

## 使用边界

本项目仅供**个人学习与信息管理**：仅采集本人账号有权访问的内容，不二次传播付费内容，
请控制访问频率、遵守平台用户协议。所有凭证仅保存在本机，请勿分享 `.env` 与 `data/` 目录。
