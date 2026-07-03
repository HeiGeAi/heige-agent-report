# heige-agent-report

<div align="center">

![License](https://img.shields.io/badge/license-MIT-64748b.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%C2%B7%20Linux-0e7490.svg)
![Agents](https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex-7c3aed.svg)
![Transport](https://img.shields.io/badge/transport-Lark%20CLI-f97316.svg)
![Deps](https://img.shields.io/badge/deps-zero%20(stdlib)-16a34a.svg)

**AI Agent 干完活，自动在飞书跟你汇报 | Your coding agents report to Feishu/Lark the moment a task is done**

用引擎级钩子，而不是模型自觉：Claude Code 结束一轮、Codex 结束一轮，都由各自的原生机制强制触发，把结果摘要实时推到你的飞书。

[这是什么](#这是什么-what-is-this) • [为什么可靠](#为什么可靠-why-its-reliable) • [工作原理](#工作原理-how-it-works) • [快速开始](#快速开始-quick-start) • [配置](#配置-configuration) • [卸载](#卸载-uninstall) • [致敬](#致敬-credits) • [English](#english)

</div>

---

## 这是什么 What is this

你在 Claude Code 桌面端、VSCode 里的 Claude Code、或者 Codex 里丢一个长任务，然后去忙别的。任务做完的那一刻，飞书弹出一条消息，带上这轮干了什么的摘要。就这么一件事，做到确定性触发、多端统一、不打扰。

它只做**一个方向**：agent → 你。在飞书上反向给 agent 下任务的能力被刻意去掉了，因为日常工作场景是在各种端口里干活、只需要一个统一的收口来接结果。少一个入口，反而更顺手。

整条链路走飞书官方 CLI（lark-cli），绑一个机器人给你发消息。零第三方依赖，纯标准库 Python。

## 为什么可靠 Why it's reliable

市面上让 agent 发通知的做法，多数是在提示词或规则文件里写一句「任务完成后发条消息」，靠模型自己记得去发。模型经常忘，于是通知时有时无。

这个产品换了地基：**用每个 agent 的引擎级钩子，绕开模型的自觉**。

| | 做法 | 是否每次都发 |
|---|---|---|
| **Claude Code** | `Stop` hook，会话每轮结束由引擎强制调用 | ✅ 确定性 |
| **Codex** | `notify` 钩子，一轮任务结束由 Codex 原生触发 | ✅ 确定性 |
| 传统规则式 | AGENTS.md / 提示词里写「记得发」 | ❌ 看模型心情 |

几个实测过的细节：

- **不刷屏**：Claude Code 侧带耗时闸门，只有单轮真干了活（默认 ≥ 45 秒）才推，快速问答不打扰。
- **不拖慢**：发送全程异步（脱离进程组的 fire-and-forget），钩子 0.07 秒返回，你的 agent 一点不卡。
- **不抢占**：Codex 若已挂了别的 `notify`（比如电脑操作通知器），安装时自动把它捕获成转发目标，原功能照常。
- **不添乱**：任何异常静默、退出 0，通知发失败也绝不影响 agent 本体。

## 工作原理 How it works

```
Claude Code (桌面端 / VSCode / CLI)
        │  会话每轮结束 → Stop hook（引擎强制）
        ▼
   claude_stop_hook.py ──┐
                         ├─→ notify_lib ──→ lark-cli ──→ 飞书机器人 ──→ 你
   codex_notify.py    ──┘         （异步发送，读本地 config.json）
        ▲
        │  一轮任务结束 → notify 钩子（Codex 原生）
Codex
```

- `claude_stop_hook.py`：读 Claude Code 的 transcript，算出本轮耗时、抽取最后的结果文本，过闸门后发送。
- `codex_notify.py`：接 Codex 传来的 JSON，抽取 last-assistant-message 发送，并把原始事件转发给你原有的 notify 程序。
- `notify_lib.py`：共享发送层，读 `~/.heige-agent-report/config.json`，通过 lark-cli 异步发一条 Markdown。

## 快速开始 Quick start

**前置**：装好并登录飞书官方 CLI。

```bash
npm i -g @larksuite/cli
lark-cli auth login
```

**安装**（不指定 open_id 时，自动读取当前登录用户）：

```bash
git clone https://github.com/HeiGeAi/heige-agent-report.git
cd heige-agent-report
bash install.sh
```

指定接收人、只装某个 agent、或调闸门：

```bash
bash install.sh --open-id ou_xxx --agents claude,codex --min-seconds 60
bash install.sh --agents claude          # 只装 Claude Code
```

安装后：

1. **新开一个 Claude Code 会话**（hook 在会话启动时加载，首次可能需要确认一次 hook 变更）。
2. **重启 Codex 会话**让 `config.toml` 生效，notify 在交互式会话结束一轮时触发。
3. 丢个稍长的任务，去喝口水，等飞书弹消息。

## 配置 Configuration

配置在 `~/.heige-agent-report/config.json`，改完即时生效（钩子每次运行都重读）：

| 字段 | 说明 | 默认 |
|---|---|---|
| `open_id` | 接收通知的飞书 open_id | 安装时自动填 |
| `identity` | 发送身份，`bot` 或 `user` | `bot` |
| `lark_cli` | lark-cli 可执行路径 | `lark-cli` |
| `min_seconds` | Claude Code 单轮耗时闸门（秒），`0` = 每轮都推 | `45` |
| `codex_chain` | Codex 原有 notify 程序，安装时自动捕获 | `[]` |

也可用环境变量临时覆盖闸门：`HEIGE_AGENT_REPORT_MIN_SECONDS=0`。

## 卸载 Uninstall

```bash
bash uninstall.sh
```

会移除 Claude Code 的 Stop hook、把 Codex 的 notify 还原成安装前的原值，并删除安装目录。`--keep-files` 可保留脚本目录。

## 致敬 Credits

这个产品站在几位开发者的肩膀上，把「桥能用」这件事，收敛成一个稳定、只做通知的日常形态：

- **[derrickgong87 / codex-lark-deliver](https://github.com/derrickgong87/codex-lark-deliver)** — 最早把 Codex 的飞书完成通知与文件交付做成可复用 skill 和安装器。本项目是它「只留通知、改用确定性钩子、扩到多 agent」的演化。
- **[Zara Zhang（zarazhangrui）/ lark-coding-agent-bridge](https://github.com/zarazhangrui/lark-coding-agent-bridge)** — 把飞书与本地编程 agent（Claude Code / Codex）打通的桥，是 agent 与飞书集成的灵感来源。
- **飞书 / Lark 官方 [@larksuite/cli](https://www.npmjs.com/package/@larksuite/cli)** — 提供命令行访问飞书消息、文档、文件的能力，是本项目的传输底座。

心意在此，若有取舍不当之处，欢迎提 issue。

## English

**heige-agent-report** pushes a Feishu/Lark message the moment your coding agent finishes a task — with a summary of what it did.

It does one thing, one direction: agent → you. Sending tasks back to the agent from chat is intentionally removed; a single, unobtrusive place to collect results fits daily work better.

The point is reliability. Instead of asking the model to remember to send a notice (prompt/rules based, often skipped), it hooks each agent's **engine-level completion mechanism**:

- **Claude Code** — a `Stop` hook, fired by the engine at the end of every turn (desktop, VSCode, CLI).
- **Codex** — the native `notify` hook, fired when a turn completes. Any pre-existing `notify` program is captured and chained, so it keeps working.

Notices are duration-gated (Claude Code, default ≥ 45s) to avoid noise, sent fully asynchronously so they never slow the agent, and fail silently so they never break it. Zero third-party dependencies (Python stdlib only); the transport is the official Lark CLI.

```bash
npm i -g @larksuite/cli && lark-cli auth login
git clone https://github.com/HeiGeAi/heige-agent-report.git && cd heige-agent-report
bash install.sh          # then open a fresh Claude Code session / restart Codex
```

See [Quick start](#快速开始-quick-start) and [Configuration](#配置-configuration) above. Uninstall with `bash uninstall.sh`.

## License

MIT © 2026 HeiGeAi (Blake Xu)。详见 [LICENSE](LICENSE)。随便用、随便改、随便分享。

Built on and grateful to codex-lark-deliver, lark-coding-agent-bridge, and the official Lark CLI. See [Credits](#致敬-credits).
