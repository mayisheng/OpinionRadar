# ADR-0003：可插拔 Connector 抽象 + B站 httpx 直连

- 状态：Accepted
- 日期：2026-10-11

## 背景

首期只接 B 站，但博主信息分散在多平台（未来可能加微信公众号、雪球、微博）。
同时 B 站接入有两种实现路线：

1. 直接使用第三方库 `bilibili-api-python`（功能全、社区维护 wbi/风控逻辑）；
2. httpx 直连官方接口，自行实现签名与分页。

需要同时确定「多平台扩展性」与「B站实现方式」。

## 决策

- 定义统一抽象 `Connector`（[collectors/base.py](../../../../src/opinion_radar/collectors/base.py)）：
  `list_updates(since)` + `fetch_detail(item)`，产出跨平台统一的 `ContentItem`。
  平台特有字段一律放 `ContentItem.payload`，领域模型不出现 B 站专有名词。
- B站首期**用 httpx 直连只读接口 + 自实现 wbi 签名**，不引入第三方 B 站 SDK。
- 提取、分析、推送层只依赖 `ContentItem`，永远不感知平台存在。

选择直连的理由：

- 实际只需要极少量只读接口（UP 视频列表、字幕、后续的专栏/动态），SDK 的大部分能力用不到；
- 第三方 SDK 跟随平台风控改版有滞后风险，且其依赖与异步模型会侵入本项目的简单同步管线；
- wbi 签名逻辑稳定、资料充分，自行实现约一个模块，可控可测（配合 fixtures 录制响应）。

**替换条件**：当需要实现的接口超过 ~5 个、或自维护签名在一个月内失效两次以上时，
重新评估引入 `bilibili-api-python`（新增 ADR 记录）。

## 后果

- 迭代 006 接入新平台时只需新增一个 Connector 实现 + 配置项，管线零改动。
- 直连意味着风控对抗（签名、UA、cookie 刷新）由自己维护：必须配套限速抖动、
  退避中止策略与运维手册（[runbook/operations.md](../../runbook/operations.md)）。
- 所有 HTTP 经统一工厂创建，测试用 respx mock + 录制脱敏响应，不依赖线上接口稳定性。
