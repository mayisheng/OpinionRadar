# 开发工作流

单人项目也要有可回溯的工程节奏。核心原则：**main 永远可发布，改动留痕，质量门禁自动化**。

## 1. 分支模型：主干开发（Trunk-Based）

```text
main（始终可发布、受保护）
 │
 ├── feat/bilibili-subtitle     功能短分支
 ├── fix/serverchan-retry       缺陷修复
 ├── docs/iteration-process     文档
 └── chore/upgrade-qwen3        依赖/杂项
```

- 一切改动从最新 `main` 切短分支，生命周期尽量不超过一个迭代。
- 合并方式：**Squash merge**（main 上保持「一个 PR 一个语义提交」）。
- 单人也走 PR：推送分支 → 在 GitHub 开 PR → 按 [PR 模板](../../../.github/PULL_REQUEST_TEMPLATE.md)
  自检 → CI 绿 → 自己合并。价值在于：CI 记录、合并前的整体 diff 审阅、以后回溯有据。
- 紧急 hotfix 可先合后补 PR，但当天必须补齐说明。

### 分支命名

`feat/xxx`、`fix/xxx`、`docs/xxx`、`refactor/xxx`、`test/xxx`、`chore/xxx`，
用英文短词或对应 Issue 编号（如 `feat/issue-12-digest-dedup`）。

## 2. 提交信息：Conventional Commits

格式：`type(scope): 中文简述`

| type | 用途 |
|---|---|
| `feat` | 用户可感知的新能力 |
| `fix` | 缺陷修复 |
| `docs` | 只改文档 |
| `refactor` | 不改变行为的重构 |
| `test` | 只改测试 |
| `chore` | 构建、依赖、配置、脚手架 |

示例：`feat(collectors): 新增 UP 主视频列表 wbi 签名与增量轮询`

规则：

- 一个提交只做一件事；提交信息说「做了什么 + 为什么」，不说「改了几个文件」。
- pre-commit 自动执行 ruff / mypy；不过门禁无法提交（不要用 `--no-verify` 绕过）。

## 3. 任务与看板

- 所有待办进 **GitHub Issues**（仓库提供 feature / bug 模板），标签：
  `type: feature|bug|chore|docs`、`layer: collectors|extractors|analysis|notify|infra`、
  `iteration: 001`、`priority: high`。
- GitHub Projects 看板四列：`Backlog → Planned（本迭代）→ In Progress → Done`。
- 一个 Issue 尽量能在 1~2 天内完成；过大的先拆。

## 4. 发布流程（每个迭代结束）

1. 更新 [CHANGELOG.md](../../CHANGELOG.md)：把 Unreleased 的条目归入新版本。
2. 更新版本号（`pyproject.toml` 与 `src/opinion_radar/__init__.py` 保持一致）。
3. 确认 CI 全绿、`make lint && make test` 全绿。
4. 打标签：`git tag -a v0.2.0 -m "迭代 002：图文与付费内容"` 并推送。
5. 本机部署：`make launchd-install`（同步代码 + 重装定时任务）。
6. 观察首日日志与推送，在迭代文档补复盘。

> 0.x 阶段版本语义：MINOR 随迭代递增（0.1=000地基、0.2=001 MVP…），PATCH 用于迭代内修复。
> 模型/Prompt 的变更不算包版本，但必须在 `summaries.prompt_version` 中可追溯。

## 5. 一天中的典型动作

```bash
git switch main && git pull
git switch -c feat/xxx
# 小步开发、随时提交
make format && make lint && make test
make dry-run            # 真实配置演练
git push -u origin feat/xxx
# 开 PR → 对照清单自检 → 合并 → 删除分支
```
