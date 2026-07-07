#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heige-agent-report · Claude Code Stop hook
Claude Code（桌面端 / VSCode / CLI）每轮任务结束时由引擎强制调用本脚本，
把本轮结果摘要通过飞书/Lark 推给用户。确定性触发，不靠模型自觉。

只在本轮耗时 >= min_seconds 时推送（快速问答不打扰）。发送异步、异常静默，
永不阻塞或影响 Claude Code 本体。
"""
import sys
import os
import json
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_lib  # noqa: E402


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def main():
    try:
        hook = json.load(sys.stdin)
    except Exception:
        return
    # 防重入：Stop hook 触发的后续 Stop 不再推
    if hook.get("stop_hook_active"):
        return
    # 与 lark-coding-agent-bridge 共存：若本会话由 bridge 驱动（它自己会回飞书卡片），
    # 这里跳过，避免重复通知。两者可并存互不打架。
    if os.environ.get("LARK_CHANNEL") or "lark-channel" in os.environ.get("CLAUDE_CONFIG_DIR", ""):
        return

    cfg = notify_lib.load_config()
    threshold = float(os.environ.get("HEIGE_AGENT_REPORT_MIN_SECONDS",
                                     cfg.get("min_seconds", 45)))

    tp = os.path.expanduser(hook.get("transcript_path", "") or "")
    cwd = hook.get("cwd", "") or ""
    if not tp or not os.path.exists(tp):
        return

    last_user_ts = None   # 本轮真人提问时间（起点）
    last_ts = None        # 最后一条带时间戳记录（终点）
    last_assistant = ""   # 本轮最后的 assistant 文本

    try:
        with open(tp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                t = o.get("type")
                ts = parse_ts(o.get("timestamp"))
                if ts:
                    last_ts = ts
                if t == "user" and not o.get("isSidechain") and "toolUseResult" not in o:
                    content = (o.get("message", {}) or {}).get("content")
                    is_prompt = isinstance(content, str) or (
                        isinstance(content, list)
                        and any(isinstance(c, dict) and c.get("type") == "text" for c in content)
                        and not any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content)
                    )
                    if is_prompt and ts:
                        last_user_ts = ts
                elif t == "assistant" and not o.get("isSidechain"):
                    parts = [c.get("text", "") for c in ((o.get("message", {}) or {}).get("content") or [])
                             if isinstance(c, dict) and c.get("type") == "text"]
                    txt = "".join(parts).strip()
                    if txt:
                        last_assistant = txt
    except Exception:
        return

    # 耗时闸门
    if last_user_ts and last_ts:
        dur = (last_ts - last_user_ts).total_seconds()
    else:
        dur = threshold  # 拿不到时间就默认推
    if dur < threshold:
        return

    summary = notify_lib.clip(last_assistant) or "（任务完成，无文本输出）"
    proj = notify_lib.project_name(cwd)
    m, s = int(dur // 60), int(dur % 60)
    dstr = f"{m}分{s}秒" if m else f"{s}秒"
    md = f"🖥️ **Claude Code · 任务完成**\n项目：{proj}  ·  耗时 {dstr}\n\n{summary}"
    notify_lib.send_markdown(md, cfg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
