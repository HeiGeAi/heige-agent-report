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

    # 3) 送信 worker 优雅失败：lark-cli 不可用时应重试→记日志→退出 0，绝不崩
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

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败")
        sys.exit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()
