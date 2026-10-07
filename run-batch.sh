#!/bin/sh
# 后台批量注册: ./run-batch.sh [数量]   数量省略则用 config.json 的 register_count
#
# 显示桌面不再硬编码 DISPLAY：由 config.display_backend 决定
#   auto   = 环境里有现成 X（如 neko 容器的 Xorg :99）直接用；没有则自动拉 Xvfb
#   native = 只用环境 DISPLAY
#   xvfb   = 代码自管 Xvfb :display_num
# 远程观看：config.display_vnc_enabled（x11vnc，默认 viewonly）
cd "$(dirname "$0")" || exit 1
COUNT="$1"
LOG_DIR=logs
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/batch-$(date +%Y%m%d-%H%M%S).log"
if [ -n "$COUNT" ]; then ARGS="cli $COUNT"; else ARGS="cli"; fi
printf 'start\n' | nohup python3 -u grok_register_ttk.py $ARGS > "$LOG" 2>&1 &
PID=$!
echo "PID: $PID"
echo "LOG: $PWD/$LOG"
echo "看进度: tail -f $LOG"
echo "优雅停止(等同 Ctrl+C): kill -INT $PID"
