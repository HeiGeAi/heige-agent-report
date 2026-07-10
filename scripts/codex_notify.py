#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heige-agent-report · Codex notify hook（debounce 聚合版）
Codex 通过 `notify = ["/usr/bin/python3", "<this>"]` 在任务过程中调用本脚本，
最后一个参数是一段 JSON（含 type / last-assistant-message 等）。

新版 Codex 桌面端会把一个任务切成多个小 turn，每个都触发一次
agent-turn-complete（实测最密 6 秒一条）。逐条推送打扰严重，因此飞书推送
采用 debounce 聚合：事件到来只入队刷新窗口，静默 quiet_seconds（默认 120，
env HEIGE_AGENT_REPORT_QUIET_SECONDS 可覆盖）后无新事件才发「最后一条」，
一个任务只给一个总反馈，与 Claude Code 端体验一致。

若配置了 codex_chain（原有 notify 程序，如电脑操作通知器），每个事件仍
即时转发给它，原功能节奏不变。一切异常静默，绝不影响 Codex 本体。
"""
import sys
import os
import json
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_lib  # noqa: E402

PENDING = os.path.join(notify_lib.HOME_DIR, ".codex_pending.json")
TURN_DONE_TYPES = ("agent-turn-complete", "turn-ended", "turn-complete")


def quiet_seconds(cfg):
    try:
        return float(os.environ.get("HEIGE_AGENT_REPORT_QUIET_SECONDS",
                                    cfg.get("quiet_seconds", 120)))
    except Exception:
        return 120.0


def chain(payload, cfg):
    """把原始事件即时转发给原有 notify 程序（节奏由它自己定，不聚合）。"""
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


def enqueue(data):
    """并入 pending：保留最新 payload、累计轮数，刷新 mtime（即刷新静默窗口）。"""
    item = {"count": 1, "data": data, "first_ts": time.time()}
    try:
        if os.path.exists(PENDING):
            old = json.load(open(PENDING, encoding="utf-8"))
            item["count"] = int(old.get("count", 0)) + 1
            item["first_ts"] = old.get("first_ts", item["first_ts"])
    except Exception:
        pass
    os.makedirs(os.path.dirname(PENDING), exist_ok=True)
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump(item, f, ensure_ascii=False)


def spawn_flusher():
    try:
        subprocess.Popen(
            [sys.executable or "python3", os.path.abspath(__file__), "--flush"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def flush():
    """静默窗口后无新事件才发送；多 worker 并发用原子 rename 抢占，只发一次。"""
    cfg = notify_lib.load_config()
    qs = quiet_seconds(cfg)
    time.sleep(qs)
    try:
        mtime = os.path.getmtime(PENDING)
    except OSError:
        return  # 已被别的 worker 发走
    if time.time() - mtime < qs - 1:
        return  # 窗口内来了新事件，交给更晚的 worker
    sending = PENDING + f".sending.{os.getpid()}"
    try:
        os.rename(PENDING, sending)
    except OSError:
        return
    try:
        item = json.load(open(sending, encoding="utf-8"))
    except Exception:
        item = {}
    finally:
        try:
            os.unlink(sending)
        except OSError:
            pass

    data = item.get("data") or {}
    count = int(item.get("count", 1))
    summary = (data.get("last-assistant-message")
               or data.get("last_assistant_message") or "").strip()
    if not summary:
        ims = data.get("input-messages") or data.get("input_messages") or []
        summary = ("；".join(str(x) for x in ims)[:200]) if ims else "（Codex 任务完成，无文本输出）"

    proj = notify_lib.project_name(data.get("cwd") or None)
    agg = f"（聚合了 {count} 轮中间汇报）" if count > 1 else ""
    md = f"⌨️ **Codex · 任务完成**{agg}\n项目：{proj}\n\n{notify_lib.clip(summary)}"
    notify_lib.send_markdown(md, cfg)
    notify_lib.log(cfg, f"codex flush: sent 1 notice for {count} turn event(s)")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--flush":
        flush()
        return
    payload = sys.argv[-1] if len(sys.argv) > 1 else "{}"
    # 与 lark-coding-agent-bridge 共存：bridge 驱动的会话由 bridge 自己回卡片，整体跳过
    if os.environ.get("LARK_CHANNEL") or "lark-channel" in os.environ.get("LARKSUITE_CLI_CONFIG_DIR", ""):
        return
    cfg = notify_lib.load_config()

    # 原有通知器即时转发，保证既有功能
    chain(payload, cfg)

    try:
        data = json.loads(payload)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    if data.get("type", "") not in TURN_DONE_TYPES:
        return
    enqueue(data)
    spawn_flusher()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
