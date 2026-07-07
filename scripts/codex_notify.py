#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heige-agent-report · Codex notify hook
Codex 通过 `notify = ["/usr/bin/python3", "<this>"]` 在一轮任务结束时调用本脚本，
最后一个参数是一段 JSON（含 type / last-assistant-message 等）。

本脚本：
1) 确定性把结果摘要推送飞书/Lark；
2) 若配置了 codex_chain，则把原始 payload 转发给原有 notify 程序
   （例如电脑操作通知器），保证既有功能不受影响。
两者都非阻塞，异常静默，绝不影响 Codex 本体。
"""
import sys
import os
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_lib  # noqa: E402

# Codex 结束一轮的事件类型（不同版本可能不同，做宽松匹配）
TURN_DONE_TYPES = ("agent-turn-complete", "turn-ended", "turn-complete", "")


def chain(payload, cfg):
    prog = cfg.get("codex_chain") or []
    if isinstance(prog, str):
        prog = [prog]
    if not prog:
        return
    try:
        subprocess.Popen(list(prog) + [payload],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def main():
    payload = sys.argv[-1] if len(sys.argv) > 1 else "{}"
    # 与 lark-coding-agent-bridge 共存：bridge 驱动的 codex 会话由 bridge 自己回卡片，
    # 这里整体跳过（含转发），避免重复通知。
    if os.environ.get("LARK_CHANNEL") or "lark-channel" in os.environ.get("LARKSUITE_CLI_CONFIG_DIR", ""):
        return
    cfg = notify_lib.load_config()

    # 先转发给原有通知器，保证既有功能
    chain(payload, cfg)

    try:
        data = json.loads(payload)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    if data.get("type", "") not in TURN_DONE_TYPES:
        return

    summary = (data.get("last-assistant-message")
               or data.get("last_assistant_message") or "").strip()
    if not summary:
        ims = data.get("input-messages") or data.get("input_messages") or []
        summary = ("；".join(str(x) for x in ims)[:200]) if ims else "（Codex 完成一轮，无文本输出）"

    proj = notify_lib.project_name()
    md = f"⌨️ **Codex · 任务完成**\n项目：{proj}\n\n{notify_lib.clip(summary)}"
    notify_lib.send_markdown(md, cfg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
