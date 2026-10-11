# AGENTS.md

本文件面向 AI 智能体，约定在 OpinionRadar 仓库内工作时必须遵守的规则。
给人看的说明见 [README.md](README.md)。

## 项目概览

- OpinionRadar：定时采集 B 站财经博主的视频、动态、评论，经本地大模型总结观点后推送到微信。
- 技术栈：Python 3 + uv + httpx + Ollama 本地模型 + SQLite + launchd 调度，架构全貌见 [docs/architecture/](docs/architecture/01-system-overview.md)。

## 文档地图（动手前先读对应文档）

- 架构与数据模型：[docs/architecture/](docs/architecture/01-system-overview.md)
- ADR 规范：[ADR-0001](docs/architecture/adr/0001-record-architecture-decisions.md)
- 迭代 SOP：[docs/product/iteration-process.md](docs/product/iteration-process.md)
- 编码 / 测试 / 工作流：[docs/development/](docs/development/coding-standards.md)
- 运维手册：[docs/runbook/](docs/runbook/operations.md)

## 技术方案自动归档（强制，无需用户提醒）

- 每次技术方案 / 架构选型讨论**定稿后**，必须立即调用 `archive-adr` 技能归档为 ADR，再进入编码。
- 触发标准（详见 ADR-0001）：新增/替换依赖、引入或拆除一层抽象、改变部署/调度方式、
  影响数据结构或安全模型的选择、关键技术路线取舍。代码排版、命名类琐事不归档。
- ADR 存放于 `docs/architecture/adr/NNNN-kebab-case.md`，编号只增不复用，**不可变**；
  决策被推翻时新增一篇 ADR，并把旧文状态标为 `Superseded by ADR-NNNN`。
- 迭代规划类文档不是 ADR，按迭代 SOP 存入 `docs/product/iterations/NNN-名称.md`。
- 归档完成后告知用户 ADR 路径与编号；**不要自动 git commit**，除非用户明确要求。
- 项目技能实体位于 `.agents/skills/`（随仓库提交）；`.trae/` 整个目录不入库，
  本地通过 `.trae/skills/<name>` 软链（指向 `../../.agents/skills/<name>`）或
  TRAE「启用 .agents 技能目录」设置来发现技能。

## 合规红线

- 不得录制、保存任何付费视频文件（无论是否已付费），仅提取文字内容用于观点分析。
- 未付费的充电专属内容直接标记为「付费不可达」，不得尝试绕过付费墙。

## 工程约定

- 常用命令：`make format`、`make lint`、`make test`、`make dry-run`。
- 提交遵循 Conventional Commits；禁止 `--no-verify` 绕过门禁；分支与单人 PR 流程见
  [开发工作流](docs/development/workflow.md)。
- 优先复用现有技术栈与 `Connector` 抽象，平台差异收敛在各 Connector 实现内，
  避免引入不必要的新依赖。
