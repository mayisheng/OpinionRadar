# 02 · 处理管线

> 本文描述管线的运行逻辑。迭代 001 先打通「视频」一条链路，其余内容类型按路线图补齐。

## 1. 触发方式

由 macOS launchd 拉起两个短生命周期命令（无常驻进程）：

| 任务 | 命令 | 频率 |
|---|---|---|
| 增量采集 | `opinion-radar run` | 每小时一次 |
| 每日汇总 | `opinion-radar digest` | 每日 20:00（可配） |

短任务 + launchd 的好处：崩溃不影响下次调度、内存天然释放、休眠唤醒后由下轮增量补回。
详见 [ADR-0004](adr/0004-launchd-scheduling.md)。

## 2. `run` 管线五阶段

### 阶段 1 · 采集（Collectors）

- 对每个启用的 source 创建对应平台 `Connector`（首期 `BilibiliConnector`）。
- 从 SQLite 读取该博主最后成功处理时间 `last_seen_at`，调用 `list_updates(since)` 增量拉取：
  视频 → 专栏 → 动态（后两者迭代 002）。
- 每条内容映射为统一 `ContentItem`，平台原始 JSON 存入 `payload` 并留档到 `data/raw/`。
- 限速：请求间隔 ≥ `collector.min_interval_seconds` + 0~`interval_jitter_seconds` 随机抖动；
  遇到网络错误走指数退避；遇到风控响应（如 -352/412）立即休眠并中止本轮，等下小时。

### 阶段 2 · 去重与状态（Storage）

- 以 `platform + external_id` 为唯一键 upsert，已存在且状态为 `pushed` 的直接跳过。
- 新条目状态置为 `discovered`，记录 `discovered_at`。
- 任意阶段失败置 `failed` 并写 `error` 字段，下一轮重试（带最大重试次数，超限标记死信）。

### 阶段 3 · 提取（Extractors）

按 `content_type` 选择提取器，产物写入 `ContentItem.body_text`，状态推进到 `fetched`。

| 类型 | 提取方式 | 迭代 |
|---|---|---|
| 视频 | ①官方字幕/AI 字幕 JSON → ②无字幕时 yt-dlp 下载音频 → mlx-whisper 转写 | 001 |
| 专栏 | HTML 正文清洗为 Markdown | 002 |
| 图文动态 | 文案 + 图片 RapidOCR，按阅读顺序拼接 | 002 |
| 付费图文 | 同上，额外携带 SESSDATA 鉴权 | 002 |

转写限并发 1（`transcription.max_concurrency`），避免风扇拉满影响其他任务。

### 阶段 4 · 分析（Analysis）

- `LLMClient` 通过 OpenAI 兼容协议调用本机 Ollama（默认 `qwen3:14b`）。
- Prompt 以文件形式存放在 `analysis/prompts/`，文件名带版本（如 `analyze_v1.txt`），
  分析结果记录 `prompt_version`，便于回溯质量回归。
- 要求模型返回 JSON，用 `AnalysisResult`（Pydantic）校验：
  `category`（行情点评/行业分析/个股观点/宏观…）、`tldr`、`viewpoints[]`、`sentiment`、`key_tags`。
- 校验失败或超时按 `llm.max_retries` 重试；观点结构化（标的/方向/逻辑/风险/去重）在迭代 003 深化。
- 成功后状态推进到 `analyzed`，结果落 `summaries` 表。

### 阶段 5 · 推送（Notifiers）

- `notify.immediate = true` 时，单条结果立即渲染为 Markdown 推送（标题=tldr，正文=观点列表+原文链接）。
- 渠道由 `notify.channel` 选择（首期 Server酱：`POST https://sct.ftqq.com/{key}.send`）。
- 推送前查 `push_logs` 幂等；网络失败重试；成功写 `message_id`，状态推进到 `pushed`。
- 本轮所有处理结束后更新博主 `last_seen_at`（只在阶段成功后推进，保证失败可重放）。

## 3. 内容状态机

```text
discovered ──提取成功──▶ fetched ──分析成功──▶ analyzed ──推送成功──▶ pushed
     │                     │                    │
     └────────失败─────────┴────────失败────────┴──推送失败──▶ failed（下轮重试 / 超限死信）
```

## 4. `digest` 日报管线（迭代 004）

查询当天 `analyzed/pushed` 的全部 Summary → 跨视频观点去重合并 → 按标的/方向分组 →
Markdown 模板渲染（超长自动切分）→ 通过同一 Notifier 发送一条汇总。

## 5. 失败处理总览

| 故障 | 行为 |
|---|---|
| B站网络抖动 | tenacity 指数退避重试（默认 3 次） |
| B站风控 | 立即中止本轮，日志记录，等待下个调度周期 |
| Ollama 未启动 / 超时 | 重试后本轮失败，条目停留原状态；迭代 005 增加云端兜底与失败告警推送 |
| 字幕缺失 + 转写失败 | 标记 `failed` 并记录错误，不阻塞同轮其他内容 |
| Server酱失败 | 不推进状态，下轮重推（幂等保证不重复） |
