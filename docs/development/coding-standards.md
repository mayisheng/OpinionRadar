# 编码规范

工具能自动管的全部交给工具（ruff / mypy / pre-commit），本文只写工具管不了的约定。

## 1. 风格与格式

- 格式化与静态检查：ruff，行长 100，配置见 [pyproject.toml](../../../pyproject.toml)。
  规则集：`E/F/I/UP/B/SIM/RET/RUF`（中文标点的 RUF001-003 已按中文项目关闭）。
- 命令：`make format`（格式化+安全修复）、`make lint`（检查 + mypy strict）。
- docstring 与注释用**中文**；标识符、异常消息、日志事件名用英文。

## 2. 类型

- mypy **strict** 模式：所有函数参数与返回值必须标注；公开模型必须显式类型。
- 优先使用容器泛型（`list[str]`、`dict[str, Any]`）与 `X | None`，不使用 `Optional` 别名。
- 边界处（外部 JSON、配置、LLM 输出）必须是 Pydantic 模型，不允许未校验的 dict 流入业务层。

## 3. 分层规则（最重要）

```text
domain/      纯数据，不 import 任何业务层与 IO 库（httpx/sqlalchemy/openai）
collectors/ 只产出 domain 模型，不直接写库、不调模型、不推送
extractors/ 输入输出都是 domain 模型，不关心平台与推送
analysis/    只读 body_text，产出 schema 结果；不发起平台请求
notifiers/   只负责把消息送到渠道，不生成业务逻辑
pipeline.py  唯一允许编排各层、写库、推进状态的地方
cli.py       只做参数解析、配置加载、退出码；不写业务逻辑
```

- 配置只能来自 `config.yaml` / `.env`，禁止代码里硬编码 UID、Key、URL（接口常量除外）。
- 密钥类变量只能出现在 `Secrets` 模型与显式传参路径上，禁止打印 / 写日志 / 落库。

## 4. 错误处理

- 外部 IO 必须设置超时；网络错误用统一的 `http_retry`（指数退避 + 抖动）。
- 预期外的平台响应（风控码、结构变化）抛项目内自定义异常（在实现迭代中定义，
  如 `collectors/exceptions.py`），管线捕获后置 `failed` 状态，不能让整轮崩溃。
- 不用裸 `except:`；`except Exception` 只允许出现在管线边界，并必须记录上下文。

## 5. 日志

- 一律 `from loguru import logger`；级别约定：
  `debug` 请求细节、`info` 阶段进展与结果、`warning` 可恢复异常（重试/风控中止）、
  `error` 本轮失败。
- 日志要能回答「哪个博主、哪条内容、哪一步、结果如何」，但不得包含 Cookie/Key。
- 用户侧结论（成功推送了什么）以 CLI 输出和推送消息为准，日志面向排查。

## 6. 依赖管理

- 新依赖先想清楚是否真的需要；重能力（yt-dlp、mlx-whisper、rapidocr）放 optional extras，
  按需安装、在代码里 lazy import，避免核心环境臃肿。
- 锁定文件 `uv.lock` **必须提交**；升级依赖走单独的 `chore:` 提交与 PR。
- 标准库能解决的（如 argparse 除外的 CLI 用 typer）不引三方库。

## 7. 命名

| 类型 | 约定 | 示例 |
|---|---|---|
| 类 | PascalCase | `BilibiliConnector` |
| 函数/变量 | snake_case | `list_updates` |
| 常量 | UPPER_SNAKE | `DEFAULT_CONFIG_PATH` |
| 布尔 | is_/has_ 前缀 | `is_paid` |
| 枚举值 | 小写字符串 | `paid_article` |
| 配置键 | snake_case（yaml 中同名） | `min_interval_seconds` |

## 8. TODO 规范

骨架阶段用 `TODO(迭代 NNN): 描述` 标注未实现点，便于全局检索：

```bash
rg "TODO\(迭代" src/
```

不允许出现无主、无迭代归属的 TODO。
