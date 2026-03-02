Perfect. Here is a minimal, clean context.md template tailored to your current TrendAgent architecture.


at project root.


---

TrendAgent – Operational Context

1. Project Structure (High Level)

trend_agent/
│
├─ main.py
├─ telegram_poll.py
├─ secrets_loader.py
├─ config_loader.py
│
├─ secret.json           (PRIVATE – gitignored)
├─ secret.example.json   (safe template)
│
├─ Json/
│   └─ config.json       (behavior only)
│
├─ memory/
│   └─ locks/run.lock
│
└─ README.md


---

2. Configuration Model (STRICT SEPARATION)

Secrets → secret.json (private, gitignored)

Required keys:

{
  "telegram_bot_token": "...",
  "discord_webhook_url": "...",
  "gmail_app_password": "..."
}

Rules:

Never commit this file.

Never print secrets to logs.

No environment variables are used anymore.

.env is deprecated and removed.



---

Behavior → Json/config.json

Example:

{
  "telegram_stay_alive": true
}

Rules:

Only non-secret behavior flags go here.

Safe to commit.

Controls runtime behavior, not credentials.



---

3. Run Modes

--telegram

python main.py --telegram

Starts Telegram polling.

Stays idle until command received.

Does NOT auto-run pipeline.

Ctrl+C to stop.



---

--once

python main.py --once

Runs full pipeline once.

Sends email + Discord.

Exits.

Used by Task Scheduler.



---

--dev

python main.py --dev

Runs pipeline once.

Skips email/Discord/browser.

Safe local testing.

Exits.



---

4. Locking System

File:

memory/locks/run.lock

Purpose:

Prevent concurrent runs.

If a run is active, new run exits cleanly.

Telegram-triggered runs respect lock.



---

5. Telegram Commands

Current supported:

ping

status

report

highlights

help


Telegram only triggers runs. It does NOT auto-send reports unless commanded.


---

6. Task Scheduler Setup

Daily automation should run:

python main.py --once

Never use:

--telegram

in Task Scheduler.


---

7. Design Principles

Secrets isolated.

Behavior isolated.

Explicit run modes.

Cross-process lock enforced.

No hidden environment dependencies.

Small Codex tasks only.

Minimal blast radius changes.



---

8. Debug Checklist

If Telegram shows 409 Conflict:

Kill all python processes.

Only one polling session allowed.


If secrets error:

Check secret.json exists.

Check required keys exist.

Confirm key names exactly match.


If run refuses:

Check lock file.

Another process may be running.



---


---

This file becomes your:

> Mental compression layer.



Instead of remembering everything, you maintain structure in one place.


---

If you want next step, we can:

Make a tiny ARCHITECTURE.md (long-term vision)

Or leave it stable and build the next feature.


Your system is now clean enough to scale.