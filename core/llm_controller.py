from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from tools.ollama_cli import run_ollama


logger = logging.getLogger("trend_agent")
CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
ALLOWED_INTENTS = {"CHAT", "FULL_REPORT_SUMMARY", "RUN_PIPELINE", "CLARIFY"}
ALLOWED_TOOLS = {"get_latest_report", "summarize_report_full", "run_pipeline", "chat_with_context"}

RUN_CONFIRM_ZH = (
    "我可以现在把完整流程跑一遍：抓 RSS、生成报告，并按你现在的设置发邮件/更新 HTML。要我现在开始吗？"
)
RUN_CONFIRM_EN = (
    "I can run the full pipeline now: fetch RSS, generate the report, and publish using your current settings. "
    "Do you want me to start now?"
)
AMBIGUOUS_CONFIRM_ZH = "你是想我直接把最新报告读完后给你一段总摘要，还是想我先重新生成一份新的报告再总结？"
AMBIGUOUS_CONFIRM_EN = (
    "Do you want me to summarize the latest existing report now, or regenerate a new report first and then summarize it?"
)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return max(minimum, int(default))
    try:
        return max(minimum, int(raw))
    except Exception:
        return max(minimum, int(default))


def detect_language(text: str) -> str:
    cjk_count = len(CJK_RE.findall(text or ""))
    return "zh" if cjk_count >= 20 else "en"


def _pick_chat_model(text: str) -> str:
    if detect_language(text) == "zh":
        return str(os.getenv("TREND_LLM_ZH_MODEL", "qwen2") or "qwen2").strip() or "qwen2"
    return str(os.getenv("TREND_LLM_EN_MODEL", "llama3") or "llama3").strip() or "llama3"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = raw[start : idx + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return None
    return None


def _normalize_plan(plan: dict[str, Any] | None, *, chat_id: int, default_lang: str) -> dict[str, Any]:
    base = {
        "intent": "CHAT",
        "actions": [{"tool": "chat_with_context", "args": {"mode": "followup"}}],
        "needs_confirmation": False,
        "confirmation_prompt": None,
        "store_context": False,
        "context_id": str(chat_id),
    }
    if not isinstance(plan, dict):
        return base

    normalized = dict(base)
    intent = str(plan.get("intent") or "").strip().upper()
    if intent in ALLOWED_INTENTS:
        normalized["intent"] = intent

    actions_raw = plan.get("actions")
    actions: list[dict[str, Any]] = []
    if isinstance(actions_raw, list):
        for item in actions_raw:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if tool not in ALLOWED_TOOLS:
                continue
            args = item.get("args")
            actions.append({"tool": tool, "args": args if isinstance(args, dict) else {}})
    if actions:
        normalized["actions"] = actions

    normalized["needs_confirmation"] = bool(plan.get("needs_confirmation", False))
    confirmation_prompt = plan.get("confirmation_prompt")
    normalized["confirmation_prompt"] = str(confirmation_prompt).strip() if isinstance(confirmation_prompt, str) and confirmation_prompt.strip() else None
    normalized["store_context"] = bool(plan.get("store_context", False))
    context_id = plan.get("context_id")
    normalized["context_id"] = str(context_id).strip() if isinstance(context_id, str) and context_id.strip() else str(chat_id)

    if normalized["intent"] == "RUN_PIPELINE" and normalized["confirmation_prompt"] is None:
        normalized["confirmation_prompt"] = RUN_CONFIRM_ZH if default_lang == "zh" else RUN_CONFIRM_EN
        normalized["needs_confirmation"] = True
    if normalized["intent"] == "FULL_REPORT_SUMMARY" and not normalized["actions"]:
        normalized["actions"] = [
            {"tool": "get_latest_report", "args": {}},
            {"tool": "summarize_report_full", "args": {"topic_hint": None}},
        ]
        normalized["store_context"] = True
    if normalized["intent"] == "RUN_PIPELINE" and not normalized["actions"]:
        normalized["actions"] = [{"tool": "run_pipeline", "args": {}}]
    if normalized["intent"] == "CHAT" and not normalized["actions"]:
        normalized["actions"] = [{"tool": "chat_with_context", "args": {"mode": "followup"}}]
    if normalized["intent"] == "CLARIFY":
        normalized["needs_confirmation"] = True
        if not normalized["confirmation_prompt"]:
            normalized["confirmation_prompt"] = AMBIGUOUS_CONFIRM_ZH if default_lang == "zh" else AMBIGUOUS_CONFIRM_EN
        normalized["actions"] = []

    return normalized


def _fallback_controller(text: str, *, chat_id: int, meta: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    lowered = (text or "").strip().lower()
    lang = detect_language(text)
    has_context = bool((meta or {}).get("context"))
    run_hit = any(token in lowered for token in ("run", "report", "rss", "refresh", "pipeline", "生成", "重跑", "跑一下", "跑一遍"))
    summary_hit = any(token in lowered for token in ("summary", "summarize", "overview", "通读", "总结", "概览"))
    report_hit = any(token in lowered for token in ("report", "报告"))

    if run_hit and summary_hit:
        plan = {
            "intent": "CLARIFY",
            "actions": [],
            "needs_confirmation": True,
            "confirmation_prompt": AMBIGUOUS_CONFIRM_ZH if lang == "zh" else AMBIGUOUS_CONFIRM_EN,
            "store_context": False,
            "context_id": str(chat_id),
        }
        reply = plan["confirmation_prompt"]
        return reply, plan

    if run_hit:
        plan = {
            "intent": "RUN_PIPELINE",
            "actions": [{"tool": "run_pipeline", "args": {}}],
            "needs_confirmation": True,
            "confirmation_prompt": RUN_CONFIRM_ZH if lang == "zh" else RUN_CONFIRM_EN,
            "store_context": False,
            "context_id": str(chat_id),
        }
        reply = plan["confirmation_prompt"]
        return reply, plan

    if summary_hit or report_hit:
        plan = {
            "intent": "FULL_REPORT_SUMMARY",
            "actions": [
                {"tool": "get_latest_report", "args": {}},
                {"tool": "summarize_report_full", "args": {"topic_hint": None}},
            ],
            "needs_confirmation": False,
            "confirmation_prompt": None,
            "store_context": True,
            "context_id": str(chat_id),
        }
        reply = "我先读最新报告，然后给你一段完整总结。" if lang == "zh" else "I will read the latest report and produce a full summary."
        return reply, plan

    plan = {
        "intent": "CHAT",
        "actions": [{"tool": "chat_with_context", "args": {"mode": "followup"}}],
        "needs_confirmation": False,
        "confirmation_prompt": None,
        "store_context": has_context,
        "context_id": str(chat_id),
    }
    reply = "好的，我结合当前上下文和你继续聊。" if lang == "zh" else "Sure, I will continue with your context."
    return reply, plan


def decide_and_respond(user_text: str, chat_id: int, meta: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    text = (user_text or "").strip()
    if not text:
        return "I need a message to continue.", _normalize_plan(None, chat_id=chat_id, default_lang="en")

    meta = meta or {}
    lang = detect_language(text)
    model = _pick_chat_model(text)
    timeout_s = _env_int("TREND_LLM_CHAT_TIMEOUT_S", 120, minimum=3)

    context = meta.get("context") if isinstance(meta.get("context"), dict) else {}
    context_digest = json.dumps(context, ensure_ascii=False)[:1200] if context else "{}"
    prompt = (
        "You are a Telegram agent controller.\n"
        "Return ONLY valid JSON. No markdown.\n"
        "JSON schema:\n"
        "{"
        '"reply":"<natural response>",'
        '"plan":{'
        '"intent":"CHAT|FULL_REPORT_SUMMARY|RUN_PIPELINE|CLARIFY",'
        '"actions":[{"tool":"get_latest_report|summarize_report_full|run_pipeline|chat_with_context","args":{}}],'
        '"needs_confirmation":true|false,'
        '"confirmation_prompt":"<string or null>",'
        '"store_context":true|false,'
        f'"context_id":"{chat_id}"'
        "}"
        "}\n"
        "Rules:\n"
        "- If user requests running a new report/pipeline, use intent RUN_PIPELINE with needs_confirmation=true.\n"
        "- If user asks to summarize latest report, use FULL_REPORT_SUMMARY.\n"
        "- For follow-up discussion with context, use CHAT + chat_with_context.\n"
        "- If ambiguous between summarize-latest and regenerate, use CLARIFY.\n"
        "- Keep reply short and natural in user's language.\n\n"
        f"User text:\n{text}\n\n"
        f"Existing context:\n{context_digest}\n"
    )

    try:
        logger.info("TG LLM chat timeout_s=%s model=%s", timeout_s, model)
        raw = run_ollama(model=model, prompt=prompt, timeout_s=timeout_s)
        payload = _extract_json_object(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("controller output was not valid JSON")
        reply = str(payload.get("reply") or "").strip()
        plan = _normalize_plan(payload.get("plan") if isinstance(payload.get("plan"), dict) else None, chat_id=chat_id, default_lang=lang)
        if plan["intent"] == "RUN_PIPELINE" and lang == "zh":
            plan["confirmation_prompt"] = RUN_CONFIRM_ZH
        if plan["intent"] == "CLARIFY" and lang == "zh":
            plan["confirmation_prompt"] = AMBIGUOUS_CONFIRM_ZH
        if not reply:
            reply = plan.get("confirmation_prompt") or ("我来处理。" if lang == "zh" else "I will handle that.")
        return reply, plan
    except Exception as exc:
        logger.warning("TG controller fallback chat_id=%s error=%s", chat_id, exc)
        return _fallback_controller(text, chat_id=chat_id, meta=meta)


def chat_with_context(user_text: str, context: dict[str, Any] | None = None, meta: dict[str, Any] | None = None) -> str:
    text = (user_text or "").strip()
    if not text:
        return "Please send a message."
    lang = detect_language(text)
    model = _pick_chat_model(text)
    timeout_s = _env_int("TREND_LLM_CHAT_TIMEOUT_S", 120, minimum=3)
    context = context if isinstance(context, dict) else {}
    context_snippet = json.dumps(context, ensure_ascii=False)[:1600] if context else "{}"
    prompt = (
        "You are a concise assistant discussing a trend report context.\n"
        "Reply naturally and keep to 3-8 sentences.\n"
        "If context is empty, answer generally and ask whether to generate a full summary first.\n\n"
        f"Context:\n{context_snippet}\n\n"
        f"User:\n{text}\n"
    )
    try:
        logger.info("TG LLM chat timeout_s=%s model=%s", timeout_s, model)
        reply = run_ollama(model=model, prompt=prompt, timeout_s=timeout_s).strip()
        if reply:
            return reply
    except Exception as exc:
        logger.warning("TG context chat fallback error=%s", exc)
    if lang == "zh":
        return "我先按通用理解回答。如果你希望更准确，我可以先读取并总结最新报告。"
    return "I can answer generally, or I can first read and summarize the latest report for a more accurate discussion."
