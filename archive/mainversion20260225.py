from pathlib import Path
import json
from datetime import datetime
import traceback
import re
import html as html_lib
import logging
import webbrowser
import smtplib
import ssl
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler
from email.message import EmailMessage
import feedparser


BASE_DIR = Path(__file__).resolve().parent

JSON_DIR      = BASE_DIR / "Json"
STATUS_PATH  = JSON_DIR / "status.json"
HISTORY_PATH = JSON_DIR / "history.json"
LOG_DIR      = BASE_DIR / "logs"              
REPORT_DIR   = BASE_DIR / "report"
HTML_REPORT_DIR = BASE_DIR / "report_html"
JSON_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)
HTML_REPORT_DIR.mkdir(exist_ok=True)

CONFIG_PATH = JSON_DIR / "config.json"
FEED_TITLE_OVERRIDES: dict[str, str] = {}


# -------- Logging --------
def setup_logger() -> logging.Logger:
    logger = logging.getLogger("trend_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # avoid duplicate logs in some environments

    # If handlers already exist (rare, but can happen), don't add again
    if logger.handlers:
        return logger

    log_file = LOG_DIR / "trend_agent.log"

    handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,   # 1MB per file
        backupCount=5,        # keep last 5
        encoding="utf-8"
    )
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Optional: also log to console when you run manually
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    return logger


logger = setup_logger()


def read_secret_from_json_file(path_value: str | None, key: str | None) -> str | None:
    if not path_value or not key:
        return None
    p = Path(path_value)
    if not p.is_absolute():
        p = BASE_DIR / p
    data = json.loads(p.read_text(encoding="utf-8"))
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return value.strip()


def collect_rss_groups(config: dict) -> list[tuple[str, list[str]]]:
    """Return grouped RSS URLs from categorized rss_sources config."""
    rss_sources = config.get("rss_sources")
    if isinstance(rss_sources, dict) and rss_sources:
        groups: list[tuple[str, list[str]]] = []
        seen_global: set[str] = set()
        for category, group_urls in rss_sources.items():
            if isinstance(group_urls, str):
                group_urls = [group_urls]
            if not isinstance(group_urls, list):
                continue

            cleaned: list[str] = []
            for url in group_urls:
                if not isinstance(url, str):
                    continue
                u = url.strip()
                if not u or u in seen_global:
                    continue
                seen_global.add(u)
                cleaned.append(u)

            if cleaned:
                groups.append((str(category), cleaned))

        if groups:
            return groups

    raise ValueError("Config must contain rss_sources as a non-empty object")


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
    password = email_cfg.get("password")
    if not password:
        password = read_secret_from_json_file(
            email_cfg.get("password_secret_file"),
            email_cfg.get("password_secret_key"),
        )
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

    webhook_url = discord_cfg.get("webhook_url")
    if not webhook_url:
        webhook_url = read_secret_from_json_file(
            discord_cfg.get("webhook_secret_file"),
            discord_cfg.get("webhook_secret_key"),
        )
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

                m = re.match(r"^- \[(.*?)\]\((.*?)\)$", line)
                if m:
                    current_feed["items"].append({
                        "title": m.group(1).strip(),
                        "url": m.group(2).strip(),
                    })
                else:
                    current_feed["items"].append({
                        "title": line[2:].strip(),
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

    def _build_report_embeds(report_md: str) -> list[dict]:
        _report_title, categories = _parse_report_markdown(report_md)
        embeds: list[dict] = []
        default_color = int(discord_cfg.get("embed_color", 0x2B6CB0))

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

            for start in range(0, len(fields), 25):
                chunk_fields = fields[start:start + 25]
                part_no = start // 25 + 1
                part_total = (len(fields) + 24) // 25
                title = str(category.get("name", "Category")) or "Category"
                if part_total > 1:
                    title = f"{title} (part {part_no}/{part_total})"
                category_color = category_colors.get(title.split(" (part ", 1)[0].strip().lower(), default_color)

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
        sent_embed_messages = 0
        for start in range(0, len(embeds), embeds_per_message):
            if sent_embed_messages >= max_embed_messages:
                _post_webhook({
                    **base_payload,
                    "content": "TrendAgent report embeds truncated. Increase discord.max_embed_messages to see more.",
                })
                break
            chunk = embeds[start:start + embeds_per_message]
            _post_webhook({**base_payload, "embeds": chunk})
            sent_embed_messages += 1

    logger.info("Discord webhook message sent successfully for run %s", run_id)
    return True


def fetch_rss(url: str, max_per_feed: int, heading_level: int = 2) -> tuple[str, int]:
    logger.info(f"Fetching RSS: {url}")

    feed = feedparser.parse(url)

    # feedparser sets bozo=1 if parse error happened
    if getattr(feed, "bozo", 0) == 1:
        exc = getattr(feed, "bozo_exception", None)
        logger.warning(f"Feed parse warning (bozo=1) for {url}: {exc}")

    feed_title = ""
    try:
        # feed.feed is a dict-like object
        feed_title = feed.feed.get("title", "Untitled feed")
    except Exception as e:
        logger.warning(f"Could not read feed title for {url}: {e}")
        feed_title = "Untitled feed"

    override_title = FEED_TITLE_OVERRIDES.get(url.strip())
    if override_title:
        feed_title = override_title

    md = ""
    heading_level = min(max(1, heading_level), 6)
    md += f"{'#' * heading_level} {feed_title}\n\n"

    entries = getattr(feed, "entries", [])
    if not entries:
        logger.info(f"No entries found for: {feed_title}")
        return "", 0

    item_count = 0
    for entry in entries[:max_per_feed]:
        title = getattr(entry, "title", "(no title)")
        link = getattr(entry, "link", "")

        # Markdown link format: - [title](link)
        if link:
            md += f"- [{title}]({link})\n"
        else:
            md += f"- {title}\n"
        item_count += 1

    md += "\n"
    return md, item_count

def md_to_simple_html(md_text: str, title: str = "Trend Agent Report") -> str:
    """Minimal markdown -> HTML with collapsible category/feed sections."""
    lines = md_text.splitlines()
    out = []
    out.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    out.append(f"<title>{html_lib.escape(title)}</title>")
    out.append(
        "<style>"
        ":root{--bg:#f6f7fb;--card:#ffffff;--text:#1f2937;--muted:#6b7280;--line:#e5e7eb;--link:#0f62fe}"
        "*{box-sizing:border-box}"
        "body{margin:0;background:linear-gradient(180deg,#f9fafb 0%,#eef2ff 100%);color:var(--text);"
        "font-family:'Segoe UI',Tahoma,Arial,sans-serif;line-height:1.6}"
        ".wrap{max-width:980px;margin:0 auto;padding:24px 16px 40px}"
        ".card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px;"
        "box-shadow:0 10px 30px rgba(17,24,39,.06)}"
        ".meta{color:var(--muted);font-size:13px;margin:0 0 14px}"
        "h1{margin:0 0 10px;font-size:28px;line-height:1.2}"
        ".ta-category{margin:18px 0 12px;border:1px solid var(--line);border-radius:12px;background:#fff;overflow:hidden}"
        ".ta-category[open]{box-shadow:0 8px 24px rgba(17,24,39,.05)}"
        ".ta-category-title{cursor:pointer;list-style:none;margin:0;padding:12px 14px;font-size:13px;"
        "letter-spacing:.14em;text-transform:uppercase;font-weight:800;color:#9a3412;"
        "background:linear-gradient(90deg,#fff7ed,#ffedd5);border-bottom:1px solid #fdba74}"
        ".ta-category-title::-webkit-details-marker{display:none}"
        ".ta-category-title::before{content:'v ';color:#c2410c}"
        ".ta-category:not([open]) .ta-category-title::before{content:'> '}"
        ".ta-category-body{padding:10px 12px 12px}"
        ".ta-feed{margin:8px 0;border:1px solid var(--line);border-radius:10px;background:#fafafa}"
        ".ta-feed-title{cursor:pointer;list-style:none;margin:0;padding:10px 12px;font-size:17px;font-weight:700;color:#1f2937}"
        ".ta-feed-title::-webkit-details-marker{display:none}"
        ".ta-feed-title::before{content:'> ';color:#64748b}"
        ".ta-feed[open] .ta-feed-title::before{content:'v '}"
        ".ta-feed ul{margin:0 0 12px;padding:0 16px 0 32px}"
        "p{margin:10px 0}"
        "ul{margin:8px 0 14px;padding-left:20px}"
        "li{margin:6px 0}"
        "a{color:var(--link);text-decoration:none;word-break:break-word}"
        "a:hover{text-decoration:underline}"
        "@media (max-width:640px){.card{padding:16px}h1{font-size:24px}.ta-category-title{font-size:12px;letter-spacing:.12em}.ta-feed-title{font-size:16px}}"
        "</style>"
    )
    out.append("</head><body>")
    out.append("<div class='wrap'><main class='card'>")
    out.append(
        f"<p class='meta'>Generated by TrendAgent | {html_lib.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</p>"
    )

    in_ul = False
    in_feed = False
    in_category = False
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    def close_feed():
        nonlocal in_feed
        close_ul()
        if in_feed:
            out.append("</details>")
            in_feed = False

    def close_category():
        nonlocal in_category
        close_feed()
        if in_category:
            out.append("</div></details>")
            in_category = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if line.startswith("# "):
            close_category()
            out.append(f"<h1>{html_lib.escape(line[2:].strip())}</h1>")
            continue

        if line.startswith("## "):
            close_category()
            cat = html_lib.escape(line[3:].strip())
            out.append("<details class='ta-category' open>")
            out.append(f"<summary class='ta-category-title'>{cat}</summary>")
            out.append("<div class='ta-category-body'>")
            in_category = True
            continue

        if line.startswith("### "):
            if not in_category:
                out.append("<details class='ta-category' open>")
                out.append("<summary class='ta-category-title'>General</summary>")
                out.append("<div class='ta-category-body'>")
                in_category = True
            close_feed()
            feed = html_lib.escape(line[4:].strip())
            out.append("<details class='ta-feed'>")
            out.append(f"<summary class='ta-feed-title'>{feed}</summary>")
            in_feed = True
            continue

        if line.startswith("- "):
            if not in_feed and in_category:
                out.append("<details class='ta-feed'>")
                out.append("<summary class='ta-feed-title'>Items</summary>")
                in_feed = True
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            item = line[2:].strip()

            def repl(m):
                txt = html_lib.escape(m.group(1))
                url = html_lib.escape(m.group(2))
                return f"<a href='{url}' target='_blank' rel='noopener noreferrer'>{txt}</a>"

            item_html = link_re.sub(repl, item)
            out.append(f"<li>{item_html}</li>")
            continue

        if stripped == "":
            close_ul()
            continue

        close_ul()
        out.append(f"<p>{html_lib.escape(line)}</p>")

    close_category()
    out.append("</main></div></body></html>")
    return "\n".join(out)


def main() -> int:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_dt = datetime.now()

    status = {
    "agent": {
        "name": "TrendAgent",
        "version": "0.2",
        "host": "windows-task-scheduler",
       
    },
    "run": {
        "id": run_id,
        "state": "RUNNING",
        "started_at": start_dt.isoformat(timespec="seconds"),
        "finished_at": None,
        "duration_seconds": None,
        "error": None,
    },
    "inputs": {
        "config_path": str(CONFIG_PATH),
        "max_per_feed": None,
        "rss_url_count": None,
    },
    "outputs": {
        "md_path": None,
        "html_path": None,
        "status_path": str(STATUS_PATH),
        "history_path": str(HISTORY_PATH),
        "json_dir": str(JSON_DIR),
        "log_dir": str(LOG_DIR),
        "report_dir": str(REPORT_DIR),
        "html_report_dir": str(HTML_REPORT_DIR),
    },
    "metrics": {
        "items_total": 0,
        "feeds_ok": 0,
        "feeds_failed": 0,
    },
    "events": []   # 你以后可以把关键步骤写进这里（可选）
    }

    write_status(status)
    

    logger.info("========== Trend Agent RUN START ==========")
    logger.info(f"Run ID: {run_id}")
    logger.info(f"Working directory: {BASE_DIR}")

    try:
        # Load config
        if not CONFIG_PATH.exists():
            raise FileNotFoundError(f"config.json not found at: {CONFIG_PATH}")

        global FEED_TITLE_OVERRIDES
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw_overrides = config.get("feed_title_overrides", {})
        if isinstance(raw_overrides, dict):
            FEED_TITLE_OVERRIDES = {
                str(k).strip(): str(v).strip()
                for k, v in raw_overrides.items()
                if str(k).strip() and str(v).strip()
            }
        else:
            FEED_TITLE_OVERRIDES = {}
        rss_groups = collect_rss_groups(config)
        rss_urls = [url for _, urls in rss_groups for url in urls]
        max_per_feed = config.get("max_per_feed", 5)

        logger.info(f"Loaded {len(rss_urls)} RSS URLs, max_per_feed={max_per_feed}")
        status["inputs"]["max_per_feed"] = max_per_feed
        status["inputs"]["rss_url_count"] = len(rss_urls)

        report = "# Trend Agent Report\n\n"
        for category_name, category_urls in rss_groups:
            category_block = ""
            category_has_visible_content = False

            pretty_category = category_name.replace("_", " ").strip().title()
            category_header = f"## {pretty_category}\n\n"
            feed_heading_level = 3
            error_heading = "###"

            for url in category_urls:
                try:
                    feed_md, item_count = fetch_rss(url, max_per_feed, heading_level=feed_heading_level)
                    if feed_md:
                        category_block += feed_md
                        category_has_visible_content = True
                    status["metrics"]["feeds_ok"] += 1
                    status["metrics"]["items_total"] += item_count
                except Exception as feed_err:
                    status["metrics"]["feeds_failed"] += 1
                    logger.exception(f"Failed to fetch RSS: {url}")
                    status["events"].append({
                        "type": "feed_error",
                        "url": url,
                        "error": f"{type(feed_err).__name__}: {feed_err}",
                    })
                    category_block += f"{error_heading} Feed Error\n\n- Failed to fetch: {url}\n\n"
                    category_has_visible_content = True

            if category_has_visible_content:
                report += category_header + category_block

        filename = f"trend_report_{run_id}.md"
        output_path = REPORT_DIR / filename
        output_path.write_text(report, encoding="utf-8")
        output_html = HTML_REPORT_DIR / f"trend_report_{run_id}.html"
        html_report = md_to_simple_html(report, title="Trend Agent Report")
        output_html.write_text(html_report, encoding="utf-8")

        logger.info(f"Saved report: {output_path}")
        logger.info(f"Saved HTML: {output_html}")
        status["outputs"]["md_path"] = str(output_path)
        status["outputs"]["html_path"] = str(output_html)

        # Email the HTML report (body + attachment) if configured (non-fatal)
        try:
            email_sent = send_email_report(
                run_id=run_id,
                html_path=output_html,
                html_content=html_report,
                email_cfg=config.get("email"),
            )
            status["outputs"]["email_sent"] = email_sent
        except Exception as email_err:
            status["outputs"]["email_sent"] = False
            logger.exception("Failed to send email report (non-fatal)")
            status["events"].append({
                "type": "email_error",
                "error": f"{type(email_err).__name__}: {email_err}",
            })

        # Send Discord summary via webhook if configured (non-fatal)
        try:
            discord_sent = send_discord_report(
                run_id=run_id,
                html_path=output_html,
                md_text=report,
                status=status,
                discord_cfg=config.get("discord"),
            )
            status["outputs"]["discord_sent"] = discord_sent
        except Exception as discord_err:
            status["outputs"]["discord_sent"] = False
            logger.exception("Failed to send Discord webhook report (non-fatal)")
            status["events"].append({
                "type": "discord_error",
                "error": f"{type(discord_err).__name__}: {discord_err}",
            })

        # Open the generated HTML report in the default browser (non-fatal)
        try:
            webbrowser.open(output_html.resolve().as_uri())
            logger.info(f"Opened HTML report in browser: {output_html}")
        except Exception:
            logger.exception("Failed to open HTML report in browser (non-fatal)")

        # Success status
        end_dt = datetime.now()
        duration = (end_dt - start_dt).total_seconds()

        status["run"]["state"] = "SUCCESS"
        status["run"]["finished_at"] = end_dt.isoformat(timespec="seconds")
        status["run"]["duration_seconds"] = duration
        status["run"]["error"] = None
        write_status(status)
        if status["metrics"]["feeds_failed"] > 0:
            logger.warning(
                "Completed with feed errors: ok=%s failed=%s items=%s",
                status["metrics"]["feeds_ok"],
                status["metrics"]["feeds_failed"],
                status["metrics"]["items_total"],
            )
    # everything OK
        logger.info(f"Duration: {duration:.2f}s")
        logger.info("========== Trend Agent RUN SUCCESS ==========")
        try:
            append_history(status)
        except Exception:
            logger.exception("append_history failed (non-fatal)")
        # Return code 0 means success
        return 0

    except Exception as e:
        end_dt = datetime.now()
        duration = (end_dt - start_dt).total_seconds()
        err_text = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc()

        status["run"]["state"] = "FAILED"
        status["run"]["finished_at"] = end_dt.isoformat(timespec="seconds")
        status["run"]["duration_seconds"] = duration
        status["run"]["error"] = {
            "message": err_text,
            "traceback": tb,
        }
        write_status(status)

        logger.error("========== Trend Agent RUN FAILED ==========")
        logger.error(err_text)
        logger.error(tb)
    return 1
      
def write_json_atomic(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def write_status(status: dict):
    write_json_atomic(STATUS_PATH, status)

def append_history(status: dict, keep_last: int = 200):
    # history.json = [ {run summary}, {run summary}, ... ]
    run_error = status.get("run", {}).get("error")
    if run_error is None:
        run_error = status.get("error")

    item = {
    "run_id": status.get("run", {}).get("id", ""),
    "state": status.get("run", {}).get("state", ""),
    "started_at": status.get("run", {}).get("started_at"),
    "finished_at": status.get("run", {}).get("finished_at"),
    "duration_seconds": status.get("run", {}).get("duration_seconds"),
    "outputs": status.get("outputs", {}),
    "error": run_error,
}

    if HISTORY_PATH.exists():
        try:
            arr = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(arr, list):
                arr = []
        except Exception:
            arr = []
    else:
        arr = []

    arr.append(item)
    arr = arr[-keep_last:]
    write_json_atomic(HISTORY_PATH, arr)
 
    write_status(status)

   # logger.error("========== Trend Agent RUN FAILED ==========")
  
        # Return non-zero means failure (useful for schedulers)
    # return 1


if __name__ == "__main__":
    raise SystemExit(main())

