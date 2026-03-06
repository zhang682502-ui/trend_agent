Read CHATGPT.md and continue TrendAgent development.

# CHATGPT.md

Project: TrendAgent

Purpose
TrendAgent is a personal AI agent system under development.
The goal is to run a Telegram-based agent that can:

- chat with the user
- read generated reports
- summarize news
- trigger tools and pipelines

Core Architecture

Telegram message
↓
Intent / Controller (cloud LLM)
↓
Tool execution layer
↓
Pipelines and utilities

Provider Rules

Controller → cloud LLM  
Chat → cloud LLM  
Summary → cloud LLM  

Only fixed commands remain local:

/status  
/help  
/report  

Local models (Ollama) must NOT be used for controller logic.

Environment

Configuration is stored in `.env`.

Required variables:

TREND_OPENAI_API_KEY  
TREND_CHAT_PROVIDER  
TREND_SUMMARY_PROVIDER  
TREND_OPENAI_CHAT_MODEL  
TREND_OPENAI_SUMMARY_MODEL  

Startup

Telegram agent:

.\start_telegram.ps1

Pipeline run:

.\start_pipeline.ps1

Development mode:

.\start_dev.ps1

Development Workflow

When making code changes:

1. Follow AGENTS.md rules.
2. Respect architecture defined in ARCHITECTURE.md.
3. Make minimal safe modifications.
4. Every Codex task must produce a SESSION REPORT.

Important

Do NOT redesign architecture unless explicitly requested.
Focus on getting the agent running reliably.

Context Recovery Instructions

When this file is provided at the start of a conversation:

1. Read this file and treat it as the active project context.
2. Assume the project is TrendAgent and continue development from this architecture.
3. Before suggesting any code change, ensure it follows AGENTS.md rules and does not break the defined architecture.