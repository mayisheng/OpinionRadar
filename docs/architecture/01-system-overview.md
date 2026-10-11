# 01 · 系统总览

## 1. 目标与约束

定时采集关注的 B 站财经博主内容，经本地模型总结观点后推送到微信。设计约束：

- **单机运行**：一台常开的 MacBook Pro（M1 Pro / 32GB），不依赖云服务器。
- **数据不出本机**：Cookie、推送 Key、采集原文、SQLite 全部在本地；模型本地推理。
- **可迭代**：内容类型（视频→图文→付费）、博主数量、推送渠道、平台都是逐步扩展的。
- **对平台友好**：低频、只读、带抖动的个人使用，不做并发压测式采集。

## 2. 分层架构

```text
┌──────────────┐   ┌───────────────┐   ┌───────────────┐   ┌──────────────┐   ┌────────────┐
│  采集层        │ → │  存储层         │ → │  提取层         │ → │  分析层        │ → │  汇总/推送  │
│ collectors   │   │ storage        │   │ extractors    │   │ analysis     │   │ digest     │
│              │   │                │   │               │   │              │   │ notifiers  │
│ B站 Connector│   │ SQLite 去重    │   │ 字幕/Whisper  │   │ Ollama Qwen3 │   │ Server酱   │
│ 鉴权/限速/退避 │   │ 状态机         │   │ 正文清洗/OCR   │   │ 分类/摘要/观点│   │ 即时+日报  │
└──────────────┘   └───────────────┘   └───────────────┘   └──────────────┘   └─────┬──────┘
        ↑                                                                             ↓
  config.yaml + .env                                                           push_logs（幂等/重试）
                              launchd：每小时 run；每日 20:00 digest
```

数据流单向流动，每一层只依赖领域模型（[domain/models.py](../../../src/opinion_radar/domain/models.py)），
不跨层调用、不反向依赖，因此任意一层可独立替换（换模型、换推送渠道、加平台）。

## 3. 各层职责

| 层 | 包 | 职责 | 关键抽象 |
|---|---|---|---|
| 采集 | `collectors` | 轮询博主更新、wbi 签名、Cookie 鉴权、限速抖动、风控退避，产出统一 `ContentItem` | `Connector` |
| 存储 | `storage` | 增量去重（`platform + external_id` 幂等）、处理状态机、原始内容留档、推送记录 | SQLAlchemy + Alembic + SQLite |
| 提取 | `extractors` | 视频字幕获取/音频转写、专栏正文清洗、图文 OCR，填充 `body_text` | `Extractor` |
| 分析 | `analysis` | 内容分类、摘要、观点结构化抽取；Prompt 版本化；输出 schema 校验 | `LLMClient`（OpenAI 兼容） |
| 汇总/推送 | `digest`、`notifiers` | 单条消息渲染与即时推送、每日 digest 聚合、重试与幂等 | `Notifier` |
| 调度 | launchd + `cli` | 定时触发、失败退出码、日志落盘 | `opinion-radar run / digest` |

## 4. 横切关注点

- **配置**：[config.py](../../../src/opinion_radar/config.py) 加载 `config.yaml`（Pydantic 校验）+ `.env`（密钥）。
- **日志**：loguru，控制台彩色输出 + `data/logs/opinion_radar.log` 按 10MB 轮转、保留 14 天。
- **HTTP**：所有外部请求经 [utils/http.py](../../../src/opinion_radar/utils/http.py) 创建客户端（统一 UA、超时、跳转），
  经 [utils/retry.py](../../../src/opinion_radar/utils/retry.py) 对网络错误做指数退避重试。
- **密钥安全**：密钥只存在于环境变量/`.env`（不入库），日志中不允许出现 Cookie、SendKey、API Key。

## 5. 代码结构

采用 src 布局（`src/opinion_radar/`），完整目录见 [README 结构](../../README.md) 或实施计划：

```text
src/opinion_radar/
├── cli.py              # 命令入口（Typer）
├── config.py           # 配置模型与加载
├── pipeline.py         # 管线编排
├── domain/models.py    # 跨层共享的领域模型（无 IO 依赖）
├── collectors/         # 采集层（bilibili.py）
├── extractors/         # 提取层
├── analysis/           # LLM 客户端、schema、prompts/
├── digest/             # 日报聚合
├── notifiers/          # 推送渠道（serverchan.py）
├── storage/            # 数据库 + migrations/
└── utils/              # 日志、HTTP、重试
```

## 6. 关键技术决策（ADR）

| 决策 | 文档 |
|---|---|
| 使用 ADR 记录决策 | [ADR-0001](adr/0001-record-architecture-decisions.md) |
| Python + Ollama 本地技术栈 | [ADR-0002](adr/0002-python-ollama-local-stack.md) |
| 可插拔 Connector + httpx 直连 | [ADR-0003](adr/0003-pluggable-connector.md) |
| launchd 调度 | [ADR-0004](adr/0004-launchd-scheduling.md) |
