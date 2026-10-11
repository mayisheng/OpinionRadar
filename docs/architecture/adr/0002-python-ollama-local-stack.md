# ADR-0002：Python + Ollama 本地技术栈

- 状态：Accepted
- 日期：2026-10-11

## 背景

核心诉求是「采集 + AI 总结」全部在本人 Mac（M1 Pro / 32GB）本地完成，
付费内容 Cookie 与原文不出本机。需要确定：实现语言、本地模型运行时、模型与转写方案。

备选：

1. **Python + Ollama（Qwen3 + mlx-whisper）**
2. Python + 直接 llama.cpp / MLX 推理（不用 Ollama）
3. Node.js/TypeScript + 本地模型
4. 直接调用云端 LLM（DeepSeek 等）

## 决策

- **语言**：Python 3.12（uv 管理环境）。爬虫、LLM、Whisper、OCR 生态最成熟，个人项目开发最快。
- **LLM 运行时**：**Ollama**（本机已装）。Metal 加速、模型拉取/切换一条命令、
  原生提供 **OpenAI 兼容接口**（`http://localhost:11434/v1`），客户端代码与云端模型完全同构。
- **主模型**：**Qwen3-14B（Q4_K_M，约 9GB）**。中文财经文本理解与 JSON 结构化能力、
  推理速度在 32GB 内存上平衡最好；高频/长文本场景配置切换 Qwen3-8B。
- **语音转写**：B站官方/AI 字幕优先（免费且快）；缺失时 yt-dlp 取音频 +
  **mlx-whisper large-v3-turbo**（Apple Silicon 原生优化）。
- **OCR**：RapidOCR（PaddleOCR 的 ONNX 发行版，CPU 可跑，安装简单）。
- 模型通过 [pyproject optional-dependencies](../../../../pyproject.toml) 的 extras 管理（`audio`、`ocr`），
  核心依赖保持轻量。

## 后果

- LLM 访问统一走 OpenAI SDK，只改 `base_url/model/api_key` 即可在本地与云端间切换；
  迭代 005 可低成本加「本地失败走云端兜底」。
- 不依赖云服务、无 API 费用、无隐私外泄；代价是摘要质量上限受 14B 模型约束，
  用 Prompt 工程 + schema 校验 + 重试弥补，必要时对单条长文手动触发云端分析。
- 直接绑定 Ollama 进程作为外部依赖：`doctor` 命令负责探测其健康状态，runbook 覆盖启停排查。
- 拒绝直接 llama.cpp/MLX：需要自己管理模型加载、服务化与并发，收益只是省一层进程，不划算。
- 拒绝 Node.js：爬虫/转写/OCR 生态弱于 Python，没有抵消切换成本的前端需求。
