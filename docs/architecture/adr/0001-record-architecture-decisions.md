# ADR-0001：使用 ADR 记录架构决策

- 状态：Accepted
- 日期：2026-10-11

## 背景

这是一个长期迭代的个人项目，技术决策（选型、抽象、部署）会随迭代不断出现。
如果决策只存在于脑子里，几个月后回看代码会不知道「为什么这样写」，
也容易在后续迭代里反复推翻已定的选择。

## 决策

采用 **ADR（Architecture Decision Record）** 记录每个有分量的架构决策：

- 存放于 `docs/architecture/adr/`，文件名 `NNNN-kebab-case.md`，编号只增不复用。
- 结构固定：**背景（Context）→ 决策（Decision）→ 影响/后果（Consequences）**，
  可附备选方案与拒绝理由。
- ADR **不可变**：决策变化时新增一篇 ADR 并把旧文状态标为 `Superseded by ADR-xxxx`，
  不删改历史。
- 状态取值：`Proposed` / `Accepted` / `Deprecated` / `Superseded by ADR-xxxx`。

什么情况值得写 ADR：新增/替换依赖、引入或拆除一层抽象、改变部署/调度方式、
影响数据结构或安全模型的选择。「代码怎么排版」「函数怎么命名」不值得写。

## 后果

- 每个迭代涉及架构时先写/更新 ADR 再写代码（见[迭代 SOP](../../product/iteration-process.md)）。
- 轻量：一篇 ADR 控制在几十行内，说清选择理由即可，不写成论文。
- Code review（含单人 PR 自检）时，架构类改动必须能链接到对应 ADR。
