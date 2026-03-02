\# TrendAgent

## Secret Setup

Copy `secret.example.json` to `secret.json` in the project root and fill in these keys:

- `telegram_bot_token`
- `discord_webhook_url`
- `gmail_app_password`

`secret.json` is gitignored and must never be committed.

All non-secret behavior lives in `Json/config.json`.

- Set `telegram_stay_alive` to control whether runs that start Telegram should idle after completion.
- Set `open_browser` to control whether successful non-dev runs open the generated HTML report locally.
- Email and Discord delivery toggles remain in `Json/config.json` under `email.enabled` and `discord.enabled`.



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

| Json/config.json    | RSS sources \& configuration |

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

模式	跑 pipeline	发邮件	发 Discord	启动 Telegram	退出？

--telegram	❌	❌	❌	✅	不退出
--once	✅	✅	✅	❌	退出
--dev	✅	❌	❌	❌	退出
Telegram report	✅	✅	✅	已在运行	不退出



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

