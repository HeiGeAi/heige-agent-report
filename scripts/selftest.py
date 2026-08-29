#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heige-agent-report · 确定性自测（CI 用，零网络、零第三方依赖）
1) 所有脚本可编译；
2) wire.py 在临时沙箱里往返：装 → 断言钩子就位 + 捕获原 notify → 卸 → 断言还原。
全部通过退出 0，否则非 0。
"""
import os
import sys
import json
import tempfile
import subprocess
import threading
import time
import py_compile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
FAILS = []


def check(name, cond):
    print(("  ok  " if cond else "FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def main():
    # 1) 语法编译
    for fn in ("notify_lib.py", "claude_stop_hook.py", "codex_notify.py",
               "wire.py", "doctor.py", "selftest.py"):
        p = os.path.join(HERE, fn)
        try:
            py_compile.compile(p, doraise=True)
            check(f"compile {fn}", True)
        except Exception as e:
            print("   ", e)
            check(f"compile {fn}", False)

    # 2) 沙箱往返
    with tempfile.TemporaryDirectory() as sb:
        cdir = os.path.join(sb, "claude")
        os.makedirs(cdir)
        cset = os.path.join(cdir, "settings.json")
        json.dump({"env": {"FOO": "bar"},
                   "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}},
                  open(cset, "w"))
        codex = os.path.join(sb, "config.toml")
        open(codex, "w").write('model = "x"\nnotify = [ "/old/n", "arg" ]\n')
        appcfg = os.path.join(sb, "app.json")
        idir = os.path.join(sb, "install")
        os.makedirs(idir)

        def wire(*args):
            subprocess.run([PY, os.path.join(HERE, "wire.py"), *args], check=True,
                           stdout=subprocess.DEVNULL)

        wire("claude", "--install-dir", idir, "--claude-config", cdir)
        wire("codex", "--install-dir", idir, "--codex-config", codex, "--app-config", appcfg)

        d = json.load(open(cset))
        cmds = [h["hooks"][0]["command"] for h in d["hooks"]["Stop"]]
        check("claude: 保留旧 hook", "echo keep" in cmds)
        check("claude: 加了本产品 hook", any("claude_stop_hook.py" in c for c in cmds))
        check("claude: 保留旧 env", d.get("env", {}).get("FOO") == "bar")

        ct = open(codex).read()
        check("codex: notify 指向本产品", "codex_notify.py" in ct)
        chain = json.load(open(appcfg)).get("codex_chain")
        check("codex: 捕获原 notify 到 codex_chain", chain == ["/old/n", "arg"])

        # 幂等
        wire("claude", "--install-dir", idir, "--claude-config", cdir)
        d2 = json.load(open(cset))
        ours = [c for c in [h["hooks"][0]["command"] for h in d2["hooks"]["Stop"]]
                if "claude_stop_hook.py" in c]
        check("claude: 幂等（不重复）", len(ours) == 1)

        # 卸载还原
        wire("unclaude", "--claude-config", cdir)
        wire("uncodex", "--codex-config", codex, "--app-config", appcfg)
        d3 = json.load(open(cset))
        cmds3 = [h["hooks"][0]["command"] for h in d3.get("hooks", {}).get("Stop", [])]
        check("claude: 卸载后只剩旧 hook", cmds3 == ["echo keep"])
        check("codex: 卸载后还原原 notify", 'notify = ["/old/n", "arg"]' in open(codex).read())

    # 3) 完整安装器在隔离 HOME 中能正常返回成功
    with tempfile.TemporaryDirectory() as sb:
        fake_lark = os.path.join(sb, "lark-cli")
        open(fake_lark, "w").write("#!/usr/bin/env sh\nprintf '%s\\n' '{\"message_id\":\"om_test\"}'\n")
        os.chmod(fake_lark, 0o755)
        codex_dir = os.path.join(sb, ".codex")
        os.makedirs(codex_dir)
        codex = os.path.join(codex_dir, "config.toml")
        open(codex, "w").write('model = "audit"\n')
        env = dict(os.environ, HOME=sb,
                   HEIGE_AGENT_REPORT_HOME=os.path.join(sb, "install"))
        result = subprocess.run(
            ["bash", os.path.join(os.path.dirname(HERE), "install.sh"),
             "--open-id", "ou_test", "--lark-cli", fake_lark,
             "--agents", "claude,codex", "--codex-config", codex],
            env=env, capture_output=True, text=True, errors="replace")
        check("安装器: 双 Agent 主路径退出 0", result.returncode == 0)

    # 4) 安装中途失败时，只回滚本轮触碰的文件
    with tempfile.TemporaryDirectory() as sb:
        home = os.path.join(sb, "home")
        cdir = os.path.join(home, ".claude")
        codex_dir = os.path.join(home, ".codex")
        idir = os.path.join(sb, "install")
        bindir = os.path.join(sb, "bin")
        for directory in (cdir, codex_dir, idir, bindir):
            os.makedirs(directory)

        paths = {
            "claude": os.path.join(cdir, "settings.json"),
            "codex": os.path.join(codex_dir, "config.toml"),
            "app": os.path.join(idir, "config.json"),
            "script": os.path.join(idir, "notify_lib.py"),
            "sentinel": os.path.join(idir, "keep.txt"),
        }
        originals = {
            "claude": b'{"hooks":{"Stop":[]},"keep":"claude"}\n',
            "codex": b'model = "keep-codex"\nnotify = ["/old/notify"]\n',
            "app": b'{"keep":"app"}\n',
            "script": b'# keep existing script\n',
            "sentinel": b'leave unrelated file alone\n',
        }
        for name, content in originals.items():
            open(paths[name], "wb").write(content)

        backups = {
            paths["claude"] + ".heige-bak": b"existing claude backup\n",
            paths["codex"] + ".heige-bak": b"existing codex backup\n",
        }
        for path, content in backups.items():
            open(path, "wb").write(content)

        fake_lark = os.path.join(bindir, "lark-cli")
        open(fake_lark, "w").write("#!/usr/bin/env sh\nexit 0\n")
        os.chmod(fake_lark, 0o755)

        fake_python = os.path.join(bindir, "python3")
        open(fake_python, "w").write(
            f"#!{PY}\n"
            "import os, sys\n"
            f"real_python = {PY!r}\n"
            "args = sys.argv[1:]\n"
            "if len(args) > 1 and args[0].endswith('/wire.py') and args[1] == 'codex':\n"
            "    config = args[args.index('--codex-config') + 1]\n"
            "    open(config, 'w').write('corrupted by failing codex wire\\n')\n"
            "    raise SystemExit(42)\n"
            "os.execv(real_python, [real_python, *args])\n"
        )
        os.chmod(fake_python, 0o755)

        env = dict(os.environ, HOME=home, HEIGE_AGENT_REPORT_HOME=idir,
                   PATH=bindir + os.pathsep + os.environ.get("PATH", ""))
        result = subprocess.run(
            ["bash", os.path.join(os.path.dirname(HERE), "install.sh"),
             "--open-id", "ou_test", "--lark-cli", fake_lark,
             "--agents", "claude,codex", "--claude-config", cdir,
             "--codex-config", paths["codex"]],
            env=env, capture_output=True, text=True, errors="replace")

        check("安装器: Codex 接线失败向外传播", result.returncode == 42)
        for name, content in originals.items():
            check(f"安装器回滚: 保持 {name} 原字节", open(paths[name], "rb").read() == content)
        for path, content in backups.items():
            check(f"安装器回滚: 不覆盖既有备份 {os.path.basename(path)}",
                  open(path, "rb").read() == content)
        for filename in ("claude_stop_hook.py", "codex_notify.py", "wire.py", "doctor.py"):
            check(f"安装器回滚: 删除本轮新增 {filename}",
                  not os.path.exists(os.path.join(idir, filename)))

    # 5) 送信 worker 优雅失败：lark-cli 不可用时应重试→记日志→退出 0，绝不崩
    with tempfile.TemporaryDirectory() as sb:
        cfgp = os.path.join(sb, "config.json")
        logp = os.path.join(sb, "report.log")
        json.dump({"open_id": "ou_test", "lark_cli": "/bin/false",
                   "max_retries": 1, "log_path": logp}, open(cfgp, "w"))
        mdp = os.path.join(sb, "msg.md")
        open(mdp, "w").write("test")
        env = dict(os.environ, HEIGE_AGENT_REPORT_CONFIG=cfgp)
        r = subprocess.run([PY, os.path.join(HERE, "notify_lib.py"), "--send", mdp],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        check("worker: 失败也退出 0", r.returncode == 0)
        check("worker: 临时 md 已清理", not os.path.exists(mdp))
        logged = os.path.exists(logp) and "give up" in open(logp).read()
        check("worker: 失败已记入日志", logged)

    # 6) codex debounce 聚合：3 个密集 turn 事件 → 只发送 1 次
    with tempfile.TemporaryDirectory() as sb:
        cfgp = os.path.join(sb, "config.json")
        logp = os.path.join(sb, "report.log")
        json.dump({"open_id": "ou_test", "lark_cli": "/bin/false",
                   "max_retries": 1, "log_path": logp,
                   "quiet_seconds": 2}, open(cfgp, "w"))
        env = dict(os.environ, HEIGE_AGENT_REPORT_CONFIG=cfgp,
                   HEIGE_AGENT_REPORT_HOME=sb,
                   HEIGE_AGENT_REPORT_QUIET_SECONDS="2")
        env.pop("LARK_CHANNEL", None)
        script = os.path.join(HERE, "codex_notify.py")
        for i in range(3):
            subprocess.run([PY, script, json.dumps(
                {"type": "agent-turn-complete",
                 "last-assistant-message": f"turn {i}"})],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pending = os.path.join(sb, ".codex_pending.json")
        check("codex: 事件已入队", os.path.exists(pending))
        # 等待静默窗口 + 发送重试(1次)完成
        deadline = time.time() + 12
        while time.time() < deadline and os.path.exists(pending):
            time.sleep(0.5)
        check("codex: pending 已被 flush", not os.path.exists(pending))
        time.sleep(2)  # 等 worker 写完日志
        logtxt = open(logp).read() if os.path.exists(logp) else ""
        check("codex: 聚合为一次发送", logtxt.count("give up") == 1)
        check("codex: 聚合计数正确", "for 3 turn event(s)" in logtxt)

    # 7) enqueue 写到一半时 flusher 不得抢走半文件并丢失新事件
    import codex_notify
    with tempfile.TemporaryDirectory() as sb:
        pending = os.path.join(sb, ".codex_pending.json")
        original_pending = codex_notify.PENDING
        original_dump = codex_notify.json.dump
        original_load_config = codex_notify.notify_lib.load_config
        original_quiet_seconds = codex_notify.quiet_seconds
        original_send_markdown = codex_notify.notify_lib.send_markdown
        original_log = codex_notify.notify_lib.log
        dump_started = threading.Event()
        allow_dump = threading.Event()
        sent = []

        def delayed_dump(*args, **kwargs):
            dump_started.set()
            allow_dump.wait(timeout=3)
            return original_dump(*args, **kwargs)

        codex_notify.PENDING = pending
        codex_notify.json.dump = delayed_dump
        codex_notify.notify_lib.load_config = lambda: {}
        codex_notify.quiet_seconds = lambda _cfg: 0
        codex_notify.notify_lib.send_markdown = lambda markdown, _cfg: sent.append(markdown)
        codex_notify.notify_lib.log = lambda *_args: None
        try:
            writer = threading.Thread(
                target=codex_notify.enqueue,
                args=({"last-assistant-message": "race-marker"},),
            )
            writer.start()
            check("codex 竞态: writer 到达序列化屏障", dump_started.wait(timeout=2))
            flusher = threading.Thread(target=codex_notify.flush)
            flusher.start()
            time.sleep(0.1)
            allow_dump.set()
            writer.join(timeout=3)
            flusher.join(timeout=3)
            pending_marker = ""
            if os.path.exists(pending):
                pending_marker = open(pending, encoding="utf-8").read()
            delivered = any("race-marker" in message for message in sent)
            check("codex 竞态: 新事件仍可交付", delivered or "race-marker" in pending_marker)
        finally:
            allow_dump.set()
            codex_notify.PENDING = original_pending
            codex_notify.json.dump = original_dump
            codex_notify.notify_lib.load_config = original_load_config
            codex_notify.quiet_seconds = original_quiet_seconds
            codex_notify.notify_lib.send_markdown = original_send_markdown
            codex_notify.notify_lib.log = original_log

    # 8) --agents 必须完整校验后才开始安装事务
    invalid_agent_values = (
        "",
        "claud",
        "claude,claude",
        "claude,unknown",
        "claude,",
        "claude,codex,",
        ",claude",
        "claude,,codex",
    )
    for value in invalid_agent_values:
        with tempfile.TemporaryDirectory() as sb:
            home = os.path.join(sb, "home")
            install_dir = os.path.join(sb, "install")
            fake_lark = os.path.join(sb, "lark-cli")
            open(fake_lark, "w").write("#!/usr/bin/env sh\nexit 0\n")
            os.chmod(fake_lark, 0o755)
            env = dict(
                os.environ,
                HOME=home,
                HEIGE_AGENT_REPORT_HOME=install_dir,
            )
            result = subprocess.run(
                [
                    "bash",
                    os.path.join(os.path.dirname(HERE), "install.sh"),
                    "--open-id",
                    "ou_test",
                    "--lark-cli",
                    fake_lark,
                    "--agents",
                    value,
                ],
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
            )
            untouched = (
                not os.path.exists(install_dir)
                and not os.path.exists(os.path.join(home, ".claude"))
                and not os.path.exists(os.path.join(home, ".codex"))
            )
            check(f"安装器 agents={value!r}: 非零退出", result.returncode != 0)
            check(f"安装器 agents={value!r}: 失败前无写入", untouched)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败")
        sys.exit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()
