#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heige-agent-report · 配置接线器
幂等地把通知钩子写入 / 移除各 agent 的配置：
- Claude Code：~/.claude/settings.json 的 hooks.Stop（JSON，安全合并）
- Codex：~/.codex/config.toml 的 notify（捕获原有 notify 作为转发目标，写回 config.json）

用法：
  wire.py claude   --install-dir DIR [--claude-config ~/.claude]
  wire.py codex    --install-dir DIR --app-config PATH [--codex-config ~/.codex/config.toml]
  wire.py unclaude [--claude-config ~/.claude]
  wire.py uncodex  [--codex-config ~/.codex/config.toml]
零第三方依赖。
"""
import sys
import os
import json
import re
import argparse

# 优先系统稳定 python（macOS 的 /usr/bin/python3 常驻），退回当前解释器
PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else (sys.executable or "python3")
MARK = "heige-agent-report"


# ---------- Claude Code ----------

def claude_settings_path(claude_dir):
    return os.path.join(os.path.expanduser(claude_dir), "settings.json")


def wire_claude(install_dir, claude_dir):
    p = claude_settings_path(claude_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    cmd = f"{PY} {os.path.join(install_dir, 'claude_stop_hook.py')}"
    hooks = data.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])
    # 幂等：去掉任何指向本产品的旧条目，再加一条
    def is_ours(group):
        for h in group.get("hooks", []):
            if MARK in (h.get("command") or "") or "claude_stop_hook.py" in (h.get("command") or ""):
                return True
        return False
    stop = [g for g in stop if not is_ours(g)]
    stop.append({"hooks": [{"type": "command", "command": cmd}]})
    hooks["Stop"] = stop
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return p


def unwire_claude(claude_dir):
    p = claude_settings_path(claude_dir)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    stop = (data.get("hooks", {}) or {}).get("Stop", [])
    def is_ours(group):
        for h in group.get("hooks", []):
            if "claude_stop_hook.py" in (h.get("command") or ""):
                return True
        return False
    kept = [g for g in stop if not is_ours(g)]
    if kept:
        data["hooks"]["Stop"] = kept
    else:
        data.get("hooks", {}).pop("Stop", None)
        if not data.get("hooks"):
            data.pop("hooks", None)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return p


# ---------- Codex ----------

NOTIFY_RE = re.compile(r'(?m)^\s*notify\s*=\s*(\[.*\])\s*$')


def wire_codex(install_dir, codex_config, app_config):
    p = os.path.expanduser(codex_config)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    text = ""
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            text = f.read()
    our_script = os.path.join(install_dir, "codex_notify.py")
    our_line = f'notify = ["{PY}", "{our_script}"]'

    m = NOTIFY_RE.search(text)
    if m:
        existing = m.group(1)
        # 已是我们的？则只更新，不重复捕获
        if "codex_notify.py" not in existing:
            # 捕获原有 notify 作为转发目标，写入 app_config 的 codex_chain
            try:
                chain = json.loads(existing)
                _set_chain(app_config, chain if isinstance(chain, list) else [])
            except Exception:
                pass
        text = NOTIFY_RE.sub(our_line, text, count=1)
    else:
        # 无 notify：追加（放在文件顶部键区之后，简单追加到末尾也合法）
        if text and not text.endswith("\n"):
            text += "\n"
        text += our_line + "\n"

    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def unwire_codex(codex_config, app_config=None):
    p = os.path.expanduser(codex_config)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        text = f.read()
    m = NOTIFY_RE.search(text)
    if m and "codex_notify.py" in m.group(1):
        # 尝试还原为原有 notify（codex_chain）
        restored = None
        if app_config and os.path.exists(os.path.expanduser(app_config)):
            try:
                cfg = json.load(open(os.path.expanduser(app_config), encoding="utf-8"))
                chain = cfg.get("codex_chain") or []
                if chain:
                    restored = "notify = " + json.dumps(chain, ensure_ascii=False)
            except Exception:
                pass
        if restored:
            text = NOTIFY_RE.sub(restored, text, count=1)
        else:
            text = NOTIFY_RE.sub("", text, count=1)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    return p


def _set_chain(app_config, chain):
    p = os.path.expanduser(app_config)
    cfg = {}
    if os.path.exists(p):
        try:
            cfg = json.load(open(p, encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["codex_chain"] = chain
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["claude", "codex", "unclaude", "uncodex"])
    ap.add_argument("--install-dir", default=os.path.expanduser("~/.heige-agent-report"))
    ap.add_argument("--claude-config", default="~/.claude")
    ap.add_argument("--codex-config", default="~/.codex/config.toml")
    ap.add_argument("--app-config", default=os.path.expanduser("~/.heige-agent-report/config.json"))
    a = ap.parse_args()
    if a.cmd == "claude":
        print(wire_claude(a.install_dir, a.claude_config))
    elif a.cmd == "codex":
        print(wire_codex(a.install_dir, a.codex_config, a.app_config))
    elif a.cmd == "unclaude":
        print(unwire_claude(a.claude_config))
    elif a.cmd == "uncodex":
        print(unwire_codex(a.codex_config, a.app_config))


if __name__ == "__main__":
    main()
