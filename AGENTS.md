# AGENTS.md

This repository is developed with the help of AI coding agents (Codex).

All agents must follow these rules when modifying this codebase.

---

## Project Roadmap

Agents must respect the project development roadmap:

1. Provider Abstraction Layer
2. Chat Agent (Conversation Interface)
3. Memory / Context Window
4. Multi-Step Reasoning / Planning Agent
5. Tooling & Command Surface
6. Personal AI System Interface
7. Deeper Architecture Problem

Agents should implement **only the requested reminder step** and must not redesign other layers unless explicitly instructed.

---

## Coding Principles

- Prefer minimal safe refactors
- Do not redesign working systems
- Do not introduce new dependencies unless necessary
- Preserve existing environment variable behavior
- Preserve CLI workflows
- Keep Telegram integration stable
- Keep report generation stable

---

## Git Workflow

Agents may create micro commits during development.

Final commits should clearly describe the implemented feature.

Example:

Implement Reminder 2 chat agent path

---

## Mandatory Session Report

Before finishing any task, agents MUST produce a session report.

Use the following format:

SESSION REPORT

Commit
- commit hash
- commit message

Changed Files
- list of files modified
- list of files created

Commands Executed
- terminal commands run during the task

Validation
- compile checks
- tests run
- runtime verification

Architecture Impact
- which reminder step this task belongs to

Environment Variables
- env vars used or modified

Notes
- assumptions made
- potential risks

---

## Safety Rules

Agents must avoid:

- breaking Telegram integration
- breaking report generation
- redesigning controller logic unless explicitly requested
- introducing architecture changes outside the requested reminder step

---

After creating AGENTS.md:
1. Save it at repository root
2. Run `git add AGENTS.md`
3. Commit with message:

Add AGENTS.md development rules for AI coding agents

Then produce the mandatory SESSION REPORT.
