\# TrendAgent

## Secret Setup

Copy `secret.example.json` to `secret.json` in the project root and fill in these keys:

- `telegram_bot_token`
- `discord_webhook_url`
- `gmail_app_password`

`secret.json` is gitignored and must never be committed.

All non-secret behavior lives in `config/config.json`.

- Set `telegram_stay_alive` to control whether runs that start Telegram should idle after completion.
- Set `open_browser` to control whether successful non-dev runs open the generated HTML report locally.
- Email and Discord delivery toggles remain in `config/config.json` under `email.enabled` and `discord.enabled`.
- Set `discord.single_message` to `true` to keep one webhook message per channel and edit that same message on each run.
- Single-message state is stored per webhook in `Json/discord_single_message_<sha256(webhook_url)[:12]>.json`.



\## 1. Vision



TrendAgent is a lightweight local automation agent designed to:



\* Monitor information streams (RSS feeds)

\* Analyze and structure trends

\* Generate daily reports

\* Maintain execution memory

\* Evolve toward an autonomous AI agent



It begins as a script, but evolves toward an agent architecture.



---



\## 2. System Architecture



TrendAgent currently runs as a local single-node agent with scheduled execution.



\### Runtime Flow



```

Task Scheduler

&nbsp;     ↓

&nbsp;  main.py

&nbsp;     ↓

Fetch RSS feeds

&nbsp;     ↓

Generate Markdown report

&nbsp;     ↓

Generate HTML report

&nbsp;     ↓

Update Json/status.json

&nbsp;     ↓

Append Json/history.json

```



---



\## 3. File Structure



| File                | Role                        |

| ------------------- | --------------------------- |

| main.py             | Core execution engine       |

| Json/               | JSON data folder             |

| config/config.json  | RSS sources \& configuration |

| Json/status.json    | Current state snapshot      |

| Json/history.json   | Execution memory log        |

| logs/               | Runtime logs                |

| report/             | Markdown reports folder     |
 
| report_html/        | HTML reports folder         |
 
| report/trend\_report\_\*.md | Markdown reports      |
 
| report_html/trend\_report\_\*.html | HTML reports   |



---



\## 4. Current Capabilities



✔ Manual execution

✔ Windows Task Scheduler automation

✔ Markdown report generation

✔ HTML report generation

✔ Status tracking

✔ Historical run memory



---



\## 5. Agent State Model (Planned Upgrade)



Current state:



```json

{

&nbsp; "last\_run\_time": "",

&nbsp; "status": "success | failure",

&nbsp; "items\_processed": 0

}

```



Future agent state model:



```json

{

&nbsp; "agent\_id": "TrendAgent-Local-01",

&nbsp; "last\_run": "",

&nbsp; "memory": {

&nbsp;   "total\_runs": 0,

&nbsp;   "failure\_count": 0

&nbsp; },

&nbsp; "health": "healthy | degraded | failed",

&nbsp; "next\_scheduled\_run": ""

}

```



This allows:



\* Agent health tracking

\* Self-monitoring

\* Future orchestration



---



\## 6. Next Evolution Phases



\### Phase 1 – Agent Refinement (Local)



\* Structured state model

\* Better logging granularity

\* Error classification

\* Retry mechanism



\### Phase 2 – Communication Layer



\* Email report delivery

\* Webhook integration

\* Slack / Telegram notifications



\### Phase 3 – Cloud Agent Architecture



\* Cloud deployment

\* Containerization (Docker)

\* API endpoint

\* Multi-agent orchestration



---



\## 7. Long-Term Vision



TrendAgent becomes:



\* A modular AI-powered trend monitoring system

\* Capable of multiple data sources

\* With persistent memory

\* Possibly running LLM analysis

\* Operating as part of a distributed agent network



---

## 🧠 AI-assisted Development Architecture/workflow

This project uses a multi-model AI workflow to maximize efficiency and capability.

Each AI system is used according to its strengths:

- **DeepSeek (VS Code Integration)**  
  Real-time coding assistance and implementation support.

- **Claude (Web Interface)**  
  Advanced reasoning, debugging, and code quality improvement.

- **Perplexity**  
  Research, documentation lookup, and technical investigation.

- **ChatGPT**  
  System architecture design and workflow planning.

This orchestration approach allows separation of concerns and reduces cost while maintaining high development quality.

                ┌──────────────┐
                │   Perplexity  │
                │  (Research)   │
                └──────┬───────┘
                       │
                       │
┌──────────────┐       │       ┌──────────────┐
│   ChatGPT    │───────┼──────▶│   Claude     │
│ Architecture │       │       │ Debug/Review │
└──────┬───────┘       │       └──────┬───────┘
       │               │              │
       │               ▼              │
       │        ┌──────────────┐      │
       └──────▶ │  DeepSeek     │◀─────┘
                │  VS Code Dev  │
                └──────────────┘




TrendAgent Run Modes

TrendAgent supports three clean execution modes. Each mode has a different purpose and behavior. Always choose the correct mode before running.

1) --telegram
Command:
    python main.py --telegram

Purpose:
    Starts Telegram polling only. The agent stays idle and waits for Telegram commands.

Behavior:
    - Does NOT auto-run the pipeline.
    - Does NOT send email or Discord unless triggered by Telegram.
    - Keeps running until Ctrl+C.
    - When a Telegram command like "report" is received, it triggers a single pipeline run.
    - Protected by cross-process lock (memory/locks/run.lock), so it will not clash with another active run.

Use this mode for:
    Normal daily operation. This is the always-on listener.


2) --once
Command:
    python main.py --once

Purpose:
    Runs the full pipeline once and exits.

Behavior:
    - Fetches RSS.
    - Generates report files.
    - Sends email.
    - Sends Discord webhook.
    - Opens local HTML in browser.
    - Exits after completion.
    - Will refuse to run if another process holds the run.lock.

Use this mode for:
    Manual production run from terminal.


3) --dev
Command:
    python main.py --dev

Purpose:
    Safe development/testing mode.

Behavior:
    - Fetches RSS.
    - Generates report files locally.
    - Does NOT send email.
    - Does NOT send Discord.
    - Does NOT open browser.
    - Exits after completion.
    - Still respects run.lock.

Use this mode for:
    Testing changes without sending notifications.


Important:
Only one pipeline run can exist at a time. If another run is active, the agent will refuse cleanly instead of crashing.


中文讲清楚。我们只讲 --dev。


---

你运行：

python main.py --dev

它做什么？


---

✅ 它会做的事情

1️⃣ 完整跑一遍 pipeline

包括：

抓 RSS

去重

生成报告

写 status.json

写 history.json

生成本地 HTML / MD 文件


它是一个“完整逻辑运行”。


---

2️⃣ 使用锁

如果有别的 run 正在运行，它会拒绝

不会并发



---

3️⃣ 写日志

会记录 run 成功/失败

会写 duration

会更新 memory



---

❌ 它不会做的事情

非常关键：

❌ 不会发邮件

❌ 不会发 Discord

❌ 不会触发 Telegram 消息

❌ 不会打开浏览器

❌ 不会启动 Telegram polling


---

一句话总结

--dev =

> 在你电脑本地完整模拟一次真实运行
但不对外发送任何东西




---

和其他模式对比

模式	         跑 pipeline	发邮件	发 Discord	启动 Telegram	退出？

--telegram	    ❌	           ❌	❌	      ✅	            不退出
--once	        ✅              ✅	✅	      ❌	            退出
--dev	        ✅	           ❌	❌	      ❌         	退出
Telegram report	✅	           ✅	✅	      已在运行	      不退出



---

什么时候用 --dev？

你改了：

格式

模板

去重逻辑

高亮生成

文件路径

memory 逻辑


你想看：

> “跑完之后结构对不对”



但你不想：

再给 Discord 发 20 条

再给自己发邮件

再污染 Telegram


那就用 --dev


---

本质理解

--dev 是：

> 安全测试沙箱模式



--once 是：

> 真实生产执行模式



--telegram 是：

> 监听模式




---

可以，而且你这个直觉很对：同一种“干净结构”应该从 delivery 延伸到 core、main。但做法要稍微换一下——因为 core 和 delivery 的职责不同，结构的“分区方式”也应该不同，否则会变成“为了整齐而整齐”。

下面我直接给你一套 可复制的结构模板，你以后每个小程序都能用（对你、对 Codex 都好理解）。


---

1) Core 的 3 个 .py：要改成什么样？

Core 的原则

Core 里不要出现：

任何 webhook / email / telegram

任何文件路径写死（尽量通过参数传入）

任何“全局状态写入”（status/history 尽量在 agent/controller 层）


Core 里应该是：

> 纯逻辑：输入 → 输出（结构化对象）



每个 core 文件推荐结构

把每个 core 模块写成 3 段：

1. Types / 数据结构（dataclass / TypedDict）


2. Pure functions（不做 IO）


3. One public API（对外暴露的主函数）



示例骨架：

core/rss_fetcher.py
- imports
- types (FeedItem, FetchResult)
- helpers (_normalize_url, _safe_parse_date)
- public function: fetch_rss(feeds, http_client, timeout) -> FetchResult

core/processor.py
- imports
- types (ProcessedItem, ReportModel)
- helpers (_rank, _dedupe)
- public function: build_report_model(items, config) -> ReportModel

core/reporter.py
- imports
- types (RenderInput, RenderOutputPaths)
- helpers (_render_md, _render_html)
- public function: render_reports(report_model, output_dir, templates) -> RenderOutputPaths

> 重点：core 的 helper 是“算法/格式化的小工具”，不是“网络/文件发送工具”。




---

2) main.py 要不要“也改成这种结构”？

要，但 main 的结构不是“helper + sender”，而是：

main.py 的职责

main.py 应该尽量薄，做 4 件事：

1. 读配置/密钥


2. 组装依赖（logger、http client、路径）


3. 调用 agent/controller（跑一次）


4. 返回 exit code（成功/失败）



也就是：

main.py
- parse args
- load config/secrets
- run_once()
- if __name__ == "__main__": sys.exit(main())

main 不应该包含业务逻辑（不要在 main 里写“如何处理 RSS/如何渲染/如何发 discord”）。


---

3) 你现在最该怎么做（不推翻重来）

你现在 core 已经有 3 个点.py（我理解是 3 个核心模块），建议你按“最小迁移”做：

第一步：只做“文件内分区”

不改文件名、不拆模块，只在每个文件里加清晰分区：

# Types

# Helpers

# Public API


马上就会更干净，而且风险最小。

第二步：把 IO 往外挪

如果 core 里有：

open(...)

requests.get(...)

写 Json/ 的状态文件


把它们慢慢挪到 agent/controller 或 delivery。

第三步：统一“核心输出模型”

让 core 最终输出一个统一对象（比如 ReportModel），render/delivery 都只吃这个对象。


---

4) 你说“对机器也好理解”——是的

对 Codex 来说，这种结构特别友好，因为：

每个函数职责单一

公共入口明确

helper 封装细节

变更时不容易误伤别的逻辑


这会显著降低它“改一处崩全局”的概率。


---

5) 你可以直接把这段也丢给 Codex（可选）

如果你想让 Codex 之后按这个风格统一 core，可以给它一句“风格指令”：

> “Please refactor each core module into three sections: Types, Helpers, Public API. Keep IO out of core; core should be pure transformations. Expose exactly one public function per module; all other helpers start with underscore.”




---
Stage 1
本地 LLM
+ 结构化思考
+ Telegram

Stage 2
加入云端 LLM Router

Stage 3
Agent 自动决策