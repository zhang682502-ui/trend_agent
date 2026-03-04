import json
import hashlib
import os
from pathlib import Path
from datetime import datetime
import logging
import re
import smtplib
import ssl
from urllib.parse import urlparse
from email.message import EmailMessage
import requests
from config.secrets_loader import load_secrets


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
JSON_DIR = PROJECT_DIR / "Json"
MD_REPEAT_TOKEN = "[[PREVIOUSLY_SHOWN]]"
MD_FRESH_TOKEN = "[[NEW_ITEM]]"
MD_SUBGROUP_PREFIX = "[[SUBGROUP]] "
logger = logging.getLogger("trend_agent")


def _run_updated_display() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _run_date_display(run_id: str) -> str:
    try:
        dt = datetime.strptime(run_id, "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _normalize_webhook_url(webhook_url: str) -> str:
    return webhook_url.split("?", 1)[0].rstrip("/")


def _mask_webhook_url(webhook_url: str) -> str:
    base_webhook_url = _normalize_webhook_url(webhook_url)
    try:
        webhook_id, webhook_token = _parse_webhook_parts(base_webhook_url)
    except ValueError:
        return base_webhook_url
    masked_token = webhook_token[:8]
    return f"https://discord.com/api/webhooks/{webhook_id}/{masked_token}..."


def _discord_state_path(webhook_url: str) -> Path:
    normalized_webhook_url = _normalize_webhook_url(webhook_url)
    webhook_hash = hashlib.sha256(normalized_webhook_url.encode("utf-8")).hexdigest()[:12]
    return JSON_DIR / f"discord_single_message_{webhook_hash}.json"


def _load_single_message_id(webhook_url: str) -> str | None:
    state_path = _discord_state_path(webhook_url)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read Discord single-message state %s: %s", state_path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Ignoring malformed Discord single-message state %s", state_path)
        return None
    message_id = str(data.get("message_id") or "").strip()
    return message_id or None


def _save_single_message_id(webhook_url: str, message_id: str) -> None:
    state_path = _discord_state_path(webhook_url)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_name(f"{state_path.name}.tmp")
        tmp_path.write_text(
            json.dumps({"message_id": str(message_id)}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, state_path)
        logger.info("Saved Discord single-message state to %s with message_id=%s", state_path, message_id)
    except Exception:
        logger.exception("Failed to persist Discord single-message state to %s", state_path)
        raise


def _parse_webhook_parts(webhook_url: str) -> tuple[str, str]:
    parsed = urlparse(_normalize_webhook_url(webhook_url))
    path_parts = [part for part in parsed.path.split("/") if part]
    try:
        webhooks_index = path_parts.index("webhooks")
    except ValueError as exc:
        raise ValueError("Discord webhook URL looks invalid") from exc
    if len(path_parts) <= webhooks_index + 2:
        raise ValueError("Discord webhook URL looks invalid")
    webhook_id = path_parts[webhooks_index + 1].strip()
    webhook_token = path_parts[webhooks_index + 2].strip()
    if not webhook_id or not webhook_token:
        raise ValueError("Discord webhook URL looks invalid")
    return webhook_id, webhook_token


def _discord_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": "TrendAgent/0.2 (Discord webhook integration)",
        "Accept": "application/json, text/plain, */*",
    }


def _raise_discord_http_error(response: requests.Response, *, allow_404: bool = False) -> None:
    if allow_404 and response.status_code == 404:
        return
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text
        if response.status_code == 403 and "1010" in detail:
            raise RuntimeError(
                "Discord webhook HTTP 403 (error 1010). This is usually a Discord/Cloudflare access block "
                "for the request source or client signature. Try rotating the webhook and retrying, or test "
                "the same webhook with curl/Postman from this machine/network."
            ) from exc
        raise RuntimeError(f"Discord webhook HTTP {response.status_code}: {detail}") from exc


def _post_webhook(webhook_url: str, payload: dict) -> None:
    response = requests.post(
        _normalize_webhook_url(webhook_url),
        json=payload,
        headers=_discord_headers(),
        timeout=30,
    )
    _raise_discord_http_error(response)


def _post_webhook_wait(webhook_url: str, payload: dict) -> str:
    base_webhook_url = _normalize_webhook_url(webhook_url)
    post_url = f"{base_webhook_url}?wait=true"
    logger.info("Discord single-message POST wait=true -> %s?wait=true", _mask_webhook_url(base_webhook_url))
    response = requests.post(
        post_url,
        json=payload,
        headers=_discord_headers(),
        timeout=30,
    )
    _raise_discord_http_error(response)
    logger.info("Discord single-message POST status=%s", response.status_code)
    try:
        data = response.json()
    except ValueError as exc:
        detail = response.text
        raise RuntimeError(
            f"Discord webhook did not return JSON for wait=true response (status {response.status_code}): {detail}"
        ) from exc
    message_id = str(data.get("id") or "").strip()
    if not message_id:
        raise RuntimeError("Discord webhook wait=true response missing message id")
    logger.info("Discord single-message POST returned message_id=%s", message_id)
    return message_id


def _patch_webhook_message(webhook_url: str, message_id: str, payload: dict) -> requests.Response:
    webhook_id, webhook_token = _parse_webhook_parts(_normalize_webhook_url(webhook_url))
    patch_url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}"
    logger.info(
        "Discord single-message PATCH -> https://discord.com/api/webhooks/%s/%s.../messages/%s",
        webhook_id,
        webhook_token[:8],
        message_id,
    )
    response = requests.patch(
        patch_url,
        json=payload,
        headers=_discord_headers(),
        timeout=30,
    )
    _raise_discord_http_error(response, allow_404=True)
    logger.info("Discord single-message PATCH status=%s", response.status_code)
    return response


def _collapse_payloads_to_single_message(base_payload: dict, payloads: list[dict], embed_char_count_fn) -> dict:
    single_payload = dict(base_payload)
    content_parts: list[str] = []
    embeds: list[dict] = []
    embed_chars = 0
    embeds_truncated_for_single_message = False

    for payload in payloads:
        content = str(payload.get("content") or "").strip()
        if content:
            content_parts.append(content)

        for embed in payload.get("embeds", []) or []:
            embed_size = embed_char_count_fn(embed)
            if len(embeds) >= 10 or embed_chars + embed_size > 5800:
                embeds_truncated_for_single_message = True
                continue
            embeds.append(embed)
            embed_chars += embed_size

    if embeds_truncated_for_single_message:
        logger.info(
            "Discord single-message payload exceeded one-message embed limits; extra embeds omitted from user-facing content"
        )

    single_payload["content"] = "\n".join(part for part in content_parts if part).strip()
    if embeds:
        single_payload["embeds"] = embeds
    return single_payload


def _embed_char_count(embed: dict) -> int:
    total = 0
    title = embed.get("title")
    if isinstance(title, str):
        total += len(title)
    description = embed.get("description")
    if isinstance(description, str):
        total += len(description)
    footer = embed.get("footer")
    if isinstance(footer, dict):
        footer_text = footer.get("text")
        if isinstance(footer_text, str):
            total += len(footer_text)
    author = embed.get("author")
    if isinstance(author, dict):
        author_name = author.get("name")
        if isinstance(author_name, str):
            total += len(author_name)
    for field in embed.get("fields", []) or []:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        value = field.get("value")
        if isinstance(name, str):
            total += len(name)
        if isinstance(value, str):
            total += len(value)
    return total


def _truncate(text: str, limit: int, suffix: str = "...") -> str:
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: limit - len(suffix)] + suffix


def _feed_items_to_field_value(items: list[dict]) -> str:
    lines: list[str] = []
    for item in items:
        title = _truncate(str(item.get("title", "(no title)")), 220)
        url = str(item.get("url", "")).strip()
        if url:
            lines.append(f"Ã¢â‚¬Â¢ [{title}]({url})")
        else:
            lines.append(f"Ã¢â‚¬Â¢ {title}")
    text = "\n".join(lines).strip() or "No items"
    return _truncate(text, 1024, "\n...")


def _parse_report_markdown(report_md: str) -> tuple[str | None, list[dict]]:
    report_title: str | None = None
    categories: list[dict] = []
    current_category: dict | None = None
    current_feed: dict | None = None

    for raw in report_md.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("# "):
            report_title = line[2:].strip() or report_title
            continue
        if line.startswith("## "):
            current_category = {"name": line[3:].strip() or "Category", "feeds": []}
            categories.append(current_category)
            current_feed = None
            continue
        if line.startswith(MD_SUBGROUP_PREFIX):
            continue
        if line.startswith("### "):
            if current_category is None:
                current_category = {"name": "General", "feeds": []}
                categories.append(current_category)
            current_feed = {"name": line[4:].strip() or "Feed", "items": []}
            current_category["feeds"].append(current_feed)
            continue

        if line.startswith("- "):
            if current_category is None:
                current_category = {"name": "General", "feeds": []}
                categories.append(current_category)
            if current_feed is None:
                current_feed = {"name": "Items", "items": []}
                current_category["feeds"].append(current_feed)

            item_line = line.replace(MD_REPEAT_TOKEN, "").replace(MD_FRESH_TOKEN, "").strip()
            m = re.match(r"^- \[(.*?)\]\((.*?)\)$", item_line)
            if m:
                current_feed["items"].append({
                    "title": m.group(1).strip(),
                    "url": m.group(2).strip(),
                })
            else:
                current_feed["items"].append({
                    "title": item_line[2:].strip() if item_line.startswith("- ") else item_line,
                    "url": "",
                })

    return report_title, categories


def _build_report_embeds(report_md: str, discord_cfg: dict) -> list[dict]:
    _report_title, categories = _parse_report_markdown(report_md)
    embeds: list[dict] = []
    default_color = int(discord_cfg.get("embed_color", 0x2B6CB0))
    max_embed_chars = 5800

    category_colors = {
        "technology": 0x2563EB,
        "politics": 0xDC2626,
        "finance": 0x059669,
        "science": 0x7C3AED,
        "culture": 0xEA580C,
        "aviation": 0x0EA5E9,
    }

    for category in categories:
        feeds = [f for f in category.get("feeds", []) if f.get("items")]
        if not feeds:
            continue

        fields: list[dict] = []
        for feed in feeds:
            fields.append({
                "name": _truncate(str(feed.get("name", "Feed")) or "Feed", 256),
                "value": _feed_items_to_field_value(feed.get("items", [])),
                "inline": False,
            })

        title_base = str(category.get("name", "Category")) or "Category"
        category_color = category_colors.get(title_base.strip().lower(), default_color)
        title_with_part_budget = _truncate(f"{title_base} (part 99/99)", 256)
        base_embed_char_cost = len(title_with_part_budget)

        field_groups: list[list[dict]] = []
        current_group: list[dict] = []
        current_chars = base_embed_char_cost

        for field in fields:
            field_chars = len(str(field.get("name", ""))) + len(str(field.get("value", "")))
            would_exceed_chars = current_group and (current_chars + field_chars > max_embed_chars)
            would_exceed_fields = len(current_group) >= 25
            if would_exceed_chars or would_exceed_fields:
                field_groups.append(current_group)
                current_group = []
                current_chars = base_embed_char_cost
            current_group.append(field)
            current_chars += field_chars

        if current_group:
            field_groups.append(current_group)

        for idx, chunk_fields in enumerate(field_groups, start=1):
            title = title_base
            if len(field_groups) > 1:
                title = f"{title_base} (part {idx}/{len(field_groups)})"
            embed = {
                "title": _truncate(title, 256),
                "color": category_color,
                "fields": chunk_fields,
            }
            embeds.append(embed)

    return embeds


def _build_discord_payloads(
    base_payload: dict,
    summary_lines: list[str],
    md_text: str | None,
    discord_cfg: dict,
) -> list[dict]:
    payloads = [{**base_payload, "content": "\n".join(summary_lines)}]

    if not md_text or not discord_cfg.get("send_report_embeds", True):
        return payloads

    embeds = _build_report_embeds(md_text, discord_cfg)
    max_embed_messages = max(1, int(discord_cfg.get("max_embed_messages", 3)))
    embeds_per_message = min(10, max(1, int(discord_cfg.get("max_embeds_per_message", 5))))
    max_embed_chars_per_message = 5800
    sent_embed_messages = 0
    start = 0

    while start < len(embeds):
        if sent_embed_messages >= max_embed_messages:
            logger.info(
                "Discord embeds truncated after %s message(s); remaining embeds omitted from user-facing content",
                max_embed_messages,
            )
            break

        chunk: list[dict] = []
        chunk_chars = 0
        while start < len(embeds) and len(chunk) < embeds_per_message:
            embed = embeds[start]
            embed_chars = _embed_char_count(embed)
            if chunk and (chunk_chars + embed_chars > max_embed_chars_per_message):
                break
            chunk.append(embed)
            chunk_chars += embed_chars
            start += 1

        if not chunk:
            chunk = [embeds[start]]
            start += 1

        payloads.append({**base_payload, "embeds": chunk})
        sent_embed_messages += 1

    return payloads

def send_email_report(
    *,
    run_id: str,
    html_path: Path,
    html_content: str,
    email_cfg: dict | None,
) -> bool:
    """Send the HTML report as email body + attachment. Returns True if sent."""
    if not email_cfg:
        logger.info("Email sending skipped: no email config found")
        return False

    if not email_cfg.get("enabled", False):
        logger.info("Email sending skipped: email.enabled is false")
        return False

    smtp_host = email_cfg.get("smtp_host")
    smtp_port = int(email_cfg.get("smtp_port", 465))
    username = email_cfg.get("username")
    password = str(load_secrets().get("gmail_app_password") or "").strip()
    to_emails = email_cfg.get("to_emails", [])
    from_email = email_cfg.get("from_email") or username
    use_ssl = bool(email_cfg.get("use_ssl", True))
    use_starttls = bool(email_cfg.get("use_starttls", not use_ssl))

    if isinstance(to_emails, str):
        to_emails = [to_emails]

    missing = [
        name for name, value in [
            ("smtp_host", smtp_host),
            ("username", username),
            ("password", password),
            ("from_email", from_email),
        ]
        if not value
    ]
    if not to_emails:
        missing.append("to_emails")
    if missing:
        raise ValueError(f"Email config missing required fields: {', '.join(missing)}")

    today_str = datetime.now().strftime('%Y-%m-%d')
    subject = email_cfg.get("subject", "TrendAgent Daily Report")
    if "{date}" in subject:
        subject = subject.format(date=today_str)
    elif today_str not in subject:
        subject = f"{subject} - {today_str}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = ", ".join(to_emails)
    msg.set_content(
        "TrendAgent daily report is attached as HTML. "
        "If your email client supports HTML, see the rendered report in the message body."
    )
    msg.add_alternative(html_content, subtype="html")
    msg.add_attachment(
        html_path.read_bytes(),
        maintype="text",
        subtype="html",
        filename=html_path.name,
    )

    logger.info(
        "Sending email report via SMTP to %s recipient(s) using %s:%s",
        len(to_emails),
        smtp_host,
        smtp_port,
    )

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=30) as server:
            server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            if use_starttls:
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
            server.login(username, password)
            server.send_message(msg)

    logger.info("Email report sent successfully for run %s", run_id)
    return True


def send_discord_report(
    *,
    run_id: str,
    html_path: Path,
    md_text: str | None,
    status: dict,
    discord_cfg: dict | None,
) -> bool:
    """Send a run summary to a Discord channel via webhook. Returns True if sent."""
    if not discord_cfg:
        logger.info("Discord sending skipped: no discord config found")
        return False

    if not discord_cfg.get("enabled", False):
        logger.info("Discord sending skipped: discord.enabled is false")
        return False

    webhook_url = str(load_secrets().get("discord_webhook_url") or "").strip()
    if not webhook_url:
        raise ValueError("Discord config missing webhook URL")
    base_webhook_url = _normalize_webhook_url(webhook_url)

    if "discord.gg/" in webhook_url:
        raise ValueError(
            "Discord invite link provided. You need a channel webhook URL "
            "(https://discord.com/api/webhooks/...)"
        )
    if "/api/webhooks/" not in webhook_url:
        raise ValueError("Discord webhook URL looks invalid")
    single_mode = bool(discord_cfg.get("single_message", False))
    state_path = _discord_state_path(base_webhook_url)
    logger.info(
        "Discord delivery starting | webhook=%s | single_mode=%s | state_path=%s",
        _mask_webhook_url(base_webhook_url),
        single_mode,
        state_path,
    )

    summary_lines = [
        "TrendAgent Report",
        f"Updated Â· {_run_updated_display()}",
    ]

    base_payload = {"username": discord_cfg.get("username", "TrendAgent")}
    avatar_url = discord_cfg.get("avatar_url")
    if avatar_url:
        base_payload["avatar_url"] = avatar_url

    logger.info("Sending Discord webhook message for run %s", run_id)
    payloads = _build_discord_payloads(base_payload, summary_lines, md_text, discord_cfg)
    try:
        if single_mode:
            single_payload = _collapse_payloads_to_single_message(base_payload, payloads, _embed_char_count)
            saved_message_id = _load_single_message_id(base_webhook_url)
            logger.info("Discord single-message loaded message_id=%s", saved_message_id or "<none>")
            if saved_message_id:
                logger.info("Discord single-message branch=PATCH-existing")
                patch_response = _patch_webhook_message(base_webhook_url, saved_message_id, single_payload)
                if patch_response.status_code == 404:
                    logger.info("Discord single-message branch=fallback-POST-after-404")
                    new_message_id = _post_webhook_wait(base_webhook_url, single_payload)
                    logger.info("Discord single-message save step reached with new message_id=%s", new_message_id)
                    _save_single_message_id(base_webhook_url, new_message_id)
                else:
                    logger.info("Discord single-message save step reached with existing message_id=%s", saved_message_id)
                    _save_single_message_id(base_webhook_url, saved_message_id)
            else:
                logger.info("Discord single-message branch=POST-first-time")
                new_message_id = _post_webhook_wait(base_webhook_url, single_payload)
                logger.info("Discord single-message save step reached with new message_id=%s", new_message_id)
                _save_single_message_id(base_webhook_url, new_message_id)
        else:
            logger.info("Discord single-message disabled; using existing multi-message POST flow")
            for payload in payloads:
                _post_webhook(base_webhook_url, payload)
    except Exception:
        logger.exception("Discord delivery failed before completion for run %s", run_id)
        raise

    logger.info("Discord webhook message sent successfully for run %s", run_id)
    return True


def deliver_to_all(
    run_id: str,
    html_path: Path,
    html_content: str,
    md_text: str,
    status: dict,
    config: dict,
) -> dict[str, bool]:
    results = {"email_sent": False, "discord_sent": False}

    try:
        results["email_sent"] = send_email_report(
            run_id=run_id,
            html_path=html_path,
            html_content=html_content,
            email_cfg=config.get("email"),
        )
    except Exception as email_err:
        logger.exception("Failed to send email report (non-fatal)")
        status.setdefault("events", []).append({
            "type": "email_error",
            "error": f"{type(email_err).__name__}: {email_err}",
        })

    try:
        results["discord_sent"] = send_discord_report(
            run_id=run_id,
            html_path=html_path,
            md_text=md_text,
            status=status,
            discord_cfg=config.get("discord"),
        )
    except Exception as discord_err:
        logger.exception("Failed to send Discord webhook report (non-fatal)")
        status.setdefault("events", []).append({
            "type": "discord_error",
            "error": f"{type(discord_err).__name__}: {discord_err}",
        })

    return results
