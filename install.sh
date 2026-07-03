#!/usr/bin/env bash
# heige-agent-report installer (macOS / Linux)
# 把「agent 完成任务 → 飞书汇报」的钩子装到 Claude Code 和 / 或 Codex。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HEIGE_AGENT_REPORT_HOME:-$HOME/.heige-agent-report}"
CONFIG_PATH="$INSTALL_DIR/config.json"

OPEN_ID=""
IDENTITY="bot"
MIN_SECONDS="45"
LARK_CLI="lark-cli"
AGENTS="claude,codex"
CLAUDE_DIR="$HOME/.claude"
CODEX_CONFIG="$HOME/.codex/config.toml"

usage() {
  cat <<'USAGE'
用法: bash install.sh [选项]
  --open-id <ou_xxx>     接收通知的飞书 open_id（不填则尝试从 lark-cli 自动读取）
  --identity <bot|user>  发送身份，默认 bot
  --min-seconds <N>      Claude Code 单轮耗时闸门（秒），默认 45，0 = 每轮都推
  --lark-cli <path>      lark-cli 可执行路径，默认 lark-cli
  --agents <list>        要装的 agent，逗号分隔，默认 claude,codex（可只写其一）
  --claude-config <dir>  Claude 配置目录，默认 ~/.claude
  --codex-config <path>  Codex 配置文件，默认 ~/.codex/config.toml
  -h, --help             显示帮助
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --open-id) OPEN_ID="$2"; shift 2;;
    --identity) IDENTITY="$2"; shift 2;;
    --min-seconds) MIN_SECONDS="$2"; shift 2;;
    --lark-cli) LARK_CLI="$2"; shift 2;;
    --agents) AGENTS="$2"; shift 2;;
    --claude-config) CLAUDE_DIR="$2"; shift 2;;
    --codex-config) CODEX_CONFIG="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "未知参数: $1" >&2; usage; exit 2;;
  esac
done

PYTHON_BIN="$(command -v python3 || true)"
[[ -z "$PYTHON_BIN" ]] && { echo "需要 python3，请先安装。" >&2; exit 1; }

# 1) 前置检查：lark-cli
if ! command -v "$LARK_CLI" >/dev/null 2>&1 && [[ ! -x "$LARK_CLI" ]]; then
  echo "未找到 lark-cli（飞书官方 CLI）。请先安装并登录：" >&2
  echo "  npm i -g @larksuite/cli && lark-cli auth login" >&2
  exit 1
fi

# 2) 解析 open_id（未指定则从 lark-cli 自动读取当前登录用户）
if [[ -z "$OPEN_ID" ]]; then
  OPEN_ID="$("$LARK_CLI" auth status 2>/dev/null | "$PYTHON_BIN" -c \
    'import sys,json;
try:
  d=json.load(sys.stdin); print(d.get("identities",{}).get("user",{}).get("openId","") or "")
except Exception: print("")' 2>/dev/null || true)"
fi
if [[ -z "$OPEN_ID" ]]; then
  echo "拿不到 open_id。请用 --open-id ou_xxx 指定接收人，或先 lark-cli auth login。" >&2
  exit 1
fi

# 3) 拷贝脚本
mkdir -p "$INSTALL_DIR"
cp "$REPO_ROOT/scripts/notify_lib.py" "$REPO_ROOT/scripts/claude_stop_hook.py" \
   "$REPO_ROOT/scripts/codex_notify.py" "$REPO_ROOT/scripts/wire.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/"*.py

# 4) 写配置（保留已有 codex_chain）
"$PYTHON_BIN" - "$CONFIG_PATH" "$OPEN_ID" "$IDENTITY" "$MIN_SECONDS" "$LARK_CLI" <<'PY'
import sys, os, json
path, open_id, identity, min_s, lark = sys.argv[1:6]
cfg = {}
if os.path.exists(path):
    try: cfg = json.load(open(path, encoding="utf-8"))
    except Exception: cfg = {}
cfg.update({"open_id": open_id, "identity": identity,
            "min_seconds": int(min_s), "lark_cli": lark})
cfg.setdefault("codex_chain", [])
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(path, "a").write("\n")
print("配置已写:", path)
PY

# 5) 接线
WIRED=()
if [[ ",$AGENTS," == *",claude,"* ]]; then
  cp "$CLAUDE_DIR/settings.json" "$CLAUDE_DIR/settings.json.heige-bak" 2>/dev/null || true
  "$PYTHON_BIN" "$INSTALL_DIR/wire.py" claude --install-dir "$INSTALL_DIR" --claude-config "$CLAUDE_DIR" >/dev/null
  WIRED+=("Claude Code (Stop hook → $CLAUDE_DIR/settings.json)")
fi
if [[ ",$AGENTS," == *",codex,"* ]]; then
  cp "$CODEX_CONFIG" "$CODEX_CONFIG.heige-bak" 2>/dev/null || true
  "$PYTHON_BIN" "$INSTALL_DIR/wire.py" codex --install-dir "$INSTALL_DIR" \
    --codex-config "$CODEX_CONFIG" --app-config "$CONFIG_PATH" >/dev/null
  WIRED+=("Codex (notify → $CODEX_CONFIG，原有 notify 已链接转发)")
fi

echo
echo "✅ 安装完成。接收人 open_id: $OPEN_ID"
for w in "${WIRED[@]}"; do echo "  - $w"; done
echo
echo "下一步："
echo "  1) 新开一个 Claude Code 会话（hook 在会话启动时加载；首次可能需确认 hook 变更）。"
echo "  2) Codex 需重启会话使 config.toml 生效；notify 在交互式会话结束一轮时触发。"
echo "  3) 自测发送：$LARK_CLI im +messages-send --as $IDENTITY --user-id $OPEN_ID --markdown '测试'"
echo "  改阈值/接收人：编辑 $CONFIG_PATH，或重跑 install.sh。卸载：bash uninstall.sh"
