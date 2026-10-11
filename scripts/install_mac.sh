#!/usr/bin/env bash
# OpinionRadar launchd 定时任务安装/卸载（macOS）
# 用法：
#   bash scripts/install_mac.sh install    # 安装/更新（默认）
#   bash scripts/install_mac.sh uninstall  # 卸载
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
LABELS=(com.opinionradar.collect com.opinionradar.digest)

log() { printf "\033[36m[install]\033[0m %s\n" "$*"; }

uninstall() {
  for label in "${LABELS[@]}"; do
    if launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1; then
      log "停止并卸载 $label"
      launchctl bootout "gui/$UID_NUM/$label" || true
    fi
    rm -f "$PLIST_DIR/$label.plist"
  done
}

install() {
  command -v uv >/dev/null 2>&1 || {
    echo "未找到 uv，请先执行：brew install uv" >&2
    exit 1
  }
  if [[ ! -f "$APP_DIR/config/config.yaml" ]]; then
    echo "缺少 $APP_DIR/config/config.yaml" >&2
    echo "请先执行：cp config/config.example.yaml config/config.yaml 并填写博主 UID" >&2
    exit 1
  fi

  log "同步 Python 依赖（含 audio / ocr extras）"
  (cd "$APP_DIR" && uv sync --extra audio --extra ocr)

  mkdir -p "$APP_DIR/data/logs" "$APP_DIR/data/raw" "$PLIST_DIR"

  # 先卸载旧任务，保证重复执行是幂等更新
  uninstall

  for template in "$APP_DIR"/deploy/launchd/*.plist.template; do
    name="$(basename "$template" .plist.template)"
    target="$PLIST_DIR/$name.plist"
    sed "s#__APP_DIR__#$APP_DIR#g" "$template" >"$target"
    plutil -lint "$target"
    log "加载 $name"
    launchctl bootstrap "gui/$UID_NUM" "$target"
  done

  # 立即触发一次采集，验证链路（日志见 data/logs/）
  launchctl kickstart -k "gui/$UID_NUM/com.opinionradar.collect" || true

  log "完成。常用命令："
  echo "  launchctl print gui/$UID_NUM/com.opinionradar.collect"
  echo "  tail -f $APP_DIR/data/logs/opinion_radar.log"
  echo "  卸载：bash scripts/install_mac.sh uninstall"
}

case "${1:-install}" in
  install) install ;;
  uninstall) uninstall ;;
  *)
    echo "用法: $0 [install|uninstall]" >&2
    exit 1
    ;;
esac
