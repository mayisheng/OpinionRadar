# Changelog

本项目所有用户可见变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)（0.x 阶段允许随迭代演进）。

## [Unreleased]

## [0.1.0] - 2026-10-11

### Added

- 迭代 000（仓库地基）：
  - 分层管线骨架：采集 / 存储 / 提取 / 分析 / 推送五层，定义 Connector、Extractor、Notifier 可插拔接口
  - 项目工具链：uv + Python 3.12、ruff、mypy（strict）、pytest、pre-commit、GitHub Actions CI
  - CLI：`opinion-radar doctor`（环境自检）、`run --dry-run`（管线演练）、`digest`（预留）
  - 配置体系：`config.yaml`（非敏感）+ `.env`（密钥）分离，Pydantic 校验
  - 文档体系：架构总览 / 管线 / 数据模型、4 篇 ADR、开发规范、PRD、迭代 SOP、路线图、迭代 001 详案、Mac 部署与运维手册
  - macOS launchd 定时任务模板与一键安装脚本（每小时采集、每日 digest）
