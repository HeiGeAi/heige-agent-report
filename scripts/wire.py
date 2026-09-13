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
import shlex
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
    # hook 命令由 shell 解析：路径含空格/特殊字符时必须 quote，否则被截断执行失败
    cmd = f"{shlex.quote(PY)} {shlex.quote(os.path.join(install_dir, 'claude_stop_hook.py'))}"
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

# 顶层 notify 赋值的键位（值由 _find_notify_span 扫描，容忍注释与多行）
_NOTIFY_KEY_RE = re.compile(r'(?m)^[ \t]*notify[ \t]*=')


def _find_notify_span(text):
    """定位顶层 notify = [...] 赋值，容忍行尾注释、多行数组、嵌套括号与引号。
    返回 (start, end, array_src)；没有合法数组写法返回 None。"""
    m = _NOTIFY_KEY_RE.search(text)
    if not m:
        return None
    n = len(text)
    i = m.end()
    while i < n and text[i] in " \t":
        i += 1
    if i >= n or text[i] != "[":
        return None
    depth = 0
    j = i
    while j < n:
        c = text[j]
        if c == "#":  # 行内注释：跳到行尾
            nl = text.find("\n", j)
            j = n if nl == -1 else nl + 1
            continue
        if c in "\"'":
            q = c
            j += 1
            while j < n:
                if q == '"' and text[j] == "\\":  # 基本字符串转义
                    j += 2
                    continue
                if text[j] == q:
                    j += 1
                    break
                j += 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return (m.start(), j + 1, text[i:j + 1])
        j += 1
    return None  # 数组未闭合


def _parse_notify_array(src):
    """把 TOML 数组源码解析成 list[str]。依次尝试 JSON、tomllib（3.11+）、
    Python 字面量（兼容 TOML 单引号写法）；全部失败返回 None。"""
    try:
        v = json.loads(src)
        if isinstance(v, list):
            return [str(x) for x in v]
    except Exception:
        pass
    try:
        import tomllib
        v = tomllib.loads("notify = " + src).get("notify")
        if isinstance(v, list):
            return [str(x) for x in v]
    except Exception:
        pass
    try:
        import ast
        v = ast.literal_eval(src)
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v]
    except Exception:
        pass
    return None


def _validate_codex_config(text, expect_notify):
    """回写前校验：notify 键数量符合预期；有 tomllib（Python 3.11+）时做全量
    TOML 解析。不合法抛 SystemExit，绝不写出破坏配置的 TOML。"""
    count = len(_NOTIFY_KEY_RE.findall(text))
    if count != expect_notify:
        raise SystemExit(
            f"回写校验失败：config.toml 应有 {expect_notify} 个 notify 键，实际 {count} 个。"
            "已中止，config.toml 未改动，请手动检查配置。")
    try:
        import tomllib
    except ImportError:
        return
    try:
        tomllib.loads(text)
    except Exception as e:
        raise SystemExit(
            f"回写校验失败：生成的 config.toml 不是合法 TOML（{e}）。"
            "已中止，config.toml 未改动。")


def wire_codex(install_dir, codex_config, app_config):
    p = os.path.expanduser(codex_config)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    text = ""
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            text = f.read()
    our_script = os.path.join(install_dir, "codex_notify.py")
    # TOML 基本字符串与 JSON 字符串转义规则一致，用 json.dumps 防路径带空格/引号
    our_line = "notify = [" + ", ".join(json.dumps(x) for x in (PY, our_script)) + "]"

    span = _find_notify_span(text)
    if span:
        start, end, existing = span
        # 已是我们的？则只更新，不重复捕获
        if "codex_notify.py" not in existing:
            # 捕获原有 notify 作为转发目标，写入 app_config 的 codex_chain。
            # 捕获失败与覆盖原值必须互斥：解析不出来就中止，绝不静默覆盖。
            chain = _parse_notify_array(existing)
            if chain is None:
                raise SystemExit(
                    "无法解析 config.toml 里原有的 notify 数组，为避免覆盖丢失已中止安装，"
                    "config.toml 未改动。请把 notify 改成 JSON 写法（双引号）后重试，"
                    "或先手动备份该键。")
            _set_chain(app_config, chain)
        text = text[:start] + our_line + text[end:]
    else:
        if _NOTIFY_KEY_RE.search(text):
            # 有 notify 键但不是合法数组写法：宁可中止也不追加成重复键
            raise SystemExit(
                "config.toml 里存在 notify 键但不是合法数组写法，已中止安装，"
                "config.toml 未改动，请手动检查。")
        # 无 notify：追加（放在文件顶部键区之后，简单追加到末尾也合法）
        if text and not text.endswith("\n"):
            text += "\n"
        text += our_line + "\n"

    _validate_codex_config(text, 1)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def unwire_codex(codex_config, app_config=None):
    p = os.path.expanduser(codex_config)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        text = f.read()
    span = _find_notify_span(text)
    if not span or "codex_notify.py" not in span[2]:
        return p
    start, end, _existing = span
    # 尝试还原为原有 notify（codex_chain）
    restored = None
    if app_config and os.path.exists(os.path.expanduser(app_config)):
        try:
            with open(os.path.expanduser(app_config), encoding="utf-8") as f:
                cfg = json.load(f)
            chain = cfg.get("codex_chain") or []
            if chain:
                restored = "notify = [" + ", ".join(
                    json.dumps(str(x), ensure_ascii=False) for x in chain) + "]"
        except Exception:
            pass
    text = text[:start] + (restored or "") + text[end:]
    _validate_codex_config(text, 1 if restored else 0)
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
