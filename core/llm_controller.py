from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import requests

from providers.provider_factory import get_provider


logger = logging.getLogger("trend_agent")
CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
ALLOWED_INTENTS = {"CHAT", "FULL_REPORT_SUMMARY", "RUN_PIPELINE", "CLARIFY"}
ALLOWED_TOOLS = {"get_latest_report", "summarize_report_full", "run_pipeline", "chat_with_context"}
INTENT_ALIASES = {
    "GET_LATEST_REPORT": "FULL_REPORT_SUMMARY",
    "SUMMARIZE_REPORT": "FULL_REPORT_SUMMARY",
    "SUMMARIZE_LATEST_REPORT": "FULL_REPORT_SUMMARY",
    "PIPELINE": "RUN_PIPELINE",
}
TOOL_ALIASES = {
    "GET_LATEST_REPORT": "get_latest_report",
    "SUMMARIZE_REPORT": "summarize_report_full",
    "SUMMARIZE_REPORT_FULL": "summarize_report_full",
    "RUN_PIPELINE": "run_pipeline",
    "CHAT": "chat_with_context",
}
OPENAI_COOLDOWN_UNTIL = 0.0

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


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip()


def detect_language(text: str) -> str:
    cjk_count = len(CJK_RE.findall(text or ""))
    return "zh" if cjk_count >= 20 else "en"


def _chat_language(text: str) -> str:
    forced = _env_str("TREND_LLM_FORCE_LANG").lower()
    if forced in {"zh", "en"}:
        return forced
    return "zh" if CJK_RE.search(text or "") else "en"


def _controller_provider_name() -> str:
    provider = _env_str("TREND_CONTROLLER_PROVIDER", "openai").lower()
    if provider in {"openai", "chatgpt"}:
        return "openai"
    if provider in {"ollama", "local"}:
        return "ollama"
    return "openai"


def _chat_provider_name() -> str:
    provider = _env_str("TREND_CHAT_PROVIDER", "openai").lower()
    if provider in {"openai", "chatgpt"}:
        return "openai"
    if provider in {"ollama", "local"}:
        return "ollama"
    return "openai"


def _local_provider_name() -> str:
    provider = _env_str("TREND_LOCAL_PROVIDER", "ollama").lower()
    if provider in {"ollama", "local"}:
        return "ollama"
    return "ollama"


def _summary_provider_name() -> str:
    provider = _env_str("TREND_SUMMARY_PROVIDER", _chat_provider_name()).lower()
    if provider in {"openai", "chatgpt"}:
        return "openai"
    if provider in {"ollama", "local"}:
        return "ollama"
    return _chat_provider_name()


def _pick_chat_model(text: str) -> str:
    if _chat_language(text) == "zh":
        return str(os.getenv("TREND_LLM_ZH_MODEL", "qwen2") or "qwen2").strip() or "qwen2"
    return str(os.getenv("TREND_LLM_EN_MODEL", "llama3") or "llama3").strip() or "llama3"


def _pick_openai_model(text: str, *, kind: str) -> str:
    lang = _chat_language(text)
    base = _env_str("TREND_OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini"
    if kind == "controller":
        specific = _env_str("TREND_OPENAI_CONTROLLER_MODEL")
    elif kind == "summary":
        specific = _env_str("TREND_OPENAI_SUMMARY_MODEL")
    else:
        specific = _env_str("TREND_OPENAI_CHAT_MODEL")
    zh_specific = _env_str("TREND_OPENAI_ZH_MODEL")
    if lang == "zh" and zh_specific:
        return zh_specific
    return specific or base


def _extract_openai_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _run_openai_chat(*, model: str, messages: list[dict[str, str]], timeout_s: int) -> str:
    global OPENAI_COOLDOWN_UNTIL
    api_key = _env_str("TREND_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("TREND_OPENAI_API_KEY is not set")
    base_url = _env_str("TREND_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    now = time.time()
    if OPENAI_COOLDOWN_UNTIL > now:
        retry_in = max(1, int(OPENAI_COOLDOWN_UNTIL - now))
        raise RuntimeError(f"OpenAI temporarily rate limited; retry in {retry_in}s")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.1,
        },
        timeout=max(5, int(timeout_s) + 5),
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        if response.status_code == 429:
            OPENAI_COOLDOWN_UNTIL = time.time() + _env_int("TREND_OPENAI_RATE_LIMIT_COOLDOWN_S", 45, minimum=5)
        raise
    payload = response.json()
    text = _extract_openai_text(payload)
    if text:
        return text
    raise RuntimeError("OpenAI response did not contain message content")


def _run_controller_llm(prompt: str, *, text: str, timeout_s: int) -> str:
    provider = _controller_provider_name()
    if provider == "openai":
        model = _pick_openai_model(text, kind="controller")
        logger.info("TG controller provider=%s model=%s timeout_s=%s", provider, model, timeout_s)
        return _run_openai_chat(
            model=model,
            messages=[
                {"role": "system", "content": "You are a Telegram agent controller. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            timeout_s=timeout_s,
        )
    model = _pick_chat_model(text)
    logger.info("TG controller provider=%s model=%s timeout_s=%s", provider, model, timeout_s)
    return get_provider("ollama", model).chat(prompt, timeout_s=timeout_s)


def _run_context_chat_llm(prompt: str, *, text: str, timeout_s: int) -> str:
    provider = _chat_provider_name()
    if provider == "openai":
        model = _pick_openai_model(text, kind="chat")
        logger.info("TG chat provider=%s model=%s timeout_s=%s", provider, model, timeout_s)
        return _run_openai_chat(
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise assistant discussing a trend report context."},
                {"role": "user", "content": prompt},
            ],
            timeout_s=timeout_s,
        )
    model = _pick_chat_model(text)
    logger.info("TG chat provider=%s model=%s timeout_s=%s", provider, model, timeout_s)
    return get_provider("ollama", model).chat(prompt, timeout_s=timeout_s)


def _run_summary_llm(prompt: str, *, text: str, timeout_s: int) -> str:
    provider = _summary_provider_name()
    if provider == "openai":
        model = _pick_openai_model(text, kind="summary")
        logger.info("TG summary provider=%s model=%s timeout_s=%s", provider, model, timeout_s)
        return _run_openai_chat(
            model=model,
            messages=[
                {"role": "system", "content": "You summarize report content. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            timeout_s=timeout_s,
        )
    model = _pick_chat_model(text)
    logger.info("TG summary provider=%s model=%s timeout_s=%s", provider, model, timeout_s)
    return get_provider(provider, model).summarize(prompt, timeout_s=timeout_s)


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


def _to_summary_list(value: Any, *, limit: int = 6) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        items = [part.strip("-* \t") for part in value.splitlines() if part.strip()]
    else:
        items = []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _split_report_items(report_text: str) -> list[str]:
    lines = (report_text or "").splitlines()
    items: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("Title:") and current:
            items.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        items.append("\n".join(current).strip())
    items = [item for item in items if item]
    if items:
        return items
    compact = (report_text or "").strip()
    return [compact] if compact else []


def _parse_report_item(item_text: str) -> dict[str, str]:
    fields = {"title": "", "source": "", "link": "", "content": ""}
    content_lines: list[str] = []
    current_key = ""
    for raw_line in (item_text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("Title:"):
            fields["title"] = line.split(":", 1)[1].strip()
            current_key = "title"
            continue
        if line.startswith("Source:"):
            fields["source"] = line.split(":", 1)[1].strip()
            current_key = "source"
            continue
        if line.startswith("Link:"):
            fields["link"] = line.split(":", 1)[1].strip()
            current_key = "link"
            continue
        if line.startswith("Content:"):
            body = line.split(":", 1)[1].strip()
            if body:
                content_lines.append(body)
            current_key = "content"
            continue
        if current_key == "content" and line:
            content_lines.append(line)
    fields["content"] = " ".join(content_lines).strip()
    return fields


def _fallback_report_summary(report_text: str, *, topic_hint: str | None = None) -> dict[str, Any]:
    items = [_parse_report_item(item) for item in _split_report_items(report_text)]
    parsed_items = [item for item in items if any(item.values())]
    titles = [item["title"] for item in parsed_items if item.get("title")]
    lang = detect_language(report_text)
    hint = str(topic_hint or "").strip()

    highlights: list[str] = []
    for item in parsed_items[:5]:
        title = item.get("title", "").strip()
        if not title:
            continue
        source = item.get("source", "").strip()
        link = item.get("link", "").strip()
        label = f"{source}: {title}" if source else title
        if link:
            label = f"{label} ({link})"
        highlights.append(label)

    if titles:
        lead = "; ".join(titles[:4])
        if lang == "zh":
            executive = f"本次报告的重点包括：{lead}。"
        else:
            executive = f"Key items in this report: {lead}."
    else:
        compact = re.sub(r"\s+", " ", (report_text or "")).strip()
        executive = compact[:400] or ("未找到可总结的报告内容。" if lang == "zh" else "No report content was available to summarize.")
    if hint:
        if lang == "zh":
            executive = f"{executive} 关注点：{hint}。"
        else:
            executive = f"{executive} Focus: {hint}."

    return {
        "executive_summary": executive,
        "trends": titles[:5],
        "highlights": highlights[:5] or titles[:5],
        "questions": [],
    }


def summarize_report_text(report_text: str, *, topic_hint: str | None = None) -> dict[str, Any]:
    content = (report_text or "").strip()
    if not content:
        return {
            "executive_summary": "No report content was found.",
            "trends": [],
            "highlights": [],
            "questions": [],
        }

    lang = detect_language(content)
    timeout_s = _env_int("TREND_LLM_SUMMARY_TIMEOUT_S", _env_int("TREND_LLM_CHAT_TIMEOUT_S", 120, minimum=3), minimum=10)
    max_chars = _env_int("TREND_CLOUD_SUMMARY_MAX_CHARS", 18000, minimum=2000)
    prompt = (
        "Summarize the following report content.\n"
        "Return ONLY valid JSON with this schema:\n"
        '{"executive_summary":"...","trends":["..."],"highlights":["..."],"questions":["..."]}\n'
        "Rules:\n"
        "- Be factual and concise.\n"
        "- executive_summary should be 3-6 sentences.\n"
        "- trends/highlights/questions max 5 items each.\n"
        "- Do not paste raw report blocks.\n"
        "- Do not invent facts that are not in the report.\n"
        "- If the report has multiple items, mention the main cross-cutting themes, not just the first link.\n"
        f"- Reply in {'Chinese' if lang == 'zh' else 'English'}.\n"
        f"Topic hint: {str(topic_hint or 'none').strip()}\n\n"
        f"Report content:\n{content[:max_chars]}"
    )
    fallback = _fallback_report_summary(content, topic_hint=topic_hint)
    try:
        raw = _run_summary_llm(prompt, text=content, timeout_s=timeout_s)
        payload = _extract_json_object(raw) or {}
        executive = str(payload.get("executive_summary") or "").strip()
        summary = {
            "executive_summary": executive or fallback["executive_summary"],
            "trends": _to_summary_list(payload.get("trends"), limit=5) or fallback["trends"],
            "highlights": _to_summary_list(payload.get("highlights"), limit=5) or fallback["highlights"],
            "questions": _to_summary_list(payload.get("questions"), limit=5),
        }
        if not summary["questions"]:
            summary["questions"] = fallback["questions"]
        return summary
    except Exception as exc:
        logger.warning("TG summary fallback error=%s", exc)
        return fallback


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
    intent = INTENT_ALIASES.get(intent, intent)
    if intent in ALLOWED_INTENTS:
        normalized["intent"] = intent

    actions_raw = plan.get("actions")
    actions: list[dict[str, Any]] = []
    if isinstance(actions_raw, list):
        for item in actions_raw:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            tool = TOOL_ALIASES.get(tool.upper(), tool)
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


def _request_hints(text: str) -> dict[str, bool]:
    lowered = (text or "").strip().lower()
    return {
        "run": any(
            token in lowered
            for token in ("run", "send report", "send the report", "pipeline", "generate", "refresh rss", "to my email", "email me")
        ),
        "summary": any(
            token in lowered
            for token in ("summary", "summarize", "summarise", "overview", "read the latest report", "key points", "what does it say", "总结", "通读")
        ),
        "report": any(token in lowered for token in ("report", "latest news", "latest report", "报告")),
        "status_like": lowered in {"status", "health", "alive"} or any(token in lowered for token in ("system status", "bot status")),
        "help_like": lowered in {"help", "commands"} or any(token in lowered for token in ("what can you do", "how do i use this", "how to use this")),
    }


def _coerce_plan_from_text(text: str, plan: dict[str, Any], *, chat_id: int, default_lang: str, has_context: bool) -> dict[str, Any]:
    normalized = dict(plan)
    hints = _request_hints(text)
    intent = str(normalized.get("intent") or "CHAT").strip().upper()

    if (hints["status_like"] or hints["help_like"]) and not hints["run"] and not hints["summary"] and not hints["report"]:
        normalized.update(
            {
                "intent": "CHAT",
                "actions": [{"tool": "chat_with_context", "args": {"mode": "followup"}}],
                "needs_confirmation": False,
                "confirmation_prompt": None,
                "store_context": has_context,
                "context_id": str(chat_id),
            }
        )
        return normalized

    if hints["run"] and not hints["summary"]:
        normalized.update(
            {
                "intent": "RUN_PIPELINE",
                "actions": [{"tool": "run_pipeline", "args": {}}],
                "needs_confirmation": True,
                "confirmation_prompt": RUN_CONFIRM_ZH if default_lang == "zh" else RUN_CONFIRM_EN,
                "store_context": False,
                "context_id": str(chat_id),
            }
        )
        return normalized

    if hints["summary"] or (hints["report"] and intent != "RUN_PIPELINE" and not hints["run"]):
        normalized.update(
            {
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
        )
        return normalized

    if intent == "CHAT":
        normalized["actions"] = [{"tool": "chat_with_context", "args": {"mode": "followup"}}]
        normalized["store_context"] = has_context
    return normalized


def _informational_chat_reply(text: str, *, lang: str) -> str | None:
    hints = _request_hints(text)
    if hints["status_like"]:
        if lang == "zh":
            return "???????????? /status???????????????????????"
        return "I am available. Use /status for the exact local status. You can also ask me to summarize the latest report or start a new report run."
    if hints["help_like"]:
        if lang == "zh":
            return "?????????????????????????????????? /status?/help?/report?"
        return "I can summarize the latest report, explain key points, or just chat normally. Exact local commands are /status, /help, and /report."
    return None


def _chat_timeout_fallback(*, lang: str) -> str:
    if lang == "zh":
        return "?????????????????????????????????"
    return "The local model timed out this time. You can try again, or ask for a shorter reply."


def _reply_from_plan(plan: dict[str, Any], *, lang: str) -> str:
    intent = str(plan.get("intent") or "CHAT").strip().upper()
    if intent == "RUN_PIPELINE":
        return str(plan.get("confirmation_prompt") or (RUN_CONFIRM_ZH if lang == "zh" else RUN_CONFIRM_EN)).strip()
    if intent == "FULL_REPORT_SUMMARY":
        return "????????????????" if lang == "zh" else "I will read the latest report and send a full summary."
    if intent == "CLARIFY":
        return str(plan.get("confirmation_prompt") or (AMBIGUOUS_CONFIRM_ZH if lang == "zh" else AMBIGUOUS_CONFIRM_EN)).strip()
    return "??????" if lang == "zh" else "What would you like to talk about?"

def _fallback_controller(text: str, *, chat_id: int, meta: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    lang = detect_language(text)
    has_context = bool((meta or {}).get("context"))
    hints = _request_hints(text)
    if hints["run"] and hints["summary"]:
        plan = {
            "intent": "CLARIFY",
            "actions": [],
            "needs_confirmation": True,
            "confirmation_prompt": AMBIGUOUS_CONFIRM_ZH if lang == "zh" else AMBIGUOUS_CONFIRM_EN,
            "store_context": False,
            "context_id": str(chat_id),
        }
        return str(plan["confirmation_prompt"]), plan

    base_plan = _normalize_plan(
        {
            "intent": "CHAT",
            "actions": [{"tool": "chat_with_context", "args": {"mode": "followup"}}],
            "needs_confirmation": False,
            "confirmation_prompt": None,
            "store_context": has_context,
            "context_id": str(chat_id),
        },
        chat_id=chat_id,
        default_lang=lang,
    )
    plan = _coerce_plan_from_text(text, base_plan, chat_id=chat_id, default_lang=lang, has_context=has_context)
    info_reply = _informational_chat_reply(text, lang=lang)
    if plan["intent"] == "CHAT" and info_reply:
        return info_reply, plan
    return _reply_from_plan(plan, lang=lang), plan

def decide_and_respond(user_text: str, chat_id: int, meta: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    text = (user_text or "").strip()
    if not text:
        return "I need a message to continue.", _normalize_plan(None, chat_id=chat_id, default_lang="en")

    meta = meta or {}
    lang = detect_language(text)
    timeout_s = _env_int("TREND_LLM_CHAT_TIMEOUT_S", 120, minimum=3)

    context = meta.get("context") if isinstance(meta.get("context"), dict) else {}
    context_digest = json.dumps(context, ensure_ascii=False)[:1200] if context else "{}"
    pending_plan = meta.get("pending_plan") if isinstance(meta.get("pending_plan"), dict) else {}
    pending_digest = json.dumps(pending_plan, ensure_ascii=False)[:600] if pending_plan else "{}"
    prompt = (
        "You are a Telegram agent controller.\n"
        "Return ONLY valid JSON. No markdown. No prose outside JSON.\n"
        "You are selecting an executable control plan, not chatting casually.\n"
        "JSON schema:\n"
        "{"
        '"reply":"<short operational response>",'
        '"plan":{'
        '"intent":"CHAT|FULL_REPORT_SUMMARY|RUN_PIPELINE|CLARIFY|GET_LATEST_REPORT|SUMMARIZE_REPORT",'
        '"actions":[{"tool":"get_latest_report|summarize_report_full|run_pipeline|chat_with_context","args":{}}],'
        '"needs_confirmation":true|false,'
        '"confirmation_prompt":"<string or null>",'
        '"store_context":true|false,'
        f'"context_id":"{chat_id}"'
        "}"
        "}\n"
        "Rules:\n"
        "- If user requests sending, generating, rerunning, refreshing, or emailing a report, use RUN_PIPELINE with needs_confirmation=true.\n"
        "- If user asks to read, summarize, explain, or give key points from the latest report/news, use FULL_REPORT_SUMMARY.\n"
        "- For follow-up discussion with existing context, use CHAT plus chat_with_context.\n"
        "- If user asks for status/help/capabilities without requesting an action, use CHAT.\n"
        "- If ambiguous between summarize-latest and regenerate, use CLARIFY.\n"
        "- reply must match the plan and stay operational, not chatty.\n"
        "- Do not say you already completed work unless the plan would execute it now.\n"
        "- Prefer concrete action intents over generic CHAT when the user asks for a report or pipeline action.\n"
        "Examples:\n"
        '- User: "please send the report" -> intent RUN_PIPELINE, needs_confirmation true.\n'
        '- User: "summarize the latest report" -> intent FULL_REPORT_SUMMARY.\n'
        '- User: "what can you do" -> intent CHAT.\n\n'
        '- User: "status" -> intent CHAT.\n'
        '- User: "what can you do" -> intent CHAT.\n'
        '- If a pending RUN_PIPELINE exists and the user asks an informational question, keep the turn as CHAT rather than cancelling the pending action.\n\n'
        f"User text:\n{text}\n\n"
        f"Pending plan:\n{pending_digest}\n\n"
        f"Existing context:\n{context_digest}\n"
    )

    try:
        raw = _run_controller_llm(prompt, text=text, timeout_s=timeout_s)
        payload = _extract_json_object(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("controller output was not valid JSON")
        plan = _normalize_plan(
            payload.get("plan") if isinstance(payload.get("plan"), dict) else None,
            chat_id=chat_id,
            default_lang=lang,
        )
        plan = _coerce_plan_from_text(text, plan, chat_id=chat_id, default_lang=lang, has_context=bool(context))
        reply = str(payload.get("reply") or "").strip()
        if plan["intent"] == "RUN_PIPELINE" and lang == "zh":
            plan["confirmation_prompt"] = RUN_CONFIRM_ZH
        if plan["intent"] == "CLARIFY" and lang == "zh":
            plan["confirmation_prompt"] = AMBIGUOUS_CONFIRM_ZH
        structured_reply = _reply_from_plan(plan, lang=lang)
        if plan["intent"] != "CHAT":
            reply = structured_reply
        elif not reply:
            reply = structured_reply
        info_reply = _informational_chat_reply(text, lang=lang)
        if plan["intent"] == "CHAT" and info_reply:
            reply = info_reply
        return reply, plan
    except Exception as exc:
        logger.warning("TG controller fallback chat_id=%s error=%s", chat_id, exc)
        return _fallback_controller(text, chat_id=chat_id, meta=meta)

def chat_with_context(user_text: str, context: dict[str, Any] | None = None, meta: dict[str, Any] | None = None) -> str:
    text = (user_text or "").strip()
    if not text:
        return "Please send a message."
    lang = _chat_language(text)
    timeout_s = _env_int("TREND_LLM_CHAT_TIMEOUT_S", 120, minimum=3)
    context = context if isinstance(context, dict) else {}
    context_snippet = json.dumps(context, ensure_ascii=False)[:1600] if context else "{}"
    provider = _chat_provider_name()
    model = _pick_openai_model(text, kind="chat") if provider == "openai" else _pick_chat_model(text)
    logger.info("TG chat provider=%s model=%s timeout_s=%s", provider, model, timeout_s)
    prompt = (
        "You are the TrendAgent chat interface.\n"
        "Reply in the same language as the user.\n"
        "Be concise, natural, and conversational.\n"
        "Do not use canned closings or awkward control phrases.\n"
        "Do not invent missing report details.\n"
        "If the user refers to the latest report but no report context is available, say that clearly and naturally.\n"
        "If the user is just chatting, answer directly.\n\n"
        f"Context:\n{context_snippet}\n\n"
        f"User:\n{text}\n"
    )
    try:
        reply = _run_context_chat_llm(prompt, text=text, timeout_s=timeout_s).strip()
        if reply:
            return reply
    except Exception as exc:
        logger.warning("TG context chat fallback error=%s", exc)
        return _chat_timeout_fallback(lang=lang)

    return _chat_timeout_fallback(lang=lang)
