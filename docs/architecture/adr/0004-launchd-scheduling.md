# ADR-0004：使用 launchd 调度短生命周期 CLI

- 状态：Accepted
- 日期：2026-10-11

## 背景

运行环境是一台常开的 MacBook Pro，需要「每小时增量采集 + 每日固定时间 digest」。备选：

1. **launchd 定时拉起 CLI 短任务**（macOS 原生）
2. crontab
3. 常驻 Python 进程 + APScheduler
4. GitHub Actions 云端定时任务

## 决策

采用方案 1：launchd 加载两个 plist（模板见 [deploy/launchd](../../../../deploy/launchd)），
分别定时执行 `opinion-radar run` 与 `opinion-radar digest`。安装/更新由
[scripts/install_mac.sh](../../../../scripts/install_mac.sh) 一键完成。

理由：

- launchd 是 macOS 官方方案，`RunAtLoad` 开机/登录后自动生效，无需额外常驻进程；
- 每次调度都是全新进程：**崩溃不会污染下次运行**，内存（尤其 Whisper/模型）天然释放；
- 休眠期间错过的执行无需补偿——管线本身是增量的，唤醒后下一轮会把漏掉的内容拉回来；
- plist 显式声明 PATH、工作目录与日志路径，排查入口清晰。

拒绝其他方案：

- **cron**：macOS 上已不推荐，休眠唤醒行为与环境变量处理更差。
- **APScheduler 常驻**：要自己解决进程守护、崩溃重启、内存泄漏；与「简单个人工具」不匹配。
- **GitHub Actions**：付费内容 Cookie 需要放云端 Secrets，违背数据不出本机的前提，
  且 schedule 触发不精确、对本地 Ollama 不可达。

## 后果

- 管线必须支持「任意时刻重复执行而结果正确」（幂等），这已在存储层设计中保证。
- 单轮任务耗时不能超过调度间隔：视频转写最重，靠「字幕优先 + 单并发 + 每轮限量」控制；
  若未来博主增多导致超时，改为常驻方案前先评估分批游标。
- 部署文档必须覆盖 plist 加载、`launchctl print` 排查、卸载流程（见 runbook）。
