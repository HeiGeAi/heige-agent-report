#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heige-agent-report · doctor 自检
检查配置、lark-cli、钩子接线，并**真实发一条测试消息**，把整条链路验通。
退出 0 = 全部通过。
用法: python3 doctor.py [--claude-config ~/.claude] [--codex-config ~/.codex/config.toml]
"""
import os
import sys
import json
import shutil
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_lib  # noqa: E402


def line(ok, name, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  [{detail}]" if detail else ""))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude-config", default="~/.claude")
    ap.add_argument("--codex-config", default="~/.codex/config.toml")
    a = ap.parse_args()

    hard_ok = True
    cfg = notify_lib.load_config()

    hard_ok &= line(bool(cfg.get("open_id")), "配置存在且 open_id 已设",
                    cfg.get("open_id", "") or "缺失")

    lark = cfg.get("lark_cli", "lark-cli")
    lark_ok = bool(shutil.which(lark) or os.path.exists(os.path.expanduser(lark)))
    hard_ok &= line(lark_ok, "lark-cli 可用", lark)

    # 钩子接线（信息性，可能只装了其一）
    cset = os.path.join(os.path.expanduser(a.claude_config), "settings.json")
    claude_wired = False
    if os.path.exists(cset):
        try:
            d = json.load(open(cset, encoding="utf-8"))
            claude_wired = any(
                "claude_stop_hook.py" in (h.get("command", ""))
                for g in d.get("hooks", {}).get("Stop", [])
                for h in g.get("hooks", []))
        except Exception:
            pass
    line(claude_wired, "Claude Code Stop hook 已接线", "" if claude_wired else "未接（若只装 Codex 可忽略）")

    ccfg = os.path.expanduser(a.codex_config)
    codex_wired = os.path.exists(ccfg) and "codex_notify.py" in open(ccfg, encoding="utf-8").read()
    line(codex_wired, "Codex notify 已接线", "" if codex_wired else "未接（若只装 Claude 可忽略）")

    # 真实测试发送（同步，直接看结果）
    if cfg.get("open_id") and lark_ok:
        md = "🩺 **heige-agent-report · doctor**\n这是一条链路自检测试消息，收到即代表配置正确、通知能送达。"
        try:
            r = subprocess.run(
                [lark, "im", "+messages-send", "--as", cfg.get("identity", "bot"),
                 "--user-id", cfg["open_id"], "--markdown", md],
                capture_output=True, text=True, timeout=40)
            sent = notify_lib._lark_ok(r.returncode, (r.stdout or "") + (r.stderr or ""))
            hard_ok &= line(sent, "测试消息已发送",
                            "去飞书查收" if sent else ((r.stdout or r.stderr).strip()[:80]))
        except Exception as e:
            hard_ok &= line(False, "测试消息已发送", str(e)[:80])
    else:
        hard_ok &= line(False, "测试消息已发送", "前置未满足，跳过")

    print()
    print("✅ 链路自检通过，去飞书看那条测试消息。" if hard_ok
          else "⚠️ 有必检项未通过，按上面的 ✗ 修。")
    sys.exit(0 if hard_ok else 1)


if __name__ == "__main__":
    main()
