# 03 · 数据模型

> 本文是迭代 001 建表与 Alembic 迁移的设计依据。当前代码仅有引擎骨架
>（[storage/database.py](../../../src/opinion_radar/storage/database.py)），表尚未创建。

## 1. 总览

- 数据库：SQLite 单文件 `data/opinion_radar.db`（零运维、单机足够）。
- ORM：SQLAlchemy 2.x；迁移：Alembic，任何表结构变更必须走迁移脚本。
- 大字段（原始 JSON、音频、字幕、转写文本）存文件 `data/raw/`，数据库只存路径。
- 备份只需拷贝 `data/` 目录（建议纳入 Time Machine）。

## 2. 表结构（计划）

### `sources` — 监控博主

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int pk | |
| platform | text | `bilibili`（为多平台预留） |
| source_uid | text | 博主 UID |
| name | text | 展示名 |
| enabled | bool | |
| last_seen_at | datetime | 最后成功处理时间（增量游标） |
| created_at / updated_at | datetime | |

唯一约束：`(platform, source_uid)`。

### `content_items` — 内容主表（状态机所在）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int pk | |
| platform | text | |
| source_id | int fk | → sources.id |
| external_id | text | BV 号 / cvid / 动态 id |
| content_type | text | `video` / `column` / `moment` / `paid_article` |
| title | text | |
| url | text | |
| is_paid | bool | |
| published_at | datetime | 平台发布时间 |
| status | text | `discovered/fetched/analyzed/pushed/failed` |
| raw_path | text | 原始响应留档路径 |
| body_path | text | 正文/转写文本路径 |
| attempts | int | 处理尝试次数 |
| error | text | 最近一次错误（脱敏） |
| discovered_at / processed_at | datetime | |

唯一约束：`(platform, external_id)`；索引：`(source_id, status)`、`(status, discovered_at)`。

### `summaries` — AI 分析结果

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int pk | |
| content_id | int fk, unique | → content_items.id |
| category | text | |
| tldr | text | 一句话摘要 |
| viewpoints_json | text | 观点数组 JSON（标的/方向/逻辑/依据/风险/时间点） |
| sentiment | text | |
| tags_json | text | |
| model | text | 如 `qwen3:14b` |
| prompt_version | text | 如 `analyze_v1` |
| created_at | datetime | |

### `push_logs` — 推送记录（幂等与排查）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int pk | |
| content_id | int fk nullable | 单条推送关联；日报为空 |
| kind | text | `immediate` / `daily_digest` / `alert` |
| channel | text | `serverchan` … |
| ok | bool | |
| message_id | text nullable | 渠道返回 id |
| response | text | 渠道响应摘要（截断） |
| pushed_at | datetime | |

索引：`(content_id, kind)` 用于幂等查询。

## 3. 状态机

见 [02 · 处理管线](02-pipeline.md#3-内容状态机)。要点：

- 状态只能单向前进；失败回退到 `failed` 而不是删除记录，保证可重放。
- `last_seen_at` 在整轮成功后才推进，避免「采集成功但处理失败」导致漏内容。
- `attempts` 超过阈值（默认 5）标记死信：不再自动重试，靠运维检查（迭代 005 做告警）。

## 4. 文件存储

```text
data/
├── opinion_radar.db          # SQLite
├── logs/opinion_radar.log    # 轮转日志
└── raw/
    └── bilibili/
        └── {source_uid}/
            ├── {external_id}.json    # 原始响应
            ├── {external_id}.txt     # 正文/字幕/转写
            └── {external_id}.m4a     # 音频（转写完成后可定期清理）
```

## 5. 迁移策略

- 首个迁移由 `alembic revision --autogenerate` 生成上述 4 张表。
- 每次涉及字段/索引的改动：一个迁移一个提交，迁移脚本必须包含 upgrade 与 downgrade。
- 发版前在本机备份数据库后执行 `alembic upgrade head`；0.x 阶段不保证 downgrade 在真实数据上演练，
  但必须可执行。
