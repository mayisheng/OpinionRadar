# OpinionRadar 仓库基础设施与开发体系实施计划

> 项目：定时采集 B 站财经博主内容（付费图文 / 视频 / 行情观点）→ 本地模型识别总结 → 微信推送到手机
> 范围：本计划只做「架构落地 + 开发规范 + 迭代体系」的地基（对应迭代 000），不含具体业务逻辑（业务从迭代 001 开始）

## 一、仓库调研结论

* 仓库为全新空仓库：仅有 `main` 分支、无任何文件、工作区干净。

* 运行环境：MacBook Pro **M1 Pro / 32GB**，macOS 本地常开。

* 已装：Ollama 0.40.2（OpenAI 兼容接口 `http://localhost:11434`，暂无模型）、Homebrew。

* 系统 Python 3.9.6 过旧，需引入独立 Python 3.12 环境。

* 已确认决策：

  * 技术栈：Python（本地模型方案，见 ADR-0001）

  * 运行方式：本地 Mac 常开 + **launchd** 调度

  * 推送渠道：**Server酱 / PushPlus**（微信接收），Notifier 做接口抽象，预留 Bark / Telegram / 飞书

  * 数据源：首期只做 B 站，采集层设计为**可插拔 Connector**，预留多平台扩展

### 本地模型选型（针对 M1 Pro 32GB）

| 用途        | 选型                                            | 说明                              |
| --------- | --------------------------------------------- | ------------------------------- |
| LLM 运行时   | Ollama（已安装）                                   | Metal 加速、模型管理简单、OpenAI 兼容 API   |
| 主模型       | **Qwen3-14B**（Q4\_K\_M，约 9GB）                 | 中文好、财经总结与 JSON 结构化抽取质量/速度平衡     |
| 轻量备选      | Qwen3-8B（约 5GB）                               | 高频次任务提速，按配置可切换                  |
| 视频语音转写    | B站字幕 API 优先；兜底 **mlx-whisper large-v3-turbo** | Apple Silicon 原生优化；仅无字幕时才下载音频转写 |
| 图片文字      | RapidOCR（PaddleOCR ONNX 版）                    | CPU 即可，用于图文动态截图 OCR             |
| Python 环境 | **uv** 管理 Python 3.12 + 依赖                    | 替代 pip/poetry，速度快、锁文件可复现        |

LLM 客户端统一走 **OpenAI 兼容协议**，可随时切换云端模型（DeepSeek 等）兜底，本地模型失效不阻塞管线。

## 二、总体架构

### 2.1 管线分层（单向数据流，每一层可独立测试/替换）

```
┌─────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐   ┌────────────┐
│ 采集层       │→ │ 规范化/去重    │→ │ 内容提取层      │→ │ AI 分析层      │→ │ 汇总/推送   │
│ Collectors  │   │ Storage(SQLite)│  │ Extractors    │   │ Analysis(LLM) │   │ Notifiers  │
│ B站 Connector│   │ 幂等/状态机    │   │ 字幕/Whisper/  │   │ 识别·观点抽取 │   │ Server酱   │
│ 轮询/限速/鉴权│   │               │   │ 正文清洗/OCR   │   │ 摘要·结构化    │   │ digest 聚合 │
└─────────────┘   └──────────────┘   └───────────────┘   └──────────────┘   └─────┬──────┘
        ↑                                                                         ↓
  config/.env                                                              推送日志/失败重试
  (博主UID/Cookie/密钥)                                                          push_logs
                                          ↓
                              launchd 定时触发 CLI（每小时采集；每日 digest）
```

1. **采集层 collectors**：插件式 `Connector` 接口。B 站实现负责视频/专栏/动态/充电专属列表轮询、wbi 签名、SESSDATA 鉴权、UA、限速抖动与风控退避，产出统一 `ContentItem`。
2. **存储层 storage**：SQLite + Alembic 迁移。表：`sources`、`content_items`、`processing_state`、`summaries`、`push_logs`；以 `source + source_id` 幂等，记录每条内容处理状态机（discovered→fetched→analyzed→pushed）。
3. **提取层 extractors**：视频走官方/AI 字幕，缺失时 yt-dlp 取音频 + mlx-whisper；专栏 HTML 正文清洗；图文动态做图片 OCR。
4. **分析层 analysis**：OpenAI 兼容客户端（默认 Ollama）；Prompt 文件版本化；输出用 Pydantic 校验（内容分类 / 观点列表：标的、方向、逻辑、依据、风险、时间点 / 摘要），失败重试。
5. **汇总推送 digest/notifiers**：单条即时推送 + 每日摘要两种模式；`Notifier` 接口，首期 Server酱实现；消息模板渲染、超长切分、重试、幂等。
6. **调度 scheduler**：launchd 定时执行 `opinion-radar run --once`（采集）与 `opinion-radar digest --daily`（日报），比常驻进程更简单、崩溃自动拉起。
7. **基础设施**：typer CLI、pydantic-settings（yaml + .env）、loguru 日志按日轮转、统一 http/retry 工具。

### 2.2 目标目录结构（本次创建）

```text
OpinionRadar/
├── README.md                         # 项目说明 + 快速开始
├── CHANGELOG.md                      # 版本变更记录（Keep a Changelog）
├── pyproject.toml                    # uv 管理的依赖与工具配置
├── Makefile                          # lint/test/run 常用命令
├── .env.example                      # 密钥模板（真实 .env 不入库）
├── .gitignore
├── .pre-commit-config.yaml
├── config/
│   └── config.example.yaml           # 博主UID/调度/模型/推送配置示例
├── .github/
│   ├── ISSUE_TEMPLATE/ (bug.yml, feature.yml)
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/ci.yml              # ruff + mypy + pytest
├── docs/
│   ├── README.md                     # 文档索引
│   ├── architecture/
│   │   ├── 01-system-overview.md     # 架构总览（含分层图）
│   │   ├── 02-pipeline.md            # 采集→推送管线详述
│   │   ├── 03-data-model.md          # SQLite 表结构与状态机
│   │   └── adr/
│   │       ├── 0001-record-architecture-decisions.md
│   │       ├── 0002-python-ollama-local-stack.md
│   │       ├── 0003-pluggable-connector.md
│   │       └── 0004-launchd-scheduling.md
│   ├── development/
│   │   ├── workflow.md               # 分支/提交/PR/发布规范
│   │   ├── coding-standards.md       # Python 代码规范
│   │   └── testing.md                # 测试策略
│   ├── product/
│   │   ├── prd.md                    # 轻量产品需求文档
│   │   ├── iteration-process.md      # 迭代 SOP（完整迭代流程）
│   │   ├── roadmap.md                # 迭代路线图 001~006
│   │   └── iterations/
│   │       └── 001-mvp.md            # 首个业务迭代详案
│   └── runbook/
│       ├── local-setup-mac.md        # Mac 环境搭建（uv/Ollama/模型/launchd）
│       └── operations.md             # 日志、风控、故障排查
├── src/opinion_radar/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                        # typer: version / run / digest / doctor
│   ├── config.py                     # 配置加载（yaml + .env）
│   ├── domain/models.py              # ContentItem/Viewpoint/Summary 等领域模型
│   ├── collectors/base.py            # Connector 抽象接口
│   ├── collectors/bilibili.py        # B站骨架（仅接口与 TODO）
│   ├── extractors/base.py            # Extractor 抽象接口
│   ├── analysis/
│   │   ├── llm_client.py             # OpenAI 兼容客户端骨架
│   │   ├── schemas.py                # LLM 结构化输出 schema
│   │   └── prompts/.gitkeep
│   ├── digest/service.py
│   ├── notifiers/base.py             # Notifier 抽象接口
│   ├── notifiers/serverchan.py       # Server酱骨架
│   ├── storage/
│   │   ├── database.py               # 引擎/会话骨架
│   │   └── migrations/.gitkeep
│   ├── pipeline.py                   # 管线编排骨架
│   └── utils/(logging.py, http.py, retry.py)
├── tests/
│   ├── conftest.py
│   ├── unit/test_smoke.py            # 冒烟测试（配置加载、CLI）
│   ├── integration/.gitkeep
│   └── fixtures/.gitkeep
├── scripts/
│   └── install_mac.sh                # 一键安装/更新本机 launchd 任务
└── deploy/launchd/
    ├── com.opinionradar.collect.plist.template
    └── com.opinionradar.digest.plist.template
```

骨架原则：**抽象接口 + 文档字符串 + TODO 指针（指向对应迭代）**，保证可运行、可 lint、可测试，但不提前写业务逻辑。

## 三、开发规范与文档体系（需求 2）

### 3.1 文档沉淀（docs-as-code，随代码一起评审演进）

| 目录                   | 内容                                | 何时更新        |
| -------------------- | --------------------------------- | ----------- |
| `docs/product/`      | PRD、roadmap、每次迭代记录、迭代 SOP         | 每次迭代规划/复盘时  |
| `docs/architecture/` | 架构总览、管线、数据模型、**ADR**（重大决策不可变编号文件） | 架构变化时先写 ADR |
| `docs/development/`  | Git 工作流、编码规范、测试指南                 | 规范调整时       |
| `docs/runbook/`      | Mac 环境搭建、运维与故障排查                  | 部署/故障经验沉淀时  |
| `CHANGELOG.md`       | 每个版本的用户可见变更                       | 发版时         |

任务事实来源用 **GitHub Issues + Projects 看板**；迭代文档只沉淀目标、决策、验收与复盘，避免双份任务清单。

### 3.2 Git 与质量规范（详见 docs/development/workflow\.md）

* 分支模型：**主干开发**——`main` 始终可发布；短分支 `feat/*`、`fix/*`、`docs/*`、`chore/*`。

* 单人开发也走 **PR 自检清单**后合并（保留评审痕迹与 CI 记录），紧急修复可直合后补 PR。

* 提交信息：**Conventional Commits**（`feat: 新增视频字幕转写` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`）。

* 质量门禁：pre-commit 执行 ruff format + ruff lint + mypy；CI 执行 ruff/mypy/pytest；新功能须带测试。

* 版本：语义化版本 `vMAJOR.MINOR.PATCH`（0.x 阶段允许迭代演进），打 tag + 更新 CHANGELOG + 部署本机。

* 密钥：`.env` 永不入库（.gitignore + 日志脱敏），仓库只提供 `.env.example`。

## 四、完整迭代流程（需求 3，详见 docs/product/iteration-process.md）

固定周期（建议每周一个迭代），9 个步骤闭环：

1. **需求收集**：日常想法/问题记入 GitHub Issues（带标签）。
2. **迭代规划**：从看板选 Issue，新建 `docs/product/iterations/NNN-xxx.md`，写目标、范围、验收标准。
3. **方案设计**：涉及架构/选型的改动先写 ADR（编号递增）。
4. **建分支开发**：从 main 切短分支，小步按 Conventional Commits 提交。
5. **本地验证**：pytest + `--dry-run` 烟测（不推送/不写库的演练模式）。
6. **PR 自检**：按 PR 模板清单检查，CI 全绿后合并 main。
7. **发布部署**：打 tag、更新 CHANGELOG、`scripts/install_mac.sh` 更新本机 launchd。
8. **运行观察**：查看日志、抽查推送质量；管线失败通过 Server酱发告警。
9. **复盘沉淀**：在迭代文档补「结果 / 问题 / 改进」，更新 roadmap，进入下一迭代。

### 路线图（roadmap.md 详版）

| 迭代        | 目标           | 主要内容                                                                     |
| --------- | ------------ | ------------------------------------------------------------------------ |
| **000**   | 地基（本计划）      | 骨架、规范、文档体系、CI、launchd 模板                                                 |
| 001 MVP   | 跑通一条链路       | 单博主视频增量采集 → B站字幕（兜底 Whisper）→ Ollama Qwen 摘要 → Server酱单条推送 → launchd 每小时 |
| 002 图文与付费 | 覆盖内容类型       | 专栏、图文动态、充电专属（Cookie 鉴权）、图片 OCR                                           |
| 003 观点结构化 | 提升信息密度       | 标的/方向/逻辑/风险 JSON 抽取、跨内容观点去重、Prompt 版本管理                                  |
| 004 日报与体验 | 聚合推送         | 每日 digest、推送模板、多博主配置、质量回看标记                                              |
| 005 健壮性   | 稳定运行         | 风控退避、失败告警、运行统计、云端模型兜底开关                                                  |
| 006 多平台   | Connector 扩展 | 接入微信公众号/雪球/微博等（验证插件式设计）                                                  |

## 五、实施步骤（依赖排序）

1. **工具链**：`brew install uv`；`uv init` 生成 Python 3.12 项目；`pyproject.toml` 声明依赖（httpx、pydantic、pydantic-settings、typer、loguru、sqlalchemy、alembic、tenacity、pyyaml、yt-dlp；dev：ruff、mypy、pytest、pre-commit、freezegun）。
2. **包骨架**：创建 `src/opinion_radar/` 全部目录；领域模型、配置加载、日志工具、统一 http/retry；CLI 提供 `version / doctor / run --dry-run / digest`（dry-run 仅校验配置并打印管线计划，不产生外部调用）。
3. **接口骨架**：Connector / Extractor / Notifier 抽象基类，Bilibili 与 Server酱 的最小实现骨架（`NotImplementedError` + 指向迭代编号的 TODO）；LLM 客户端骨架（读取 Ollama base\_url/model 配置）。
4. **测试骨架**：pytest 配置、conftest、配置加载与 CLI 的冒烟测试。
5. **质量工具**：ruff/mypy 配置写入 pyproject；`.pre-commit-config.yaml`；Makefile（install/lint/format/test/run/dry-run）。
6. **CI**：`.github/workflows/ci.yml`（3.12 上 lint + pytest），Issue/PR 模板。
7. **文档**：按第二节目录写全部文档（架构 3 篇 + 4 个 ADR、开发规范 3 篇、产品 PRD/迭代 SOP/roadmap/001 详案、runbook 2 篇、docs 索引、README、CHANGELOG）。
8. **部署模板**：`config/config.example.yaml`、`.env.example`、两个 launchd plist 模板（每小时采集 + 每日 digest）、`install_mac.sh`（渲染模板、加载/更新 launchd）。
9. **本地验证**（见下节）。
10. 提交：拆为 3 个提交——`chore: 项目骨架与工具链`、`docs: 架构与开发规范文档`、`feat(deploy): launchd 调度模板与安装脚本`。

## 六、依赖与注意事项

* `uv` 通过 Homebrew 安装；Python 3.12 由 uv 自动下载管理，不污染系统 Python。

* Ollama 模型（Qwen3-14B 约 9GB）**不在本计划下载**，在 runbook 中写明 `ollama pull` 步骤，于迭代 001 验证期拉取。

* B 站访问首期采用 httpx 直连 + wbi 签名（只读、接口少、依赖风险低）；ADR-0003 记录「不直接绑定 bilibili-api 第三方库」的理由与替换条件。

* 音频下载用 yt-dlp（按需调用、不进常驻依赖路径问题通过 lazy import 缓解）。

* 所有外部调用（B站、Ollama、Server酱）经统一 http 工具加超时与重试。

## 七、验证方式

* `uv sync` 成功，环境为 Python 3.12。

* `make lint`（ruff + mypy）零错误；`make test` 冒烟测试通过。

* `opinion-radar --help`、`opinion-radar doctor`、`opinion-radar run --dry-run` 正常输出。

* `plutil -lint deploy/launchd/*.plist.template`（去模板变量后）语法合法。

* pre-commit 在提交时正常触发。

* 文档目录结构与第二节一致，相对链接人工通读无死链。

## 八、风险与应对

* **B 站风控/封号**：单账号只读、请求间隔加随机抖动、指数退避、单博主低频率；Cookie 仅存本机；runbook 写明降级策略。

* **付费内容合规**：只采集本人账号已购买内容、仅供个人学习、推送不做二次传播；README/runbook 加使用边界声明。

* **本地模型抽取质量不稳定**：Pydantic 校验 + 重试 + few-shot prompt；LLM 走 OpenAI 协议保留云端兜底开关（迭代 005）。

* **Whisper 占资源/耗时**：字幕优先、转写限并发 1、仅对未处理内容触发；M1 Pro 32GB 可承受。

* **密钥泄露**：`.env` 不入库、日志脱敏、CI 不依赖任何真实密钥。

* **launchd 环境坑**（PATH、网络、休眠唤醒）：plist 显式声明 PATH 与日志路径；runbook 覆盖 `launchctl print` 排查；休眠错过的任务由下次轮询的增量机制自然补回。

