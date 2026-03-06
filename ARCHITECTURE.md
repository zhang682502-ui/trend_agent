# ARCHITECTURE.md

This document describes the intended high-level runtime architecture for TrendAgent.

## Cloud-First Message Flow

```text
Telegram message
      ↓
main.py
      ↓
Intent Split
      ↓
CHAT        → Cloud LLM
SUMMARY     → Cloud LLM
COMMAND     → local deterministic rule path
TOOLS       → execution layer
```

## Routing Rules

### Local deterministic command path

Only these exact slash commands stay local:

- `/status`
- `/help`
- `/report`

These commands are handled directly in local Python without LLM interpretation.

### Chat path

Normal conversational messages should use the cloud provider.

Examples:

- `hi`
- `can we chat`
- `你好`
- `thanks`

The chat path should produce a natural conversational reply from the cloud LLM.

### Summary path

Summary requests should use the cloud provider.

Examples:

- `summarize the latest report`
- `today's news`
- `read the latest report`
- `总结最新报告`

Local code may fetch report text or files, but the semantic summarization should be performed by the cloud LLM.

### Tools / execution layer

The execution layer remains local and is responsible for:

- running the pipeline
- fetching latest report text or files
- managing pending confirmations
- managing runtime state and safety rails

The execution layer should not be the primary semantic interpreter.

## Provider Boundary

### Cloud provider

Used by default for:

- chat
- report/news summarization
- explanation of report content

### Local provider

Not the default for chat or summary.

It is reserved for:

- optional future local mode
- fallback mode
- explicitly requested local-provider workflows

## Long-Term Direction

Intent splitting is intended to become cloud-LLM-driven in the long term, while fixed slash commands remain deterministic and local.

That means:

- local exact commands stay local
- cloud handles semantic interpretation
- local tools execute the structured result
