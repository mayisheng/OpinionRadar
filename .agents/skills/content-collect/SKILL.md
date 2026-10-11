---
name: content-collect
description: 当用户要求抓取/采集博主或内容源的最新内容（动态、视频、文章）、补齐归档中缺失的正文或图片、回溯某年以来的全部内容、或询问「现在抓到了什么」时使用。封装 discover / archive / backfill-detail 等 CLI 操作的执行顺序、风控约束与验证方法。不负责解析器调试（走 api-probe）和方案决策（走 archive-adr）。
---

# content-collect：内容源采集操作

## 目的

把真实浏览器响应拦截采集（见对应 ADR）的日常操作固化成 SOP，
保证每次采集都符合风控基线与合规红线，产出可直接核验的本地归档。
平台差异收敛在各 Connector 实现内，本技能只约束跨平台通用的操作流程。

## 触发时机

- 「抓一下某博主 / 看看最近有什么动态」「归档 XX 年至今的内容」。
- 「归档里动态没正文 / 缺图片，补一下」→ 详情回填。
- 解析器修正后需要重跑补齐历史归档。

## 可用命令

| 命令 | 用途 | 备注 |
|---|---|---|
| `uv run opinion-radar discover --hours N` | 探测时间窗内新内容，只打印不落盘 | 怀疑链路问题时先跑这个 |
| `uv run opinion-radar archive --year YYYY --max-scrolls N` | 回溯归档整年内容到 data/archive/ | 回溯场景放宽滚动上限 |
| `uv run opinion-radar backfill-detail --limit N` | 补齐归档中缺正文/图片的条目详情 | 逐条打开详情页，人类节奏 |
| 平台登录命令 | 有头浏览器扫码登录，持久化登录态 | `uv run opinion-radar --help` 查看当前子命令 |

## 执行顺序

1. **冷却检查**：`data/` 下存在未过期的风控冷却标记文件（文件名见采集器浏览器模块常量）时，直接停止并告知用户解除时间，不得启动浏览器。
2. **登录态**：profile 未登录会抛 `LoginRequiredError`，引导用户执行平台登录命令（需要人工扫码）。
3. **增量优先**：日常采集用默认 headless；回填批量较大时加 `--limit`（建议每轮 ≤10 条），必要时 `--headed` 便于人工观察验证码。
4. **验证产出**：采集完成后统计归档结果并向用户汇报，例如：

```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('data/archive/*/*.json')):
    d = json.load(open(f))
    p = d.get('payload') or {}
    print(f[:15], d['status'], len(d.get('body_text') or ''), '字', len(p.get('image_urls') or []), '图')
"
```

5. **异常处理**：`RiskControlBlocked`（验证码 / 业务风控码）→ 立即停止本轮，报告用户，等待冷却；不得调整风控参数（节奏、上限）硬闯。

## 硬约束（违反即停）

- 抓取内容只写 `data/archive/`（已 gitignore），不得提交、不得上传远程、不得粘贴到 Issue/PR。
- 未付费内容直接标记「付费不可达」，不得尝试绕过付费墙；不录制、不保存任何视频/音频文件，仅提取文字用于观点分析。
- 风控节奏参数（动作间隔、滚动上限、冷却时长）是安全基线，禁止为提速调小。
- 归档幂等：已存在文件自动跳过，可安全重跑；详情回填覆写前先合并（非空字段不覆盖）。

## 边界

- 解析器拿不到预期字段、接口结构核对 → 用 [api-probe](../api-probe/SKILL.md)。
- 采集方案的架构决策记录 → 用 [archive-adr](../archive-adr/SKILL.md)。
- 某类内容（如视频字幕、评论区）尚未实现时，向用户说明现状而非硬做。
