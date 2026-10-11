# 运维手册

面向「已经跑起来之后」的日常检查与故障处理。排障顺序：**doctor → 应用日志 → launchd 日志 → 外部服务**。

## 1. 日常检查

```bash
make doctor
tail -n 100 data/logs/opinion_radar.log
ls -lt data/logs/
```

正常状态：每小时 `launchd-collect.out` 有一次输出，应用日志中有完整 5 阶段记录，
手机按内容更新频率收到推送。

## 2. launchctl 常用命令

```bash
UID_NUM=$(id -u)
launchctl print gui/$UID_NUM/com.opinionradar.collect     # 查看状态/上次退出码
launchctl kickstart -k gui/$UID_NUM/com.opinionradar.collect  # 立即手动跑一轮
launchctl bootout gui/$UID_NUM/com.opinionradar.collect   # 停止单个任务
make launchd-install                                      # 重新加载全部
make launchd-uninstall
```

plist 位置：`~/Library/LaunchAgents/com.opinionradar.{collect,digest}.plist`（由模板生成，勿手改，
改模板后重新 install）。

## 3. 获取 / 更新 B站 SESSDATA（迭代 002 起需要）

1. Mac 浏览器登录 B 站 → 开发者工具 → Application/存储 → Cookies → `https://www.bilibili.com`。
2. 复制 `SESSDATA` 的值，填入 `.env` 的 `OPINION_RADAR_BILIBILI_SESSDATA`。
3. 重新跑一轮：`launchctl kickstart -k gui/$UID/com.opinionradar.collect`。

失效现象：付费内容接口返回未登录/`-101`。SESSDATA 有效期较长，但改密码、退登会使其失效，
重新获取即可。**不要把该值发给任何人、不要贴进 Issue/截图。**

## 4. B站风控应对

现象：接口返回 `-352` / `-799` / HTTP 412，或日志出现风控警告。

处置原则（代码已内置，运维侧只做观察与降级）：

- 单轮立即**中止**（不带病继续请求），等下个调度周期；
- 连续两轮触发：调大 `config.yaml` 中 `min_interval_seconds`（如 6→15）与抖动；
- 一天内反复触发：暂停任务 24~48 小时：
  `launchctl bootout gui/$(id -u)/com.opinionradar.collect`；
- 检查是否与其他 B 站脚本/浏览器插件共用账号高频访问。

绝对不要为了赶内容而调高并发或删除 sleep——个人使用，稳定与账号安全优先。

## 5. Ollama / 模型问题

| 现象 | 处理 |
|---|---|
| doctor 显示不可达 | 启动 Ollama.app；`ollama list` 检查 |
| 推理很慢、风扇大 | 改用 `qwen3:8b`（改 `config.yaml` 的 `llm.model`）；避开 digest 时段 |
| 内存压力 | 32GB 可跑 14B；其他机型按：16GB→8B、8GB→4B 量化 |
| 输出不是合法 JSON | 属模型质量问题，迭代 001 有重试；频繁出现则在 Issue 记录样例，迭代 003 优化 Prompt |
| 升级模型 | `ollama pull qwen3:14b` 拉新 tag；旧 tag `ollama rm`；先 dry-run 观察一天再常驻 |

查看 Ollama 自身日志：`~/.ollama/logs/server.log`。

## 6. 转写（Whisper）相关

- 仅在视频无字幕时触发，音频缓存在 `data/raw/.../*.m4a`；确认转写正常后可定期清理音频。
- 转写慢：确认使用的是 mlx-whisper（活动监控可见 GPU/ANE 占用）；模型默认
  `whisper-large-v3-turbo`，不建议随意换更大模型。
- 字幕接口返回空但视频明明有字幕：检查字幕语言与登录态，Issue 中附（脱敏的）接口响应。

## 7. 推送失败

- 先验证渠道本身：浏览器打开
  `https://sct.ftqq.com/<KEY>.send?title=t&desp=d`。
- Server酱免费档有频率/条数限制；`push_logs` 记录每次响应，429/限流时当天稍后会自动重推。
- 长时间收不到但日志显示成功：检查微信服务号通知免打扰、Server酱后台消息记录。

## 8. 数据与备份

- 全部状态在 `data/`：一个 SQLite + raw 文件 + 日志。
- 备份：`cp data/opinion_radar.db` 或直接让 Time Machine 覆盖项目目录。
- 重置重来：停掉 launchd → 备份后删除 `data/opinion_radar.db` 与 `data/raw/` →
  重新跑迁移（首轮只处理最近 N 条，不会轰炸推送）。

## 9. 健康自查清单（每周复盘前 2 分钟）

- [ ] doctor 全 PASS
- [ ] 过去 7 天每小时调度无大段缺失（缺失是否由休眠导致，属正常）
- [ ] 无 `status=failed` 且 `attempts` 超阈值的死信内容
- [ ] 推送主观质量：无用推送数 / 总推送数（记入迭代复盘）
- [ ] data/ 体积正常，音频缓存按需清理

## 10. 使用边界（再次提示）

仅采集本人有权访问的内容、仅供个人学习；不二次传播付费内容；
控制频率、遵守平台协议；凭证不出本机。
