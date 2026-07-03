#!/usr/bin/env bash
# heige-agent-report uninstaller：移除各 agent 的通知钩子，还原配置。
set -euo pipefail

INSTALL_DIR="${HEIGE_AGENT_REPORT_HOME:-$HOME/.heige-agent-report}"
CONFIG_PATH="$INSTALL_DIR/config.json"
CLAUDE_DIR="$HOME/.claude"
CODEX_CONFIG="$HOME/.codex/config.toml"
KEEP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --claude-config) CLAUDE_DIR="$2"; shift 2;;
    --codex-config) CODEX_CONFIG="$2"; shift 2;;
    --keep-files) KEEP=1; shift;;
    -h|--help) echo "用法: bash uninstall.sh [--claude-config DIR] [--codex-config PATH] [--keep-files]"; exit 0;;
    *) echo "未知参数: $1" >&2; exit 2;;
  esac
done

PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"
WIRE="$INSTALL_DIR/wire.py"
[[ -f "$WIRE" ]] || WIRE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/wire.py"

"$PYTHON_BIN" "$WIRE" unclaude --claude-config "$CLAUDE_DIR" >/dev/null 2>&1 && echo "已移除 Claude Code Stop hook" || true
"$PYTHON_BIN" "$WIRE" uncodex --codex-config "$CODEX_CONFIG" --app-config "$CONFIG_PATH" >/dev/null 2>&1 && echo "已还原 Codex notify" || true

if [[ "$KEEP" -eq 0 ]]; then
  rm -rf "$INSTALL_DIR" && echo "已删除 $INSTALL_DIR"
else
  echo "保留 $INSTALL_DIR"
fi
echo "卸载完成。新开会话生效。"
