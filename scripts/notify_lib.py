#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heige-agent-report · 共享库
读取本地配置，把一条 Markdown 消息通过飞书/Lark CLI 发给用户。
零第三方依赖（仅标准库）。任何异常静默，绝不影响宿主 agent。

送信采用「非阻塞 + 后台重试 + 落日志」：
钩子只写一个临时文件并拉起脱离进程组的 worker 就立即返回（~0.07s），
worker 带退避重试 lark-cli，并把每次结果记到 report.log，便于排查漏发。
"""
import os
import sys
import json
import time
import tempfile
import datetime
import subprocess

HOME_DIR = os.path.expanduser(
    os.environ.get("HEIGE_AGENT_REPORT_HOME", "~/.heige-agent-report"))
DEFAULT_CONFIG_PATH = os.path.expanduser(
    os.environ.get("HEIGE_AGENT_REPORT_CONFIG", os.path.join(HOME_DIR, "config.json")))
DEFAULT_LOG_PATH = os.path.join(HOME_DIR, "report.log")

DEFAULTS = {
    "open_id": "",          # 接收人 open_id（必填）
    "identity": "bot",      # 发送身份：bot | user
    "lark_cli": "lark-cli", # lark-cli 可执行路径或命令名
    "min_seconds": 45,      # Claude Code 单轮耗时闸门（秒），0 = 每轮都推
    "codex_chain": [],      # Codex：转发给的原有 notify 程序
    "max_retries": 3,       # 送信失败最多重试次数
    "log": True,            # 是否记录 report.log
}

_BACKOFF = [2, 5, 12]  # 各次重试前等待秒数


def load_config(path=None):
    cfg = dict(DEFAULTS)
    p = os.path.expanduser(path or DEFAULT_CONFIG_PATH)
    try:
        with open(p, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if v is not None})
    except Exception:
        pass
    return cfg


def log(cfg, msg):
    """把一行带时间戳的消息追加到 report.log；超 512KB 自动截断保留尾部。"""
    if cfg is not None and cfg.get("log") is False:
        return
    try:
        p = os.path.expanduser((cfg or {}).get("log_path", DEFAULT_LOG_PATH))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > 512 * 1024:
            with open(p, "rb") as f:
                tail = f.read()[-256 * 1024:]
            with open(p, "wb") as f:
                f.write(tail)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}\n")
    except Exception:
        pass


def _lark_ok(rc, out):
    o = (out or "").lower()
    return rc == 0 and ('"message_id"' in o or '"ok": true' in o or '"ok":true' in o)


def _worker(tmp_path):
    """脱离进程组运行：带退避重试 lark-cli，记日志。"""
    cfg = load_config()
    try:
        with open(tmp_path, encoding="utf-8") as f:
            md = f.read()
    except Exception:
        return
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    open_id = str(cfg.get("open_id") or "").strip()
    if not open_id:
        return
    lark = cfg.get("lark_cli") or "lark-cli"
    identity = cfg.get("identity") or "bot"
    tries = max(1, int(cfg.get("max_retries", 3)))

    for attempt in range(1, tries + 1):
        err = ""
        try:
            r = subprocess.run(
                [lark, "im", "+messages-send", "--as", identity,
                 "--user-id", open_id, "--markdown", md],
                capture_output=True, text=True, timeout=40)
            out = (r.stdout or "") + (r.stderr or "")
            if _lark_ok(r.returncode, out):
                log(cfg, f"sent ok (attempt {attempt}/{tries})")
                return
            err = out.strip().replace("\n", " ")[:200]
        except Exception as e:
            err = str(e)[:200]
        log(cfg, f"send failed {attempt}/{tries}: {err}")
        if attempt < tries:
            time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
    log(cfg, "give up after retries")


def send_markdown(md, cfg=None):
    """非阻塞送信：写临时文件 + 拉起脱离进程组的重试 worker，父进程立即返回。
    返回 True 表示 worker 已拉起（不代表送达；送达结果见 report.log）。"""
    cfg = cfg or load_config()
    open_id = str(cfg.get("open_id") or "").strip()
    if not open_id:
        return False
    try:
        fd, tmp = tempfile.mkstemp(prefix="har-", suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(md)
        subprocess.Popen(
            [sys.executable or "python3", os.path.abspath(__file__), "--send", tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        return True
    except Exception:
        return False


def project_name(cwd=None):
    cwd = cwd or os.getcwd()
    return os.path.basename(str(cwd).rstrip("/")) or str(cwd) or "?"


def clip(text, n=900):
    text = (text or "").strip()
    return text[:n]


if __name__ == "__main__":
    # worker 入口：python3 notify_lib.py --send <tmp.md>
    if len(sys.argv) >= 3 and sys.argv[1] == "--send":
        _worker(sys.argv[2])
