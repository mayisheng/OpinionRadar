# 文档索引

文档与代码同仓库演进（docs-as-code），所有文档变更随 PR 一起评审。

## 产品（做什么、为什么、怎么迭代）

- [产品需求文档 PRD](product/prd.md)
- [迭代流程 SOP](product/iteration-process.md) — 每个迭代固定走的 9 个步骤
- [路线图](product/roadmap.md) — 迭代 000~006 的目标与退出标准
- [迭代 001：MVP 详案](product/iterations/001-mvp.md)

## 架构（怎么实现、为什么这样设计）

- [01 · 系统总览](architecture/01-system-overview.md)
- [02 · 处理管线](architecture/02-pipeline.md)
- [03 · 数据模型](architecture/03-data-model.md)
- [ADR 目录](architecture/adr/0001-record-architecture-decisions.md) — 重大技术决策的不可变记录

## 开发（怎么写代码、怎么协作）

- [开发工作流](development/workflow.md) — 分支、提交、PR、发布
- [编码规范](development/coding-standards.md)
- [测试指南](development/testing.md)

## 运维（怎么跑起来、出问题怎么办）

- [Mac 环境搭建](runbook/local-setup-mac.md)
- [运维手册](runbook/operations.md)

## 文档约定

| 目录 | 何时更新 |
|---|---|
| `docs/product/` | 每次迭代规划与复盘时 |
| `docs/architecture/` | 架构变化前先写/更新 ADR，再改代码 |
| `docs/development/` | 规范调整时 |
| `docs/runbook/` | 部署或处理故障后沉淀经验时 |

任务的事实来源是 **GitHub Issues + Projects 看板**；文档负责沉淀目标、决策、验收与复盘，
不要在文档里维护会与看板重复的任务勾选清单。
