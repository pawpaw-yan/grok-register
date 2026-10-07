#!/usr/bin/env bash
# grok-register launcher (Linux / A1). Usage:
#   ./register.sh                main TUI menu
#   ./register.sh run [count]    start CLI registration directly
#   ./register.sh show           show full config (masked)
#   ./register.sh base|mail|proxy|local|remote|cpa    jump into one module
cd "$(dirname "$0")" || exit 1
# 用实际运行检测而不是 command -v：绕开 Windows 商店的 python3 占位 stub
if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
    exec python3 config_tui.py "$@"
fi
if command -v python >/dev/null 2>&1 && python -c '' >/dev/null 2>&1; then
    exec python config_tui.py "$@"
fi
echo "[ERROR] python3 not found" >&2
exit 1
