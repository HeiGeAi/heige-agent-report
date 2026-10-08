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
import hashlib
import fcntl
from contextlib import contextmanager

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


def session_key(data):
    identity = data.get("thread-id") or data.get("thread_id") or data.get("session-id") or data.get("session_id")
    identity = ["thread", identity] if identity else ["cwd", data.get("cwd")]
    if identity == ["cwd", None]:
        return ""
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def pending_path(key):
    if key and (len(key) != 64 or any(c not in "0123456789abcdef" for c in key)):
        raise ValueError("invalid session key")
    return PENDING + ("." + key if key else "")


@contextmanager
def pending_lock(path):
    # Lock a stable inode, never the pending file that flush removes.
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def enqueue(data):
    key = session_key(data)
    path = pending_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with pending_lock(path):
        item = {"count": 1, "data": data, "first_ts": time.time()}
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
            item["count"] = int(old.get("count", 0)) + 1
            item["first_ts"] = old.get("first_ts", item["first_ts"])
        except (OSError, ValueError):
            pass
        temporary = path + f".tmp.{os.getpid()}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False)
        os.replace(temporary, path)
    return key


def spawn_flusher(key=""):
    try:
        subprocess.Popen(
            [sys.executable or "python3", os.path.abspath(__file__), "--flush", key],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def flush(key=""):
    """Claim only this session under the same stable lock as enqueue."""
    cfg = notify_lib.load_config()
    qs = quiet_seconds(cfg)
    time.sleep(qs)
    path = pending_path(key)
    if not os.path.exists(path):
        return
    with pending_lock(path):
        try:
            if time.time() - os.path.getmtime(path) < qs:
                return
            with open(path, encoding="utf-8") as f:
                item = json.load(f)
            os.unlink(path)
        except OSError:
            return

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
        flush(sys.argv[2] if len(sys.argv) > 2 else "")
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
    key = enqueue(data)
    spawn_flusher(key)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
