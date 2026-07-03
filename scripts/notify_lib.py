#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heige-agent-report · 共享库
读取本地配置、把一条 Markdown 消息通过飞书/Lark CLI 异步发给用户。
零第三方依赖（仅标准库）。任何异常静默，绝不影响宿主 agent。
"""
import os
import json
import subprocess

DEFAULT_CONFIG_PATH = os.path.expanduser(
    os.environ.get("HEIGE_AGENT_REPORT_CONFIG", "~/.heige-agent-report/config.json")
)

DEFAULTS = {
    "open_id": "",          # 接收人 open_id（必填）
    "identity": "bot",      # 发送身份：bot | user
    "lark_cli": "lark-cli", # lark-cli 可执行路径或命令名
    "min_seconds": 45,      # Claude Code 单轮耗时闸门（秒），0 = 每轮都推
    "codex_chain": [],      # Codex：转发给的原有 notify 程序，如 ["/path/prog", "arg"]
}


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


def send_markdown(md, cfg=None):
    """通过 lark-cli 异步发送一条 Markdown 消息（fire-and-forget）。
    返回 True 表示已成功拉起发送子进程（不代表送达）。"""
    cfg = cfg or load_config()
    open_id = str(cfg.get("open_id") or "").strip()
    if not open_id:
        return False
    lark = cfg.get("lark_cli") or "lark-cli"
    identity = cfg.get("identity") or "bot"
    try:
        subprocess.Popen(
            [lark, "im", "+messages-send", "--as", identity,
             "--user-id", open_id, "--markdown", md],
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
