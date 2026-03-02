from pathlib import Path
import json
from datetime import datetime
import logging
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from secrets_loader import load_secrets


BASE_DIR = Path(__file__).resolve().parent
MD_REPEAT_TOKEN = "[[PREVIOUSLY_SHOWN]]"
MD_FRESH_TOKEN = "[[NEW_ITEM]]"
MD_SUBGROUP_PREFIX = "[[SUBGROUP]] "
logger = logging.getLogger("trend_agent")

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

    if "discord.gg/" in webhook_url:
        raise ValueError(
            "Discord invite link provided. You need a channel webhook URL "
            "(https://discord.com/api/webhooks/...)"
        )
    if "/api/webhooks/" not in webhook_url:
        raise ValueError("Discord webhook URL looks invalid")

    def _run_date_display() -> str:
        try:
            dt = datetime.strptime(run_id, "%Y%m%d_%H%M%S")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def _post_webhook(payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "TrendAgent/0.2 (Discord webhook integration)",
                "Accept": "application/json, text/plain, */*",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status_code = getattr(resp, "status", None) or resp.getcode()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code == 403 and "1010" in detail:
                raise RuntimeError(
                    "Discord webhook HTTP 403 (error 1010). This is usually a Discord/Cloudflare access block "
                    "for the request source or client signature. Try rotating the webhook and retrying, or test "
                    "the same webhook with curl/Postman from this machine/network."
                ) from e
            raise RuntimeError(f"Discord webhook HTTP {e.code}: {detail}") from e
        if status_code not in (200, 204):
            raise RuntimeError(f"Unexpected Discord webhook response status: {status_code}")

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
                lines.append(f"• [{title}]({url})")
            else:
                lines.append(f"• {title}")
        text = "\n".join(lines).strip() or "No items"
        return _truncate(text, 1024, "\n...(truncated)")

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

    def _build_report_embeds(report_md: str) -> list[dict]:
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

    summary_lines = [f"**TrendAgent Report**  •  {_run_date_display()}"]

    base_payload = {"username": discord_cfg.get("username", "TrendAgent")}
    avatar_url = discord_cfg.get("avatar_url")
    if avatar_url:
        base_payload["avatar_url"] = avatar_url

    logger.info("Sending Discord webhook message for run %s", run_id)
    _post_webhook({**base_payload, "content": "\n".join(summary_lines)})

    if md_text and discord_cfg.get("send_report_embeds", True):
        embeds = _build_report_embeds(md_text)
        max_embed_messages = max(1, int(discord_cfg.get("max_embed_messages", 3)))
        embeds_per_message = min(10, max(1, int(discord_cfg.get("max_embeds_per_message", 5))))
        max_embed_chars_per_message = 5800
        sent_embed_messages = 0
        start = 0
        while start < len(embeds):
            if sent_embed_messages >= max_embed_messages:
                _post_webhook({
                    **base_payload,
                    "content": "TrendAgent report embeds truncated. Increase discord.max_embed_messages to see more.",
                })
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

            _post_webhook({**base_payload, "embeds": chunk})
            sent_embed_messages += 1

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
