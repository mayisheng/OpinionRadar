# 测试指南

## 1. 测试金字塔与目录

```text
tests/
├── unit/          # 纯逻辑：schema 校验、状态推进、模板渲染、去重算法
├── integration/   # 跨层：管线编排（外部 HTTP 全部 mock）、SQLite 真库迁移
└── fixtures/      # 脱敏的接口样例响应、字幕样例、LLM JSON 样例
```

- 单元测试为主，追求毫秒级；集成测试每个迭代新增少量关键路径即可。
- 不追求花哨覆盖率数字，标准是：**每个曾经出过 bug 的地方都有测试**、
  **每个外部边界（平台接口、LLM JSON、推送响应）都有样例约束**。

## 2. 怎么写

- 框架：pytest；夹具放 `conftest.py`；测试函数 `test_行为_条件_预期`。
- HTTP 边界一律用 [respx](https://github.com/lundberg/respx) mock httpx，
  样例响应放 `tests/fixtures/`（提交前脱敏：UID 改 123456、删除 cookie 字段）。
- 时间相关用 freezegun；临时目录用 pytest 自带 `tmp_path`。
- 数据库测试用真 SQLite（`sqlite://` 内存或 tmp_path 文件），不 mock SQLAlchemy。
- 真打本机 Ollama / B站的测试默认跳过，用 `@pytest.mark.live` 标记，仅本地显式
  `pytest -m live` 运行（在 pyproject 中注册 marker）。

## 3. 各层测试重点

| 层 | 重点 |
|---|---|
| collectors | wbi 签名正确性（样例对拍）、分页停止条件、风控响应中止、限速不低于配置下限 |
| storage | 唯一键 upsert 幂等、状态机非法跃迁被拒、迁移 upgrade/downgrade 可执行 |
| extractors | 字幕缺失时回退转写、正文清洗不丢关键段落、OCR 文本拼接顺序 |
| analysis | LLM 返回非法 JSON 时重试/报错、AnalysisResult schema 边界、prompt 版本落库 |
| notifiers | 成功/失败响应解析、不重复推送（幂等）、超长内容切分 |
| pipeline | 一条样例内容走通五阶段（全 mock）、单条失败不阻塞同轮其他内容 |

## 4. 手工验证（每次 PR 必做）

自动化测试之外，涉及外部行为的改动必须做真实环境演练：

```bash
make doctor          # Ollama/配置健康
make dry-run         # 管线计划符合预期配置
# 需要真跑时（迭代开发中）：
uv run opinion-radar run
```

PR 描述中写清「如何验证」（命令 + 观察到的结果），不接受只写「已自测」。

## 5. 运行

```bash
make test                     # 全量（跳过 live）
uv run pytest tests/unit -k connector -x
uv run pytest -m live         # 含本机真实依赖
```
