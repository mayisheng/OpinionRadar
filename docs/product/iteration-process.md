# 迭代流程 SOP

固定节奏（建议**每周一个迭代**，业务忙时可两周，但流程不变）。一个迭代闭环 9 步：

```text
需求收集 → 迭代规划 → 方案设计 → 建分支开发 → 本地验证
         → PR 自检合并 → 发布部署 → 运行观察 → 复盘沉淀
```

## 1. 需求收集（贯穿，随时）

- 想法、痛点、推送质量问题随手开 GitHub Issue（用 feature / bug 模板），进 `Backlog`。
- 来源：日常使用观察、推送质量回看、失败日志。

## 2. 迭代规划（迭代第一天）

- 从 Backlog 选 3~6 个高价值 Issue 拖入 `Planned`，打 `iteration: NNN` 标签。
- 复制下方模板创建 `docs/product/iterations/NNN-名称.md`，写清**目标、范围、验收标准**。
- 范围控制：宁可一个迭代完整交付一小块，也不要堆满半成品。

**进入迭代的条件（DoR）**：目标可验证；范围与非范围明确；涉及外部接口的已确认可行（录到样例响应）。

## 3. 方案设计

- 纯业务实现直接进开发；凡满足以下任一条件，**先写 ADR**：
  新增/替换依赖、引入/拆除抽象、改部署方式、改数据结构或安全模型。
- ADR 编号取下一个号，状态 `Accepted`（见 [ADR-0001](../architecture/adr/0001-record-architecture-decisions.md)）。

## 4. 建分支开发

- 从最新 main 切 `feat/fix/docs/chore/*` 短分支，一个 PR 对应 1~3 个 Issue。
- 小步提交（Conventional Commits），随时 `make format && make lint`。
- 遵循[编码规范](../development/coding-standards.md)与[测试指南](../development/testing.md)。

## 5. 本地验证

- `make lint && make test` 全绿；新逻辑带测试。
- 涉及外部行为：`make doctor` + `make dry-run`，必要时真跑一轮，结果写进 PR。

## 6. PR 自检与合并

- 推送分支开 PR，填 [PR 模板](../../../.github/PULL_REQUEST_TEMPLATE.md)，逐条勾选。
- CI（ruff/mypy/pytest）全绿；以整体 diff 视角自审一遍再 **Squash merge**。
- Issue 拖入 `Done`，删除已合并分支。

**完成的定义（DoD）**：CI 绿 + 测试覆盖新逻辑 + dry-run/真实验证 + 文档同步（CHANGELOG/runbook/ADR）。

## 7. 发布部署

- 更新 CHANGELOG 与版本号，打 tag（流程见[开发工作流](../development/workflow.md#4-发布流程每个迭代结束)）。
- `make launchd-install` 更新本机定时任务；若含数据库变更先备份 `data/` 再 `alembic upgrade head`。

## 8. 运行观察（迭代进行中持续）

- 看 `data/logs/opinion_radar.log` 与 launchd 日志，确认前 24 小时调度正常。
- 手机上收到推送后随手判断质量；问题直接开 Issue 进 Backlog。

## 9. 复盘沉淀（迭代最后一天，15 分钟）

在迭代文档补全 `结果 / 指标 / 问题 / 改进动作`：哪些验收没达成、踩了什么坑、
runbook/规范要不要更新、roadmap 要不要调整。改进动作落成 Backlog Issue，闭环。

---

## 迭代文档模板

```markdown
# 迭代 NNN：标题

- 状态：Planned | In Progress | Done
- 日期：YYYY-MM-DD ~ YYYY-MM-DD
- 关联 Issue：#12, #13

## 目标
（一句话，迭代结束后能被验证）

## 范围
- In：
- Out（本迭代明确不做）：

## 验收标准
- [ ] 可验证标准 1
- [ ] 可验证标准 2

## 方案要点 / ADR
-

## 结果（复盘填写）
- 完成情况：
- 指标/观察：
- 问题与坑：
- 改进动作（→ Issue 链接）：
```
