from core.env_bootstrap import refresh_windows_path_from_registry

refresh_windows_path_from_registry()
from pathlib import Path
import argparse
import os
import sys
import threading
import json
import warnings
from datetime import date, datetime, timedelta
import traceback
import re
import string
import html as html_lib
import logging
import time
import webbrowser
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from logging.handlers import RotatingFileHandler
import feedparser

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
warnings.filterwarnings("ignore", category=UserWarning)
from config.config_loader import CONFIG_PATH as RUNTIME_CONFIG_PATH, ConfigError, load_config
from config.secrets_loader import SecretConfigError, load_secrets
from core.delivery import deliver_to_all
from core.health import format_health_text, record_command, record_report_trigger, reset_health_state
from core.runtime_guard import RuntimeAlreadyRunning, acquire_lock
from core.telegram_poll import TelegramConflictError, run_telegram_forever, start_telegram_polling
from core.voice import VoiceTranscriptionError, preload_fast_voice_model, transcribe_telegram_media
from memory.identity import canonicalize_url, make_item_id
from memory.ops_store import load_ops_memory, save_ops_memory_atomic, update_ops_after_run
from memory.prefs import load_prefs
from memory.recall_store import (
    init_db as init_recall_db2,
    has_seen,
    mark_seen,
    record_feed_failure,
    commit as recall_commit,
    close as recall_close,
)


BASE_DIR = Path(__file__).resolve().parent

JSON_DIR      = BASE_DIR / "Json"
DATA_DIR      = BASE_DIR / "data"
MEMORY_DIR = BASE_DIR / "memory"
MEMORY_RUN_DIR = MEMORY_DIR / "run"
MEMORY_RECALL_DIR = MEMORY_DIR / "recall"
MEMORY_KNOWLEDGE_DIR = MEMORY_DIR / "knowledge"
LOCK_DIR = MEMORY_DIR / "locks"
STATUS_PATH  = JSON_DIR / "status.json"
HISTORY_PATH = JSON_DIR / "history.json"
HISTORY_URLS_PATH = JSON_DIR / "history_urls.json"
FEED_FAILOVER_STATE_PATH = JSON_DIR / "feed_failover_state.json"
AGENT_MEMORY_PATH = MEMORY_RUN_DIR / "agent_memory.json"
OPS_MEMORY_PATH = MEMORY_DIR / "ops" / "agent_memory.json"
PREFS_PATH = MEMORY_DIR / "prefs" / "prefs.yaml"
RECALL_DB_PATH = MEMORY_RECALL_DIR / "recall.sqlite"
SEEN_ITEMS_DB_PATH = MEMORY_RECALL_DIR / "seen_items.sqlite"
KNOWLEDGE_PREFS_PATH = MEMORY_KNOWLEDGE_DIR / "prefs.yaml"
KNOWLEDGE_SOURCES_PATH = MEMORY_KNOWLEDGE_DIR / "sources.yaml"
KNOWLEDGE_RULES_PATH = MEMORY_KNOWLEDGE_DIR / "rules.yaml"
RUN_FILE_LOCK_PATH = LOCK_DIR / "run.lock"
LOG_DIR      = BASE_DIR / "logs"              
REPORT_DIR   = BASE_DIR / "report"
HTML_REPORT_DIR = BASE_DIR / "report_html"
JSON_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
MEMORY_DIR.mkdir(exist_ok=True)
MEMORY_RUN_DIR.mkdir(exist_ok=True)
MEMORY_RECALL_DIR.mkdir(exist_ok=True)
MEMORY_KNOWLEDGE_DIR.mkdir(exist_ok=True)
LOCK_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)
HTML_REPORT_DIR.mkdir(exist_ok=True)

CONFIG_PATH = BASE_DIR / "config" / "config.json"
FEED_TITLE_OVERRIDES: dict[str, str] = {}
HISTORY_DEDUPE_LOOKBACK_DAYS = 7
DEFAULT_DISPLAY_COUNT = 3
DEFAULT_FETCH_COUNT = 30
DEFAULT_FAILOVER_SWITCH_AFTER_FAILURES = 2
DEFAULT_MAX_ACTIVE_FEEDS_PER_SUBGROUP = 3
DEFAULT_AGENT_ID = "TrendAgent-Local-01"
MD_FEED_NOTE_PREFIX = "[[FEED_NOTE]] "
MD_REPEAT_TOKEN = "[[PREVIOUSLY_SHOWN]]"
MD_FRESH_TOKEN = "[[NEW_ITEM]]"
MD_SUBGROUP_PREFIX = "[[SUBGROUP]] "
HTTP_FETCH_TIMEOUT_SECONDS = 20
HTTP_RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

TRACKING_QUERY_PARAM_NAMES = {
    "ref",
    "source",
    "src",
    "campaign",
    "cmp",
    "cid",
    "mc_cid",
    "mc_eid",
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "igshid",
    "mkt_tok",
}

FRIENDLY_FEED_HOST_TITLES = {
    "www.whitehouse.gov": "White House",
    "whitehouse.gov": "White House",
    "www.gov.uk": "GOV.UK",
    "gov.uk": "GOV.UK",
    "ec.europa.eu": "EU Commission",
    "www.ec.europa.eu": "EU Commission",
    "boeing.mediaroom.com": "Boeing",
    "www.reutersagency.com": "Reuters",
    "reutersagency.com": "Reuters",
    "www.politico.com": "Politico",
    "politico.com": "Politico",
    "feeds.bbci.co.uk": "BBC Politics",
    "www.bbc.co.uk": "BBC Politics",
    "bbc.co.uk": "BBC Politics",
}


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
RUN_TRIGGER_LOCK = threading.Lock()
TELEGRAM_THREAD = None


def _load_runtime_secrets() -> dict | None:
    try:
        return load_secrets()
    except SecretConfigError as exc:
        message = f"Secret config error: {exc}"
        logger.error(message)
        print(message, file=sys.stderr)
        return None


def _load_runtime_config() -> dict | None:
    try:
        config = load_config()
    except ConfigError as exc:
        message = f"Config error: {exc}"
        logger.error(message)
        print(message, file=sys.stderr)
        return None
    discord_cfg = config.get("discord")
    discord_single_message = None
    if isinstance(discord_cfg, dict):
        discord_single_message = bool(discord_cfg.get("single_message", False))
    logger.info(
        "Loaded config from %s | discord.single_message=%s",
        RUNTIME_CONFIG_PATH.resolve(),
        discord_single_message,
    )
    return config


def _acquire_run_file_lock() -> int:
    try:
        fd = os.open(str(RUN_FILE_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"Another TrendAgent run is active ({RUN_FILE_LOCK_PATH})") from exc
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return fd


def _release_run_file_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except Exception:
        logger.exception("Failed to close run lock file handle")
    try:
        RUN_FILE_LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed to remove run lock file")


def _load_telegram_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _load_telegram_status() -> dict | None:
    if not STATUS_PATH.exists():
        return None
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _telegram_run_info(status: dict) -> dict:
    run = status.get("run")
    return run if isinstance(run, dict) else {}


def _telegram_metrics(status: dict) -> dict:
    metrics = status.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _telegram_error_message(status: dict) -> str | None:
    run_error = _telegram_run_info(status).get("error")
    if isinstance(run_error, dict):
        message = str(run_error.get("message") or "").strip()
        return message or None
    if isinstance(run_error, str):
        message = run_error.strip()
        return message or None
    return None


def _latest_telegram_run_record() -> dict | None:
    status = _load_telegram_status()
    if isinstance(status, dict):
        run = _telegram_run_info(status)
        outputs = status.get("outputs")
        if run or isinstance(outputs, dict):
            return {
                "run_id": run.get("id"),
                "state": run.get("state"),
                "outputs": outputs if isinstance(outputs, dict) else {},
            }

    history = _load_telegram_history()
    for item in reversed(history):
        run_id = item.get("run_id")
        outputs = item.get("outputs")
        if run_id or isinstance(outputs, dict):
            return {
                "run_id": run_id,
                "state": item.get("state"),
                "outputs": outputs if isinstance(outputs, dict) else {},
            }
    return None


def _clean_report_line(line: str) -> str:
    text = line.strip()
    if not text:
        return ""
    text = re.sub(r"\[\[.*?\]\]", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_markdown_highlights(path: Path, limit: int = 8) -> list[str]:
    highlights: list[str] = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[[FEED_NOTE]]") or line.startswith("[[SUBGROUP]]"):
            continue
        if line.startswith("# Trend Agent Report"):
            continue
        if line.startswith("### "):
            cleaned = _clean_report_line(line[4:])
            if cleaned:
                highlights.append(cleaned)
        elif line.startswith("## "):
            cleaned = _clean_report_line(line[3:])
            if cleaned:
                highlights.append(cleaned)
        elif line.startswith("- "):
            cleaned = _clean_report_line(line)
            if cleaned:
                highlights.append(cleaned)
        if len(highlights) >= limit:
            break
    return highlights


def _extract_html_highlights(path: Path, limit: int = 8) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(h1|h2|h3|li|p|summary|div)>", "\n", text)
    text = re.sub(r"(?i)<(h1|h2|h3|li|p|summary|div)[^>]*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    highlights: list[str] = []
    for raw_line in text.splitlines():
        cleaned = _clean_report_line(raw_line)
        if not cleaned or cleaned == "Trend Agent Report":
            continue
        highlights.append(cleaned)
        if len(highlights) >= limit:
            break
    return highlights


def _latest_report_path(run_record: dict | None) -> Path | None:
    candidates: list[Path] = []
    outputs = run_record.get("outputs", {}) if isinstance(run_record, dict) else {}
    if isinstance(outputs, dict):
        for key in ("md_path", "html_path"):
            value = outputs.get(key)
            if value:
                candidates.append(Path(str(value)))
    run_id = ""
    if isinstance(run_record, dict):
        run_id = str(run_record.get("run_id") or "").strip()
    if run_id:
        candidates.append(REPORT_DIR / f"trend_report_{run_id}.md")
        candidates.append(HTML_REPORT_DIR / f"trend_report_{run_id}.html")
    candidates.extend(sorted(REPORT_DIR.glob("trend_report_*.md"), reverse=True))
    candidates.extend(sorted(HTML_REPORT_DIR.glob("trend_report_*.html"), reverse=True))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = Path(candidate)
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _telegram_last_run_id_text() -> str:
    run_record = _latest_telegram_run_record()
    if not isinstance(run_record, dict):
        return "No runs yet"
    run_id = str(run_record.get("run_id") or "").strip()
    return run_id or "No runs yet"


def _telegram_highlights_text() -> str:
    run_record = _latest_telegram_run_record()
    if not isinstance(run_record, dict):
        return "No runs yet"

    run_id = str(run_record.get("run_id") or "").strip() or "unknown"
    try:
        report_path = _latest_report_path(run_record)
        if report_path is None:
            return f"Could not locate the latest report for run_id={run_id}"
        if report_path.suffix.lower() == ".md":
            highlights = _extract_markdown_highlights(report_path)
        else:
            highlights = _extract_html_highlights(report_path)
        if not highlights:
            return f"No highlights found for run_id={run_id}"
        return "\n".join([f"Latest run: {run_id}"] + highlights[:8])
    except Exception:
        logger.exception("TG highlights extraction failed")
        return f"Could not extract highlights for run_id={run_id}"


def _telegram_alias_map() -> dict[str, str]:
    return {
        "ping": "ping",
        "status": "status",
        "report": "report",
        "run": "report",
        "help": "help",
        "last": "last",
        "hl": "highlights",
        "highlight": "highlights",
        "highlights": "highlights",
        "summary": "highlights",
        "stats": "stats",
        "errors": "errors",
        "health": "health",
    }


def _telegram_command_name(text: str) -> str:
    normalized = (text or "").strip().lower()
    if normalized.startswith("/"):
        normalized = normalized[1:]
    cmd = normalized.split(None, 1)[0].split("@", 1)[0] if normalized else ""
    return _telegram_alias_map().get(cmd, "")


def _telegram_voice_model_name() -> str:
    try:
        config = load_config()
    except ConfigError as exc:
        logger.warning("TG voice config load failed; using default model small: %s", exc)
        return "small"
    model_name = str(config.get("telegram_voice_model") or "").strip()
    return model_name or "small"


def _telegram_command_text_from_transcription(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.lower().startswith("cmd:"):
        return stripped[4:].strip()
    return stripped


def _voice_command_tokens(text: str) -> list[str]:
    strip_chars = string.punctuation + "“”‘’"
    collapsed = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not collapsed:
        return []
    tokens = [part.strip(strip_chars) for part in collapsed.split(" ")]
    return [token for token in tokens if token]


def parse_voice_command(transcript: str, duration_sec: float | None) -> str | None:
    stripped = re.sub(r"\s+", " ", (transcript or "").strip())
    if not stripped:
        return None

    normalized = stripped.lower()
    if normalized.startswith("/"):
        command_name = _telegram_command_name(stripped)
        return f"/{command_name}" if command_name else None
    if normalized.startswith("cmd:"):
        command_name = _telegram_command_name(_telegram_command_text_from_transcription(stripped))
        return f"/{command_name}" if command_name else None

    tokens = _voice_command_tokens(stripped)
    if not tokens:
        return None

    if tokens[0] in {"slash", "cmd"}:
        fallback_text = "/" + " ".join(tokens[1:])
        command_name = _telegram_command_name(fallback_text)
        return f"/{command_name}" if command_name else None

    if len(tokens) > 3:
        return None
    if duration_sec is not None and duration_sec > 3:
        return None

    canonical_tokens = [_telegram_alias_map().get(token, "") for token in tokens]
    if not canonical_tokens or not canonical_tokens[0]:
        return None
    if any(not token for token in canonical_tokens):
        return None
    if len(set(canonical_tokens)) != 1:
        return None
    return f"/{canonical_tokens[0]}"


def _handle_telegram_text(chat_id: int, text: str, source: str = "text") -> str:
    logger.info("TG recv chat_id=%s source=%s text=%r", chat_id, source, text)
    cmd = _telegram_command_name(text)

    if cmd:
        logger.info("TG cmd=%s chat_id=%s source=%s", cmd, chat_id, source)
        record_command(f"/{cmd}")

    if cmd == "ping":
        return "pong ?"

    if cmd == "status":
        return "TrendAgent alive ?"

    if cmd == "report":
        if RUN_TRIGGER_LOCK.locked():
            return "Report already running ?"
        record_report_trigger()

        def run_report() -> None:
            exit_code = -1
            try:
                logger.info("TG trigger report start chat_id=%s", chat_id)
                exit_code = main(start_telegram=False)
            except Exception:
                logger.exception("Telegram-triggered report failed")
            finally:
                logger.info("TG trigger report finished (exit_code=%s) chat_id=%s", exit_code, chat_id)

        threading.Thread(target=run_report, daemon=True).start()
        return "Report triggered ?"

    if cmd == "help":
        return (
            "Commands: ping, status, report/run, hl/highlights (alias: summary), last, help, stats, errors, health. "
            "Voice: short commands like ping/status/help run directly; longer speech is transcribed and echoed."
        )

    if cmd == "last":
        return _telegram_last_run_id_text()

    if cmd == "highlights":
        return _telegram_highlights_text()

    if cmd == "errors":
        status = _load_telegram_status()
        if status is None:
            return "No status available yet."
        run_state = str(_telegram_run_info(status).get("state") or "UNKNOWN").upper()
        error_message = _telegram_error_message(status)
        if run_state == "RUNNING":
            return f"state=RUNNING {error_message or 'No errors recorded.'}"
        return error_message or "No errors recorded."

    if cmd == "stats":
        status = _load_telegram_status()
        if status is None:
            return "No status available yet."
        run_state = str(_telegram_run_info(status).get("state") or "UNKNOWN").upper()
        metrics = _telegram_metrics(status)
        prefix = "state=RUNNING " if run_state == "RUNNING" else ""
        return (
            f"{prefix}feeds_ok={int(metrics.get('feeds_ok', 0) or 0)} "
            f"feeds_failed={int(metrics.get('feeds_failed', 0) or 0)} "
            f"items_kept={int(metrics.get('items_total', 0) or 0)} "
            f"items_new={int(metrics.get('items_new', 0) or 0)} "
            f"items_duplicates={int(metrics.get('items_duplicates', 0) or 0)}"
        )

    if cmd == "health":
        return format_health_text()

    return "I didn't understand. Try: ping, status, report/run, hl/highlights, last, help, or send a voice note."


def _handle_telegram_voice_message(chat_id: int, message: dict, token: str) -> str:
    model_name = _telegram_voice_model_name()
    logger.info("TG voice recv chat_id=%s model=%s", chat_id, model_name)
    media_payload = message.get("voice") if isinstance(message.get("voice"), dict) else message.get("audio")
    duration_sec = None
    if isinstance(media_payload, dict) and media_payload.get("duration") is not None:
        try:
            duration_sec = float(media_payload.get("duration") or 0)
        except (TypeError, ValueError):
            duration_sec = None
    transcription = transcribe_telegram_media(token=token, message=message, logger=logger, model_size=model_name)
    logger.info("TG voice text chat_id=%s text=%r", chat_id, transcription)
    parsed_command = parse_voice_command(transcription, duration_sec=duration_sec)
    executed = bool(parsed_command)
    logger.info(
        "TG voice command_parse chat_id=%s duration=%s transcript=%r parsed_command=%r executed=%s",
        chat_id,
        duration_sec,
        transcription,
        parsed_command,
        executed,
    )

    if not parsed_command:
        return f"Transcription: {transcription}"

    execute_started_at = time.perf_counter()
    reply = _handle_telegram_text(chat_id, parsed_command, source="voice")
    logger.info(
        "TG voice execute chat_id=%s command=%r elapsed=%.2fs",
        chat_id,
        parsed_command,
        time.perf_counter() - execute_started_at,
    )
    return reply


def handle_telegram_message(chat_id: int, text: str, message: dict | None = None, token: str | None = None) -> str:
    has_voice = isinstance(message, dict) and isinstance(message.get("voice"), dict)
    has_audio = isinstance(message, dict) and isinstance(message.get("audio"), dict)
    if has_voice or has_audio:
        if not token:
            return "Voice transcription failed: Telegram bot token missing."
        try:
            return _handle_telegram_voice_message(chat_id, message or {}, token)
        except VoiceTranscriptionError as exc:
            logger.warning("TG voice transcription failed chat_id=%s error=%s", chat_id, exc)
            return f"Voice transcription failed: {exc}"
        except Exception:
            logger.exception("TG voice transcription unexpected failure chat_id=%s", chat_id)
            return "Voice transcription failed due to an unexpected error."
    return _handle_telegram_text(chat_id, text, source="text")


class FeedFetchError(RuntimeError):
    pass


def load_feed_failover_state(path: Path) -> dict:
    if not path.exists():
        return {"feeds": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read feed failover state: %s", path, exc_info=True)
        return {"feeds": {}}
    if not isinstance(data, dict):
        return {"feeds": {}}
    feeds = data.get("feeds")
    if not isinstance(feeds, dict):
        data["feeds"] = {}
    return data


def save_feed_failover_state(path: Path, state: dict) -> None:
    write_json_atomic(path, state)


def make_feed_runtime_key(category_name: str, subgroup_name: str | None, feed_def: dict) -> str:
    if isinstance(feed_def.get("id"), str) and str(feed_def.get("id")).strip():
        return normalize_section_key(category_name, str(feed_def.get("id")).strip())
    if isinstance(feed_def.get("name"), str) and str(feed_def.get("name")).strip():
        return normalize_section_key(category_name, str(feed_def.get("name")).strip())
    urls = feed_def.get("urls", [])
    primary = str(urls[0]).strip() if isinstance(urls, list) and urls else ""
    seed = normalize_url(primary) or primary
    subgroup_part = f"{subgroup_name} " if subgroup_name else ""
    return normalize_section_key(category_name, f"{subgroup_part}{seed}")


def normalize_category_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_") or "general"


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urllib.parse.urlsplit(raw)
    except Exception:
        return raw

    scheme = (parts.scheme or "").lower()
    netloc = (parts.netloc or "").lower()
    path = parts.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    filtered_query: list[tuple[str, str]] = []
    for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        key = (k or "").strip()
        key_l = key.lower()
        if key_l.startswith("utm_") or key_l in TRACKING_QUERY_PARAM_NAMES:
            continue
        filtered_query.append((key, v))

    filtered_query.sort(key=lambda kv: (kv[0].lower(), kv[0], kv[1]))
    query = urllib.parse.urlencode(filtered_query, doseq=True)
    normalized = urllib.parse.urlunsplit((scheme, netloc, path, query, ""))
    return normalized or raw


def normalize_section_key(category_name: str, section_name: str) -> str:
    return f"{normalize_category_key(category_name)}__{normalize_category_key(section_name)}"


def infer_feed_title_from_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
    except Exception:
        return "Feed"
    host = (parts.netloc or "").lower()
    if host in FRIENDLY_FEED_HOST_TITLES:
        return FRIENDLY_FEED_HOST_TITLES[host]

    host = host.split(":", 1)[0]
    labels = [p for p in host.split(".") if p and p not in {"www", "feeds", "feed"}]
    if labels:
        base = labels[0]
        if base == "bbci":
            return "BBC"
        return base.replace("-", " ").replace("_", " ").title()
    return "Feed"


def _safe_date_from_iso(value: str | None) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


def load_history_urls_store(path: Path) -> dict[str, dict[str, list[str]]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read history URLs store: %s", path, exc_info=True)
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, dict[str, list[str]]] = {}
    for day_key, per_section in data.items():
        if not isinstance(day_key, str) or not isinstance(per_section, dict):
            continue
        section_map: dict[str, list[str]] = {}
        for section_key, urls in per_section.items():
            if not isinstance(section_key, str) or not isinstance(urls, list):
                continue
            section_map[section_key] = [u for u in urls if isinstance(u, str) and u]
        cleaned[day_key] = section_map
    return cleaned


def prune_history_urls_store(
    store: dict[str, dict[str, list[str]]],
    *,
    today: date,
    window_days: int,
) -> dict[str, dict[str, list[str]]]:
    cutoff = today - timedelta(days=max(1, int(window_days)))
    pruned: dict[str, dict[str, list[str]]] = {}
    for day_key, per_section in store.items():
        d = _safe_date_from_iso(day_key)
        if d is None or d < cutoff:
            continue
        pruned[day_key] = per_section
    return pruned


def build_seen_urls_by_section(
    store: dict[str, dict[str, list[str]]],
    *,
    today: date,
    window_days: int,
) -> dict[str, set[str]]:
    cutoff = today - timedelta(days=max(1, int(window_days)))
    seen: dict[str, set[str]] = {}
    for day_key, per_section in store.items():
        d = _safe_date_from_iso(day_key)
        if d is None or d < cutoff:
            continue
        for section_key, urls in per_section.items():
            bucket = seen.setdefault(section_key, set())
            for url in urls:
                norm = normalize_url(url)
                if norm:
                    bucket.add(norm)
    return seen


def save_history_urls_store(path: Path, store: dict[str, dict[str, list[str]]]) -> None:
    write_json_atomic(path, store)


def ensure_knowledge_files() -> None:
    defaults = {
        KNOWLEDGE_PREFS_PATH: (
            "agent_id: TrendAgent-Local-01\n"
            "timezone: America/New_York\n"
            "language: en\n"
            "summary_style: concise\n"
        ),
        KNOWLEDGE_SOURCES_PATH: (
            "priority:\n"
            "  - official\n"
            "  - primary_media\n"
            "  - secondary_media\n"
            "trust_rules:\n"
            "  require_two_sources_for_major_claims: true\n"
        ),
        KNOWLEDGE_RULES_PATH: (
            "freshness_window_days: 7\n"
            "dedupe_by_normalized_url: true\n"
            "max_items_per_feed: 3\n"
        ),
    }
    for path, content in defaults.items():
        if path.exists():
            continue
        path.write_text(content, encoding="utf-8")


def init_recall_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
                normalized_url TEXT PRIMARY KEY,
                section_key TEXT,
                title TEXT,
                source_url TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_items_last_seen ON seen_items(last_seen_at)"
        )
        conn.commit()


def persist_run_memory_files(status: dict) -> dict:
    started_at = str(status.get("run", {}).get("started_at") or "")
    day_key = started_at[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", started_at[:10]) else date.today().isoformat()
    day_dir = MEMORY_RUN_DIR / day_key
    day_dir.mkdir(parents=True, exist_ok=True)

    run_json_path = day_dir / "run.json"
    errors_log_path = day_dir / "errors.log"
    write_json_atomic(run_json_path, status)

    lines: list[str] = []
    run_id = str(status.get("run", {}).get("id") or "")
    run_state = str(status.get("run", {}).get("state") or "")
    finished_at = str(status.get("run", {}).get("finished_at") or datetime.now().isoformat(timespec="seconds"))
    run_error = status.get("run", {}).get("error")
    if run_error:
        lines.append(f"[{finished_at}] run_id={run_id} state={run_state} run_error={run_error}")
    for event in status.get("events", []):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type.endswith("_error") or event_type == "feed_error":
            lines.append(f"[{finished_at}] run_id={run_id} event={event_type} detail={event}")
    if lines:
        with errors_log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    return {
        "run_day_dir": str(day_dir),
        "run_json_path": str(run_json_path),
        "errors_log_path": str(errors_log_path),
    }


def _default_agent_memory() -> dict:
    return {
        "agent_id": DEFAULT_AGENT_ID,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "totals": {
            "runs": 0,
            "successes": 0,
            "failures": 0,
        },
        "streaks": {
            "success": 0,
            "failure": 0,
        },
        "last_run": {
            "id": None,
            "state": None,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "feeds_ok": 0,
            "feeds_failed": 0,
            "items_total": 0,
            "error": None,
        },
        "health": {
            "state": "unknown",
            "reason": "No runs recorded yet",
            "updated_at": None,
        },
    }


def load_agent_memory(path: Path) -> dict:
    if not path.exists():
        return _default_agent_memory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read agent memory: %s", path, exc_info=True)
        return _default_agent_memory()
    if not isinstance(data, dict):
        return _default_agent_memory()
    merged = _default_agent_memory()
    for key in ("agent_id", "created_at", "updated_at"):
        if key in data and isinstance(data.get(key), str):
            merged[key] = data[key]
    for key in ("totals", "streaks", "last_run", "health"):
        if isinstance(data.get(key), dict):
            merged[key].update(data[key])
    return merged


def compute_agent_health(run_state: str, feeds_ok: int, feeds_failed: int, failure_streak: int) -> tuple[str, str]:
    if run_state == "FAILED" or failure_streak >= 3:
        return "failed", "Run failed or repeated failures reached threshold"
    if feeds_failed > 0:
        return "degraded", "Run succeeded with partial feed failures"
    if feeds_ok <= 0:
        return "degraded", "Run succeeded but no feeds produced data"
    return "healthy", "Run succeeded without feed failures"


def update_agent_memory(path: Path, status: dict) -> dict:
    memory = load_agent_memory(path)
    now_iso = datetime.now().isoformat(timespec="seconds")
    run_state = str(status.get("run", {}).get("state") or "")
    is_success = run_state == "SUCCESS"
    is_failure = run_state == "FAILED"

    totals = memory.setdefault("totals", {})
    totals["runs"] = int(totals.get("runs", 0) or 0) + 1
    totals["successes"] = int(totals.get("successes", 0) or 0) + (1 if is_success else 0)
    totals["failures"] = int(totals.get("failures", 0) or 0) + (1 if is_failure else 0)

    streaks = memory.setdefault("streaks", {})
    success_streak = int(streaks.get("success", 0) or 0)
    failure_streak = int(streaks.get("failure", 0) or 0)
    if is_success:
        success_streak += 1
        failure_streak = 0
    elif is_failure:
        failure_streak += 1
        success_streak = 0
    else:
        success_streak = 0
        failure_streak = 0
    streaks["success"] = success_streak
    streaks["failure"] = failure_streak

    metrics = status.get("metrics", {})
    feeds_ok = int(metrics.get("feeds_ok", 0) or 0)
    feeds_failed = int(metrics.get("feeds_failed", 0) or 0)
    items_total = int(metrics.get("items_total", 0) or 0)
    run_error = status.get("run", {}).get("error")
    if run_error is None:
        run_error = status.get("error")

    memory["last_run"] = {
        "id": status.get("run", {}).get("id"),
        "state": run_state or None,
        "started_at": status.get("run", {}).get("started_at"),
        "finished_at": status.get("run", {}).get("finished_at"),
        "duration_seconds": status.get("run", {}).get("duration_seconds"),
        "feeds_ok": feeds_ok,
        "feeds_failed": feeds_failed,
        "items_total": items_total,
        "error": run_error,
    }

    health_state, health_reason = compute_agent_health(run_state, feeds_ok, feeds_failed, failure_streak)
    memory["health"] = {
        "state": health_state,
        "reason": health_reason,
        "updated_at": now_iso,
    }
    if not memory.get("created_at"):
        memory["created_at"] = now_iso
    memory["updated_at"] = now_iso

    write_json_atomic(path, memory)
    return memory


def memory_summary(memory: dict) -> dict:
    totals = memory.get("totals", {}) if isinstance(memory.get("totals"), dict) else {}
    streaks = memory.get("streaks", {}) if isinstance(memory.get("streaks"), dict) else {}
    health = memory.get("health", {}) if isinstance(memory.get("health"), dict) else {}
    return {
        "agent_id": memory.get("agent_id"),
        "totals": {
            "runs": int(totals.get("runs", 0) or 0),
            "successes": int(totals.get("successes", 0) or 0),
            "failures": int(totals.get("failures", 0) or 0),
        },
        "streaks": {
            "success": int(streaks.get("success", 0) or 0),
            "failure": int(streaks.get("failure", 0) or 0),
        },
        "health": {
            "state": health.get("state"),
            "reason": health.get("reason"),
            "updated_at": health.get("updated_at"),
        },
    }


def _entry_published_dt(entry) -> datetime:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6])
            except Exception:
                continue
    for attr in ("published", "updated", "created"):
        raw = getattr(entry, attr, None)
        if isinstance(raw, str):
            text = raw.strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(text)
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
            except Exception:
                continue
    return datetime.min


def _strip_invalid_xml_control_bytes(data: bytes) -> bytes:
    # XML 1.0 forbids most ASCII control chars; some feeds include them.
    return re.sub(rb"[\x00-\x08\x0B\x0C\x0E-\x1F]", b"", data)


def _parse_iso_datetime(text: str | None) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        return None


def _title_from_url_slug(url: str) -> str:
    try:
        path = urllib.parse.urlsplit(url).path or ""
    except Exception:
        path = ""
    slug = path.rstrip("/").split("/")[-1] if path else ""
    if not slug:
        return infer_feed_title_from_url(url)
    text = slug.replace("-", " ").replace("_", " ").strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].upper() + text[1:] if text else "Untitled item"


def normalize_item_title(text: str) -> str:
    # Some feeds (notably GovInfo) include tabs/newlines in titles.
    return re.sub(r"\s+", " ", str(text or "")).strip() or "(no title)"


def _parse_govcn_html_entries(
    html_bytes: bytes,
    *,
    source_url: str,
    fetch_count: int,
    _depth: int = 0,
) -> list[dict]:
    if _depth > 2:
        return []
    try:
        html_text = html_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return []
    try:
        parts = urllib.parse.urlsplit(source_url)
    except Exception:
        return []
    host = (parts.netloc or "").lower()
    path = parts.path or ""
    if "gov.cn" not in host:
        return []
    if "/yaowen/" not in path and "/zhengce/" not in path:
        return []

    # gov.cn pages often JS-redirect list index pages.
    redirect_match = re.search(
        r"window\.location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']",
        html_text,
        re.IGNORECASE,
    )
    if redirect_match:
        redirect_url = urllib.parse.urljoin(source_url, redirect_match.group(1).strip())
        try:
            redirected_bytes, redirected_ct, _, _ = _fetch_feed_bytes(redirect_url)
            if "html" in (redirected_ct or ""):
                redirected_items = _parse_govcn_html_entries(
                    redirected_bytes,
                    source_url=redirect_url,
                    fetch_count=fetch_count,
                    _depth=_depth + 1,
                )
                if redirected_items:
                    return redirected_items
        except Exception:
            pass

    items: list[dict] = []
    seen_links: set[str] = set()
    for m in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html_text, re.IGNORECASE | re.DOTALL):
        href = (m.group(1) or "").strip()
        label_html = m.group(2) or ""
        if not href:
            continue
        full_url = urllib.parse.urljoin(source_url, href)
        try:
            u = urllib.parse.urlsplit(full_url)
        except Exception:
            continue
        if "gov.cn" not in (u.netloc or "").lower():
            continue
        p = (u.path or "").lower()
        is_news = bool(re.search(r"/yaowen/liebiao/\d{6}/content_\d+\.htm$", p))
        is_policy = bool(re.search(r"/zhengce/(content/)?\d{6}/content_\d+\.htm$", p))
        if not (is_news or is_policy):
            continue
        norm = normalize_url(full_url)
        if not norm or norm in seen_links:
            continue
        seen_links.add(norm)
        label = re.sub(r"<[^>]+>", " ", label_html)
        label = normalize_item_title(label)
        items.append({
            "title": label,
            "link": full_url,
            "normalized_url": norm,
            "published_dt": datetime.min,
        })
        if len(items) >= max(1, int(fetch_count)):
            break
    return items


def _parse_govcn_json_entries(json_bytes: bytes, *, source_url: str, fetch_count: int) -> list[dict]:
    try:
        parts = urllib.parse.urlsplit(source_url)
    except Exception:
        return []
    host = (parts.netloc or "").lower()
    if "gov.cn" not in host:
        return []
    try:
        text = json_bytes.decode("utf-8", errors="ignore")
        payload = json.loads(text)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        raw_url = row.get("URL") or row.get("url") or row.get("LINK") or row.get("link")
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        link = urllib.parse.urljoin(source_url, raw_url.strip())
        norm = normalize_url(link)
        if not norm or norm in seen:
            continue
        seen.add(norm)

        raw_title = row.get("TITLE") or row.get("title") or row.get("SUB_TITLE") or row.get("subtitle")
        title = normalize_item_title(str(raw_title or _title_from_url_slug(link)))

        raw_date = row.get("DOCRELPUBTIME") or row.get("PUBLISH_TIME") or row.get("pubDate") or row.get("date")
        published_dt = datetime.min
        if isinstance(raw_date, str) and raw_date.strip():
            parsed = _parse_iso_datetime(raw_date.strip())
            if parsed is None:
                m = re.match(r"^(\\d{4})-(\\d{2})-(\\d{2})$", raw_date.strip())
                if m:
                    try:
                        parsed = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    except Exception:
                        parsed = None
            if parsed is not None:
                published_dt = parsed

        items.append({
            "title": title,
            "link": link,
            "normalized_url": norm,
            "published_dt": published_dt,
        })
        if len(items) >= max(1, int(fetch_count)):
            break
    return items


def _govcn_json_feed_title(url: str) -> str | None:
    upper = url.upper()
    if "YAOWENLIEBIAO.JSON" in upper:
        return "State Council News"
    if "ZUIXINZHENGCE.JSON" in upper:
        return "Latest Policies"
    return None


def resolve_display_feed_title(configured_name: str | None, fetched_title: str | None, url: str) -> str:
    configured = str(configured_name or "").strip()
    fetched = str(fetched_title or "").strip()
    if configured:
        if not fetched:
            return configured
        if configured.lower() == fetched.lower():
            return configured
        if configured.lower() in fetched.lower():
            return fetched
        return f"{configured} | {fetched}"
    if fetched:
        return fetched
    return infer_feed_title_from_url(url)


def _sitemap_entry_title_and_date(node: ET.Element) -> tuple[str | None, datetime | None]:
    title_text: str | None = None
    date_text: str | None = None
    for child in node.iter():
        tag = str(child.tag or "")
        if not isinstance(child.text, str):
            continue
        local = tag.rsplit("}", 1)[-1].lower()
        if local == "title" and not title_text and child.text.strip():
            title_text = normalize_item_title(child.text)
        elif local in {"publication_date", "lastmod"} and not date_text and child.text.strip():
            date_text = child.text.strip()
    return title_text, _parse_iso_datetime(date_text)


def _parse_sitemap_entries(
    xml_bytes: bytes,
    *,
    source_url: str,
    fetch_count: int,
    _depth: int = 0,
) -> list[dict]:
    if _depth > 2:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return []

    root_local = str(root.tag or "").rsplit("}", 1)[-1].lower()
    items: list[dict] = []

    if root_local == "sitemapindex":
        child_urls: list[str] = []
        for node in root.iter():
            if str(node.tag or "").rsplit("}", 1)[-1].lower() == "loc" and isinstance(node.text, str):
                loc = node.text.strip()
                if loc:
                    child_urls.append(loc)
        # White House uses many child sitemaps; prioritize post sitemaps first.
        if "whitehouse.gov" in urllib.parse.urlsplit(source_url).netloc.lower():
            child_urls.sort(key=lambda u: (0 if "post-sitemap" in u else 1, u))
        seen_child: set[str] = set()
        for child_url in child_urls:
            if child_url in seen_child:
                continue
            seen_child.add(child_url)
            try:
                child_bytes, _, _, _ = _fetch_feed_bytes(child_url)
            except FeedFetchError:
                continue
            items.extend(_parse_sitemap_entries(
                child_bytes,
                source_url=child_url,
                fetch_count=fetch_count,
                _depth=_depth + 1,
            ))
            if len(items) >= max(1, int(fetch_count)) * 3:
                break
        items.sort(key=lambda item: item.get("published_dt") or datetime.min, reverse=True)
        return items[: max(1, int(fetch_count))]

    if root_local != "urlset":
        return []

    source_host = urllib.parse.urlsplit(source_url).netloc.lower()
    for url_node in list(root):
        if str(url_node.tag or "").rsplit("}", 1)[-1].lower() != "url":
            continue
        loc = None
        for child in list(url_node):
            if str(child.tag or "").rsplit("}", 1)[-1].lower() == "loc" and isinstance(child.text, str):
                loc = child.text.strip()
                break
        if not loc:
            continue
        norm = normalize_url(loc)
        if not norm:
            continue
        title_text, published_dt = _sitemap_entry_title_and_date(url_node)
        if not title_text:
            title_text = _title_from_url_slug(loc)
        # Avoid ultra-generic navigational URLs when using broad sitemaps.
        if "whitehouse.gov" in source_host:
            path = urllib.parse.urlsplit(loc).path.lower()
            if path in {"/news/", "/"}:
                continue
            if not re.search(r"/\d{4}/\d{2}/", path):
                continue
        items.append({
            "title": title_text,
            "link": loc,
            "normalized_url": norm,
            "published_dt": published_dt or datetime.min,
        })

    items.sort(key=lambda item: item.get("published_dt") or datetime.min, reverse=True)
    return items[: max(1, int(fetch_count))]


def _fetch_feed_bytes(url: str, timeout: int = HTTP_FETCH_TIMEOUT_SECONDS) -> tuple[bytes, str, int | None, str]:
    req = urllib.request.Request(url, headers=HTTP_RSS_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            status = getattr(resp, "status", None)
            content_type = str(resp.headers.get("Content-Type", "") or "").lower()
            final_url = str(getattr(resp, "geturl", lambda: url)() or url)
            return data, content_type, status, final_url
    except urllib.error.HTTPError as e:
        raise FeedFetchError(f"HTTP {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise FeedFetchError(f"Network error: {reason}") from e
    except Exception as e:
        raise FeedFetchError(f"Request failed: {e}") from e


def select_feed_items(
    items: list[dict],
    seen_urls: set[str],
    *,
    display_count: int = DEFAULT_DISPLAY_COUNT,
    freshness_window_days: int = HISTORY_DEDUPE_LOOKBACK_DAYS,
) -> tuple[list[dict], str | None]:
    cutoff_dt = datetime.now() - timedelta(days=max(1, int(freshness_window_days)))
    sorted_items = sorted(
        items,
        key=lambda item: item.get("published_dt") or datetime.min,
        reverse=True,
    )
    fresh_items: list[dict] = []
    recent_repeat_items: list[dict] = []
    older_repeat_items: list[dict] = []
    for item in sorted_items:
        norm = str(item.get("normalized_url", "") or "")
        item_copy = dict(item)
        item_copy["is_repeat"] = False
        if norm and norm in seen_urls:
            published_dt = item_copy.get("published_dt")
            if isinstance(published_dt, datetime) and published_dt >= cutoff_dt:
                recent_repeat_items.append(item_copy)
            else:
                older_repeat_items.append(item_copy)
        else:
            fresh_items.append(item_copy)

    selected: list[dict] = fresh_items[:display_count]
    if len(selected) < display_count:
        needed = display_count - len(selected)
        for item in recent_repeat_items[:needed]:
            item["is_repeat"] = True
            selected.append(item)
        if len(selected) < display_count:
            needed = display_count - len(selected)
            for item in older_repeat_items[:needed]:
                item["is_repeat"] = True
                selected.append(item)

    fresh_count = sum(1 for item in selected if not item.get("is_repeat"))
    repeat_count = sum(1 for item in selected if item.get("is_repeat"))
    notes: list[str] = []
    latest_published_dt = None
    for item in sorted_items:
        published_dt = item.get("published_dt")
        if isinstance(published_dt, datetime) and published_dt != datetime.min:
            latest_published_dt = published_dt
            break

    if not items:
        notes.append("Feed returned no entries.")
    elif latest_published_dt is not None and latest_published_dt < cutoff_dt:
        notes.append(f"No news update within the last {int(freshness_window_days)} days.")
    elif not selected:
        notes.append("Feed returned entries, but none could be selected.")
    elif fresh_count == 0 and repeat_count > 0:
        notes.append(
            f"No new update today. Showing the latest items from the last {int(freshness_window_days)} days."
        )
    elif repeat_count > 0:
        notes.append(
            f"Some items have appeared within the last {int(freshness_window_days)} days due to limited new content."
        )
    if 0 < len(selected) < display_count:
        notes.append(f"Only {len(selected)} item(s) available from this feed.")

    return selected, (" ".join(notes) if notes else None)


def extract_category_urls_from_markdown(md_text: str) -> dict[str, set[str]]:
    category_urls: dict[str, set[str]] = {}
    current_category = "general"
    link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    for raw_line in md_text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            header = line[3:].strip()
            header = re.sub(r"\s+\([^)]*\)\s*$", "", header).strip()
            current_category = normalize_category_key(header)
            category_urls.setdefault(current_category, set())
            continue
        if not line.startswith("- "):
            continue
        for url in link_re.findall(line):
            norm = normalize_url(url)
            if norm:
                category_urls.setdefault(current_category, set()).add(norm)

    return category_urls


def extract_section_urls_from_markdown(md_text: str) -> dict[str, set[str]]:
    section_urls: dict[str, set[str]] = {}
    current_category = "general"
    current_feed = "items"
    link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    for raw_line in md_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            header = line[3:].strip()
            header = re.sub(r"\s+\([^)]*\)\s*$", "", header).strip()
            current_category = normalize_category_key(header)
            current_feed = "items"
            continue
        if line.startswith(MD_SUBGROUP_PREFIX) or line.startswith(MD_FEED_NOTE_PREFIX):
            continue
        if line.startswith("### "):
            current_feed = line[4:].strip() or "Feed"
            section_key = normalize_section_key(current_category, current_feed)
            section_urls.setdefault(section_key, set())
            continue
        if not line.startswith("- "):
            continue
        cleaned = line.replace(MD_REPEAT_TOKEN, "").replace(MD_FRESH_TOKEN, "").strip()
        section_key = normalize_section_key(current_category, current_feed)
        for url in link_re.findall(cleaned):
            norm = normalize_url(url)
            if norm:
                section_urls.setdefault(section_key, set()).add(norm)

    return section_urls


def load_recent_seen_urls_by_section_from_history(lookback_days: int = HISTORY_DEDUPE_LOOKBACK_DAYS) -> dict[str, set[str]]:
    if not HISTORY_PATH.exists():
        return {}
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read history.json for section-level backfill", exc_info=True)
        return {}
    if not isinstance(data, list):
        return {}

    cutoff = datetime.now() - timedelta(days=max(1, int(lookback_days)))
    result: dict[str, set[str]] = {}

    for item in data:
        if not isinstance(item, dict):
            continue
        started_at = item.get("started_at")
        if isinstance(started_at, str):
            try:
                if datetime.fromisoformat(started_at) < cutoff:
                    continue
            except Exception:
                pass

        outputs = item.get("outputs")
        if not isinstance(outputs, dict):
            continue
        md_path_raw = outputs.get("md_path")
        if not isinstance(md_path_raw, str) or not md_path_raw.strip():
            continue
        try:
            md_text = Path(md_path_raw).read_text(encoding="utf-8")
        except Exception:
            continue

        for section_key, urls in extract_section_urls_from_markdown(md_text).items():
            if not urls:
                continue
            result.setdefault(section_key, set()).update(urls)

    return result


def load_recent_history_urls_by_category(lookback_days: int = HISTORY_DEDUPE_LOOKBACK_DAYS) -> dict[str, set[str]]:
    if not HISTORY_PATH.exists():
        return {}

    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read history.json for dedupe index", exc_info=True)
        return {}

    if not isinstance(data, list):
        return {}

    cutoff = datetime.now() - timedelta(days=max(1, int(lookback_days)))
    result: dict[str, set[str]] = {}

    for item in data:
        if not isinstance(item, dict):
            continue

        started_at = item.get("started_at")
        include_entry = True
        if isinstance(started_at, str):
            try:
                include_entry = datetime.fromisoformat(started_at) >= cutoff
            except Exception:
                include_entry = True
        if not include_entry:
            continue

        dedupe = item.get("dedupe")
        if isinstance(dedupe, dict):
            categories = dedupe.get("categories")
            if isinstance(categories, dict):
                for raw_key, payload in categories.items():
                    cat_key = normalize_category_key(str(raw_key))
                    urls: list[str] = []
                    if isinstance(payload, dict):
                        raw_urls = payload.get("normalized_urls", [])
                        if isinstance(raw_urls, list):
                            urls = [u for u in raw_urls if isinstance(u, str)]
                    elif isinstance(payload, list):
                        urls = [u for u in payload if isinstance(u, str)]
                    if urls:
                        bucket = result.setdefault(cat_key, set())
                        for u in urls:
                            norm = normalize_url(u)
                            if norm:
                                bucket.add(norm)
                continue

        outputs = item.get("outputs")
        if not isinstance(outputs, dict):
            continue
        md_path_raw = outputs.get("md_path")
        if not isinstance(md_path_raw, str) or not md_path_raw.strip():
            continue

        try:
            md_text = Path(md_path_raw).read_text(encoding="utf-8")
        except Exception:
            continue

        for cat_key, urls in extract_category_urls_from_markdown(md_text).items():
            if not urls:
                continue
            result.setdefault(cat_key, set()).update(urls)

    return result


def collect_rss_groups(config: dict) -> list[dict]:
    """Return categorized RSS sources with optional subgroups and fallback URLs.

    Output shape:
    [
      {"category": "politics", "subgroups": [{"name": "Primary Sources", "feeds": [...]}, ...]},
      {"category": "technology", "subgroups": [{"name": None, "feeds": [...]}]},
    ]
    """
    rss_sources = config.get("rss_sources")
    max_active_feeds_per_subgroup = max(
        1,
        int(config.get("max_active_feeds_per_subgroup", DEFAULT_MAX_ACTIVE_FEEDS_PER_SUBGROUP)),
    )
    raw_cap_overrides = config.get("max_active_feeds_per_subgroup_overrides", {})
    cap_overrides: dict[str, int] = {}
    if isinstance(raw_cap_overrides, dict):
        for k, v in raw_cap_overrides.items():
            key = str(k).strip().lower()
            if not key:
                continue
            try:
                cap_overrides[key] = max(1, int(v))
            except Exception:
                continue

    def _cap_subgroup_feeds(feeds: list[dict], limit: int) -> list[dict]:
        # Keep subgroup UI compact: only N active feeds are shown.
        # Overflow feeds become backup URLs for failover.
        if len(feeds) <= limit:
            return feeds

        active: list[dict] = []
        for feed in feeds[:limit]:
            active.append({
                "id": feed.get("id"),
                "name": feed.get("name"),
                "urls": list(feed.get("urls", [])),
            })

        for idx, overflow_feed in enumerate(feeds[limit:]):
            target = active[idx % limit]
            target_urls = target.setdefault("urls", [])
            for url in overflow_feed.get("urls", []):
                if url not in target_urls:
                    target_urls.append(url)

        return active

    if isinstance(rss_sources, dict) and rss_sources:
        groups: list[dict] = []
        for category, source_def in rss_sources.items():
            seen_global: set[str] = set()
            subgroup_entries: list[dict] = []

            def _clean_feed_entries(raw_entries) -> list[dict]:
                if isinstance(raw_entries, (str, dict)):
                    raw_entries = [raw_entries]
                if not isinstance(raw_entries, list):
                    return []

                cleaned_entries: list[dict] = []
                for raw_entry in raw_entries:
                    feed_name = None
                    feed_id = None
                    raw_urls = raw_entry
                    if isinstance(raw_entry, dict):
                        feed_name = raw_entry.get("name")
                        feed_id = raw_entry.get("id")
                        raw_urls = raw_entry.get("urls")
                        if raw_urls is None:
                            raw_urls = raw_entry.get("url")
                    if isinstance(raw_urls, str):
                        raw_urls = [raw_urls]
                    if not isinstance(raw_urls, list):
                        continue

                    cleaned_urls: list[str] = []
                    seen_local: set[str] = set()
                    for url in raw_urls:
                        if not isinstance(url, str):
                            continue
                        u = url.strip()
                        if not u or u in seen_local:
                            continue
                        seen_local.add(u)
                        cleaned_urls.append(u)

                    if not cleaned_urls:
                        continue

                    primary_url = cleaned_urls[0]
                    if primary_url in seen_global:
                        continue
                    seen_global.add(primary_url)
                    for backup_url in cleaned_urls[1:]:
                        seen_global.add(backup_url)

                    cleaned_entries.append({
                        "id": str(feed_id).strip() if isinstance(feed_id, str) and str(feed_id).strip() else None,
                        "name": str(feed_name).strip() if isinstance(feed_name, str) and str(feed_name).strip() else None,
                        "urls": cleaned_urls,
                    })

                return cleaned_entries

            if isinstance(source_def, dict):
                for subgroup_name, subgroup_urls in source_def.items():
                    cleaned_feeds = _clean_feed_entries(subgroup_urls)
                    subgroup_key = str(subgroup_name).replace("_", " ").strip().lower()
                    cap_key = f"{str(category).strip().lower()}.{str(subgroup_name).strip().lower()}"
                    cap_limit = cap_overrides.get(cap_key, max_active_feeds_per_subgroup)
                    if subgroup_key == "primary sources":
                        cap_limit = cap_overrides.get(cap_key, cap_limit)
                    cleaned_feeds = _cap_subgroup_feeds(cleaned_feeds, cap_limit)
                    if cleaned_feeds:
                        subgroup_entries.append({
                            "name": str(subgroup_name).replace("_", " ").strip().title(),
                            "feeds": cleaned_feeds,
                        })
            else:
                cleaned_feeds = _clean_feed_entries(source_def)
                cap_key = str(category).strip().lower()
                cap_limit = cap_overrides.get(cap_key, max_active_feeds_per_subgroup)
                cleaned_feeds = _cap_subgroup_feeds(cleaned_feeds, cap_limit)
                if cleaned_feeds:
                    subgroup_entries.append({"name": None, "feeds": cleaned_feeds})

            if subgroup_entries:
                groups.append({
                    "category": str(category),
                    "subgroups": subgroup_entries,
                })

        if groups:
            return groups

    raise ValueError("Config must contain rss_sources as a non-empty object")


def fetch_rss_entries_detailed(url: str, fetch_count: int = DEFAULT_FETCH_COUNT) -> dict:
    logger.info(f"Fetching RSS: {url}")
    raw_bytes, content_type, http_status, final_url = _fetch_feed_bytes(url)
    result = {
        "request_url": url,
        "final_url": final_url,
        "http_status": http_status,
        "content_type": content_type,
        "fetch_ok": True,
        "parse_ok": False,
        "entries_count": 0,
        "feed_title": "",
        "items": [],
    }
    if "json" in (content_type or "").lower() or url.lower().endswith(".json"):
        govcn_json_items = _parse_govcn_json_entries(raw_bytes, source_url=url, fetch_count=fetch_count)
        if govcn_json_items:
            feed_title = (
                FEED_TITLE_OVERRIDES.get(url.strip())
                or _govcn_json_feed_title(url)
                or infer_feed_title_from_url(url)
            )
            result["parse_ok"] = True
            result["entries_count"] = len(govcn_json_items)
            result["feed_title"] = feed_title
            result["items"] = govcn_json_items
            return result
        raise FeedFetchError("JSON response did not contain parseable entries")
    if "html" in content_type and "xml" not in content_type:
        govcn_items = _parse_govcn_html_entries(raw_bytes, source_url=url, fetch_count=fetch_count)
        if govcn_items:
            feed_title = FEED_TITLE_OVERRIDES.get(url.strip()) or infer_feed_title_from_url(url)
            result["parse_ok"] = True
            result["entries_count"] = len(govcn_items)
            result["feed_title"] = feed_title
            result["items"] = govcn_items
            return result
        raise FeedFetchError(f"Unexpected content type: {content_type or 'unknown'}")

    sitemap_items = _parse_sitemap_entries(raw_bytes, source_url=url, fetch_count=fetch_count)
    if sitemap_items:
        feed_title = FEED_TITLE_OVERRIDES.get(url.strip()) or infer_feed_title_from_url(url)
        result["parse_ok"] = True
        result["entries_count"] = len(sitemap_items)
        result["feed_title"] = feed_title
        result["items"] = sitemap_items
        return result

    feed = feedparser.parse(raw_bytes)
    bozo_exc = getattr(feed, "bozo_exception", None) if getattr(feed, "bozo", 0) == 1 else None
    if bozo_exc is not None:
        logger.warning(f"Feed parse warning (bozo=1) for {url}: {bozo_exc}")

    entries = getattr(feed, "entries", [])
    if not entries and bozo_exc is not None:
        cleaned_bytes = _strip_invalid_xml_control_bytes(raw_bytes)
        if cleaned_bytes != raw_bytes:
            retry_feed = feedparser.parse(cleaned_bytes)
            retry_entries = getattr(retry_feed, "entries", [])
            if retry_entries:
                logger.info("Recovered feed after XML control-byte cleanup: %s", url)
                feed = retry_feed
                entries = retry_entries
                bozo_exc = getattr(feed, "bozo_exception", None) if getattr(feed, "bozo", 0) == 1 else None
            else:
                retry_exc = getattr(retry_feed, "bozo_exception", None)
                if retry_exc is not None:
                    bozo_exc = retry_exc

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
    elif not str(feed_title).strip() or str(feed_title).strip().lower() == "untitled feed":
        feed_title = infer_feed_title_from_url(url)
    result["feed_title"] = feed_title

    if not entries:
        if bozo_exc is not None:
            raise FeedFetchError(f"Parse error: {bozo_exc}")
        logger.info(f"No entries found for: {feed_title}")
        result["parse_ok"] = True
        result["entries_count"] = 0
        result["items"] = []
        return result

    if http_status is not None and http_status >= 400:
        raise FeedFetchError(f"HTTP {http_status}")

    items: list[dict] = []
    sorted_entries = sorted(entries, key=_entry_published_dt, reverse=True)
    for entry in sorted_entries[: max(1, int(fetch_count))]:
        title = str(getattr(entry, "title", "(no title)") or "(no title)")
        title = normalize_item_title(title)
        link = str(getattr(entry, "link", "") or "").strip()
        items.append({
            "title": title,
            "link": link,
            "normalized_url": normalize_url(link),
            "published_dt": _entry_published_dt(entry),
        })

    result["parse_ok"] = True
    result["entries_count"] = len(entries)
    result["items"] = items
    return result


def fetch_rss_entries(url: str, fetch_count: int = DEFAULT_FETCH_COUNT) -> tuple[str, list[dict]]:
    result = fetch_rss_entries_detailed(url, fetch_count=fetch_count)
    return str(result.get("feed_title", "") or infer_feed_title_from_url(url)), list(result.get("items", []))


def fetch_feed_with_failover(
    *,
    category_name: str,
    subgroup_name: str | None,
    feed_def: dict,
    fetch_count: int,
    failover_state: dict,
    switch_after_failures: int,
) -> tuple[dict, dict]:
    urls = [str(u).strip() for u in (feed_def.get("urls") or []) if isinstance(u, str) and str(u).strip()]
    if not urls:
        raise FeedFetchError("No feed URLs configured")

    feed_key = make_feed_runtime_key(category_name, subgroup_name, feed_def)
    feeds_state = failover_state.setdefault("feeds", {})
    state_entry = feeds_state.setdefault(feed_key, {})
    try:
        active_index = int(state_entry.get("active_index", 0))
    except Exception:
        active_index = 0
    if active_index < 0 or active_index >= len(urls):
        active_index = 0
    consecutive_failures = max(0, int(state_entry.get("consecutive_failures", 0) or 0))
    switch_after_failures = max(1, int(switch_after_failures))

    attempts: list[dict] = []
    tried_indices: set[int] = set()
    current_index = active_index
    allow_immediate_failover = True
    while current_index not in tried_indices and len(tried_indices) < len(urls):
        tried_indices.add(current_index)
        current_url = urls[current_index]
        try:
            fetch_result = fetch_rss_entries_detailed(current_url, fetch_count=fetch_count)
            state_entry["active_index"] = current_index
            state_entry["consecutive_failures"] = 0
            state_entry["primary_unhealthy"] = bool(current_index != 0)
            state_entry["last_success_at"] = datetime.now().isoformat(timespec="seconds")
            state_entry["last_success_url"] = current_url
            state_entry.pop("last_error", None)
            state_entry["configured_urls"] = urls
            fetch_result["feed_key"] = feed_key
            fetch_result["configured_urls"] = urls
            fetch_result["active_url_index"] = current_index
            fetch_result["attempts"] = attempts
            if feed_def.get("name") and not fetch_result.get("feed_title"):
                fetch_result["feed_title"] = str(feed_def.get("name"))
            return fetch_result, state_entry
        except Exception as e:
            attempts.append({
                "url": current_url,
                "error": f"{type(e).__name__}: {e}",
            })
            state_entry["last_error"] = attempts[-1]["error"]
            state_entry["last_error_at"] = datetime.now().isoformat(timespec="seconds")
            state_entry["last_error_url"] = current_url
            state_entry["configured_urls"] = urls
            if current_index == active_index:
                consecutive_failures += 1
                state_entry["consecutive_failures"] = consecutive_failures
                if consecutive_failures >= switch_after_failures and len(urls) > 1:
                    current_index = (current_index + 1) % len(urls)
                    state_entry["active_index"] = current_index
                    state_entry["consecutive_failures"] = 0
                    state_entry["primary_unhealthy"] = bool(current_index != 0)
                    state_entry["last_switch_at"] = datetime.now().isoformat(timespec="seconds")
                    state_entry["last_switch_reason"] = f"{consecutive_failures} consecutive failures"
                    if allow_immediate_failover:
                        allow_immediate_failover = False
                        continue
            state_entry["active_index"] = current_index
            state_entry["primary_unhealthy"] = bool(current_index != 0)
            raise

    raise FeedFetchError("No working feed URL after failover attempts")


def feed_items_to_markdown(
    feed_title: str,
    items: list[dict],
    heading_level: int = 2,
    section_note: str | None = None,
) -> str:
    heading_level = min(max(1, heading_level), 6)
    md = f"{'#' * heading_level} {feed_title}\n\n"
    if section_note:
        md += f"{MD_FEED_NOTE_PREFIX}{section_note}\n\n"
    if not items:
        return md
    for item in items:
        title = item.get("title", "(no title)")
        link = item.get("link", "")
        suffix = f" {MD_REPEAT_TOKEN}" if item.get("is_repeat") else f" {MD_FRESH_TOKEN}"
        if link:
            md += f"- [{title}]({link}){suffix}\n"
        else:
            md += f"- {title}{suffix}\n"
    md += "\n"
    return md

def md_to_simple_html(
    md_text: str,
    title: str = "Trend Agent Report",
    *,
    memory: dict | None = None,
    run_snapshot: dict | None = None,
) -> str:
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
        ".ta-memory{margin:0 0 14px;padding:10px 12px;border:1px solid #bfdbfe;border-radius:10px;background:linear-gradient(90deg,#eff6ff,#ffffff)}"
        ".ta-memory-title{margin:0 0 8px;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#1d4ed8}"
        ".ta-memory-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}"
        ".ta-memory-cell{padding:8px;border:1px solid #dbeafe;border-radius:8px;background:#fff}"
        ".ta-memory-k{display:block;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em}"
        ".ta-memory-v{display:block;font-size:16px;font-weight:700;color:#111827}"
        ".ta-health-healthy{color:#166534}"
        ".ta-health-degraded{color:#92400e}"
        ".ta-health-failed{color:#991b1b}"
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
        ".ta-subgroup{margin:8px 0 10px;padding:8px 10px;border-left:4px solid #fdba74;"
        "background:linear-gradient(90deg,#fffaf0,#fff);border-radius:8px;font-size:13px;"
        "font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#9a3412}"
        ".ta-feed{margin:8px 0;border:1px solid var(--line);border-radius:10px;background:#fafafa}"
        ".ta-feed-title{cursor:pointer;list-style:none;margin:0;padding:10px 12px;font-size:17px;font-weight:700;color:#1f2937}"
        ".ta-feed-title::-webkit-details-marker{display:none}"
        ".ta-feed-title::before{content:'> ';color:#64748b}"
        ".ta-feed[open] .ta-feed-title::before{content:'v '}"
        ".ta-feed-note{margin:0;padding:0 12px 8px;color:var(--muted);font-size:12px;line-height:1.4}"
        ".ta-fresh-badge{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;"
        "font-size:11px;font-weight:700;color:#166534;background:#dcfce7;border:1px solid #86efac;vertical-align:middle}"
        ".ta-repeat-badge{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;"
        "font-size:11px;font-weight:700;color:#92400e;background:#fef3c7;border:1px solid #fcd34d;vertical-align:middle}"
        ".ta-feed ul{margin:0 0 12px;padding:0 16px 0 32px}"
        "p{margin:10px 0}"
        "ul{margin:8px 0 14px;padding-left:20px}"
        "li{margin:6px 0}"
        "a{color:var(--link);text-decoration:none;word-break:break-word}"
        "a:hover{text-decoration:underline}"
        "@media (max-width:640px){.card{padding:16px}h1{font-size:24px}.ta-memory-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ta-category-title{font-size:12px;letter-spacing:.12em}.ta-feed-title{font-size:16px}}"
        "</style>"
    )
    out.append("</head><body>")
    out.append("<div class='wrap'><main class='card'>")
    out.append(
        f"<p class='meta'>Generated by TrendAgent | {html_lib.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</p>"
    )
    if isinstance(memory, dict):
        totals = memory.get("totals", {}) if isinstance(memory.get("totals"), dict) else {}
        streaks = memory.get("streaks", {}) if isinstance(memory.get("streaks"), dict) else {}
        health = memory.get("health", {}) if isinstance(memory.get("health"), dict) else {}
        health_state = str(health.get("state", "unknown") or "unknown")
        health_css = (
            "ta-health-healthy" if health_state == "healthy" else
            "ta-health-degraded" if health_state == "degraded" else
            "ta-health-failed" if health_state == "failed" else ""
        )
        feeds_failed_now = int((run_snapshot or {}).get("feeds_failed", 0) or 0)
        items_total_now = int((run_snapshot or {}).get("items_total", 0) or 0)
        out.append("<section class='ta-memory'>")
        out.append("<p class='ta-memory-title'>Agent Memory Dashboard</p>")
        out.append("<div class='ta-memory-grid'>")
        out.append(
            f"<div class='ta-memory-cell'><span class='ta-memory-k'>Health</span><span class='ta-memory-v {health_css}'>{html_lib.escape(health_state.upper())}</span></div>"
        )
        out.append(
            f"<div class='ta-memory-cell'><span class='ta-memory-k'>Total Runs</span><span class='ta-memory-v'>{int(totals.get('runs', 0) or 0)}</span></div>"
        )
        out.append(
            f"<div class='ta-memory-cell'><span class='ta-memory-k'>Success Streak</span><span class='ta-memory-v'>{int(streaks.get('success', 0) or 0)}</span></div>"
        )
        out.append(
            f"<div class='ta-memory-cell'><span class='ta-memory-k'>Failure Streak</span><span class='ta-memory-v'>{int(streaks.get('failure', 0) or 0)}</span></div>"
        )
        out.append(
            f"<div class='ta-memory-cell'><span class='ta-memory-k'>Successes</span><span class='ta-memory-v'>{int(totals.get('successes', 0) or 0)}</span></div>"
        )
        out.append(
            f"<div class='ta-memory-cell'><span class='ta-memory-k'>Failures</span><span class='ta-memory-v'>{int(totals.get('failures', 0) or 0)}</span></div>"
        )
        out.append(
            f"<div class='ta-memory-cell'><span class='ta-memory-k'>This Run Items</span><span class='ta-memory-v'>{items_total_now}</span></div>"
        )
        out.append(
            f"<div class='ta-memory-cell'><span class='ta-memory-k'>This Run Feed Errors</span><span class='ta-memory-v'>{feeds_failed_now}</span></div>"
        )
        out.append("</div></section>")

    in_ul = False
    in_feed = False
    in_category = False
    current_category_key = "general"
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    key_re = re.compile(r"[^a-z0-9]+")
    key_counts: dict[str, int] = {}

    def make_details_key(*parts: str) -> str:
        tokens = []
        for part in parts:
            token = key_re.sub("-", str(part).strip().lower()).strip("-")
            tokens.append(token or "section")
        base = ".".join(tokens)
        count = key_counts.get(base, 0) + 1
        key_counts[base] = count
        return base if count == 1 else f"{base}.{count}"

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
            current_category_key = make_details_key("cat", line[3:].strip())
            out.append(f"<details class='ta-category' data-key='{current_category_key}' open>")
            out.append(f"<summary class='ta-category-title'>{cat}</summary>")
            out.append("<div class='ta-category-body'>")
            in_category = True
            continue

        if line.startswith("### "):
            if not in_category:
                current_category_key = make_details_key("cat", "general")
                out.append(f"<details class='ta-category' data-key='{current_category_key}' open>")
                out.append("<summary class='ta-category-title'>General</summary>")
                out.append("<div class='ta-category-body'>")
                in_category = True
            close_feed()
            feed_name = line[4:].strip()
            feed = html_lib.escape(line[4:].strip())
            feed_key = make_details_key("feed", current_category_key, feed_name)
            out.append(f"<details class='ta-feed' data-key='{feed_key}' open>")
            out.append(f"<summary class='ta-feed-title'>{feed}</summary>")
            in_feed = True
            continue

        if line.startswith(MD_SUBGROUP_PREFIX):
            if in_category:
                close_feed()
                subgroup_name = html_lib.escape(line[len(MD_SUBGROUP_PREFIX):].strip())
                if subgroup_name:
                    out.append(f"<div class='ta-subgroup'>{subgroup_name}</div>")
                    continue

        if line.startswith("- "):
            if not in_feed and in_category:
                items_key = make_details_key("feed", current_category_key, "items")
                out.append(f"<details class='ta-feed' data-key='{items_key}' open>")
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
            item_html = item_html.replace(
                MD_FRESH_TOKEN,
                "<abbr class='ta-fresh-badge' title='New in the last freshness window'>NEW</abbr>",
            )
            item_html = item_html.replace(
                MD_REPEAT_TOKEN,
                "<abbr class='ta-repeat-badge' title='Previously shown within the freshness window'>PREV</abbr>",
            )
            out.append(f"<li>{item_html}</li>")
            continue

        if line.startswith(MD_FEED_NOTE_PREFIX):
            note_text = line[len(MD_FEED_NOTE_PREFIX):].strip()
            if in_feed and note_text:
                close_ul()
                out.append(f"<p class='ta-feed-note'>{html_lib.escape(note_text)}</p>")
                continue

        if stripped == "":
            close_ul()
            continue

        close_ul()
        out.append(f"<p>{html_lib.escape(line)}</p>")

    close_category()
    out.append(
        "<script>"
        "(function(){"
        "var storageKey='trendagent.foldState.v1';"
        "var state={};"
        "try{state=JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch(e){state={};}"
        "var save=function(){try{localStorage.setItem(storageKey,JSON.stringify(state));}catch(e){}};"
        "document.querySelectorAll('details[data-key]').forEach(function(el){"
        "var key=el.getAttribute('data-key');"
        "if(Object.prototype.hasOwnProperty.call(state,key)){el.open=!!state[key];}"
        "else{el.open=true;}"
        "el.addEventListener('toggle',function(){state[key]=el.open;save();});"
        "});"
        "})();"
        "</script>"
    )
    out.append("</main></div></body></html>")
    return "\n".join(out)


def main(start_telegram: bool = False, dev_mode: bool = False) -> int:
    if not RUN_TRIGGER_LOCK.acquire(blocking=False):
        logger.warning("Run requested while another run is active")
        return 2
    lock_fd = None
    try:
        lock_fd = _acquire_run_file_lock()
    except RuntimeError as exc:
        RUN_TRIGGER_LOCK.release()
        logger.warning(str(exc))
        return 2

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
        "dedupe_window_days": None,
    },
    "outputs": {
        "md_path": None,
        "html_path": None,
        "status_path": str(STATUS_PATH),
        "history_path": str(HISTORY_PATH),
        "history_urls_path": str(HISTORY_URLS_PATH),
        "agent_memory_path": str(AGENT_MEMORY_PATH),
        "ops_memory_path": str(OPS_MEMORY_PATH),
        "prefs_path": str(PREFS_PATH),
        "recall_db_path": str(RECALL_DB_PATH),
        "seen_items_db_path": str(SEEN_ITEMS_DB_PATH),
        "knowledge_prefs_path": str(KNOWLEDGE_PREFS_PATH),
        "knowledge_sources_path": str(KNOWLEDGE_SOURCES_PATH),
        "knowledge_rules_path": str(KNOWLEDGE_RULES_PATH),
        "json_dir": str(JSON_DIR),
        "data_dir": str(DATA_DIR),
        "memory_dir": str(MEMORY_DIR),
        "log_dir": str(LOG_DIR),
        "report_dir": str(REPORT_DIR),
        "html_report_dir": str(HTML_REPORT_DIR),
    },
    "metrics": {
        "items_total": 0,
        "items_new": 0,
        "items_duplicates": 0,
        "feeds_ok": 0,
        "feeds_failed": 0,
    },
    "dedupe": {
        "lookback_days": HISTORY_DEDUPE_LOOKBACK_DAYS,
        "categories": {},
    },
    "events": []   # 你以后可以把关键步骤写进这里（可选）
    }

    write_status(status)
    runtime_config = _load_runtime_config()
    runtime_secrets = _load_runtime_secrets()
    if runtime_config is None or runtime_secrets is None:
        _release_run_file_lock(lock_fd)
        RUN_TRIGGER_LOCK.release()
        return 1

    global TELEGRAM_THREAD
    stay_alive = bool(runtime_config.get("telegram_stay_alive", True))
    if stay_alive:
        logger.info("telegram_stay_alive enabled")
    if start_telegram and TELEGRAM_THREAD is None:
        token = str(runtime_secrets.get("telegram_bot_token") or "").strip()
        if token:
            try:
                TELEGRAM_THREAD = start_telegram_polling(token, handle_telegram_message, logger=logger)
                logger.info("Telegram polling started")
            except Exception:
                logger.exception("Failed to start Telegram polling; continuing without Telegram")
        else:
            logger.info("telegram_bot_token not set; Telegram polling disabled")
    
    agent_memory_path = AGENT_MEMORY_PATH
    ops_memory_path = OPS_MEMORY_PATH
    recall_conn = None
    recall_enabled = False
    prefs: dict = {}
    feed_failure_rollup: dict[str, dict] = {}

    logger.info("========== Trend Agent RUN START ==========")
    logger.info(f"Run ID: {run_id}")
    logger.info(f"Working directory: {BASE_DIR}")

    try:
        ensure_knowledge_files()
        init_recall_db(SEEN_ITEMS_DB_PATH)
        prefs = load_prefs(PREFS_PATH)
        logger.info("Loaded prefs: %s", PREFS_PATH)
        try:
            recall_conn = init_recall_db2(RECALL_DB_PATH)
            recall_enabled = True
            logger.info("Recall DB enabled: %s", RECALL_DB_PATH)
        except Exception:
            recall_enabled = False
            recall_conn = None
            logger.exception("Recall DB init failed; continuing without recall memory")

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
        configured_feeds = [
            feed
            for group in rss_groups
            for subgroup in group.get("subgroups", [])
            for feed in subgroup.get("feeds", [])
        ]
        max_per_feed = int(config.get("max_per_feed", DEFAULT_DISPLAY_COUNT))
        fetch_count = max(DEFAULT_FETCH_COUNT, int(config.get("fetch_count_per_feed", DEFAULT_FETCH_COUNT)))
        history_window_days = int(config.get("freshness_window_days", HISTORY_DEDUPE_LOOKBACK_DAYS))
        prefs_window = prefs.get("dedupe_window_days")
        if isinstance(prefs_window, int) and prefs_window > 0:
            history_window_days = int(prefs_window)
        failover_switch_after_failures = int(config.get("feed_failover_switch_after_failures", DEFAULT_FAILOVER_SWITCH_AFTER_FAILURES))
        failover_state_path_cfg = config.get("feed_failover_state_path")
        failover_state_path = (
            Path(failover_state_path_cfg)
            if isinstance(failover_state_path_cfg, str) and failover_state_path_cfg.strip()
            else FEED_FAILOVER_STATE_PATH
        )
        if not failover_state_path.is_absolute():
            failover_state_path = BASE_DIR / failover_state_path
        failover_state_path.parent.mkdir(parents=True, exist_ok=True)
        failover_state = load_feed_failover_state(failover_state_path)
        history_urls_path_cfg = config.get("history_urls_path")
        history_urls_path = Path(history_urls_path_cfg) if isinstance(history_urls_path_cfg, str) and history_urls_path_cfg.strip() else HISTORY_URLS_PATH
        if not history_urls_path.is_absolute():
            history_urls_path = BASE_DIR / history_urls_path
        history_urls_path.parent.mkdir(parents=True, exist_ok=True)
        agent_memory_path_cfg = config.get("agent_memory_path")
        agent_memory_path = Path(agent_memory_path_cfg) if isinstance(agent_memory_path_cfg, str) and agent_memory_path_cfg.strip() else AGENT_MEMORY_PATH
        if not agent_memory_path.is_absolute():
            agent_memory_path = BASE_DIR / agent_memory_path
        agent_memory_path.parent.mkdir(parents=True, exist_ok=True)
        ops_memory = load_ops_memory(ops_memory_path)
        status["agent"]["memory"] = {
            "agent_id": ops_memory.get("agent_id"),
            "totals": ops_memory.get("totals", {}),
            "streaks": ops_memory.get("streaks", {}),
            "health": ops_memory.get("health", {}),
        }
        status["outputs"]["history_urls_path"] = str(history_urls_path)
        status["outputs"]["feed_failover_state_path"] = str(failover_state_path)
        status["outputs"]["agent_memory_path"] = str(agent_memory_path)
        status["outputs"]["ops_memory_path"] = str(ops_memory_path)
        status["outputs"]["prefs_path"] = str(PREFS_PATH)
        status["outputs"]["recall_db_path"] = str(RECALL_DB_PATH)
        status["inputs"]["dedupe_window_days"] = history_window_days

        logger.info(f"Loaded {len(configured_feeds)} feed definitions, max_per_feed={max_per_feed}")
        status["inputs"]["max_per_feed"] = max_per_feed
        status["inputs"]["rss_url_count"] = len(configured_feeds)
        status["dedupe"]["lookback_days"] = history_window_days
        today_key = datetime.now().date()
        history_urls_store = prune_history_urls_store(
            load_history_urls_store(history_urls_path),
            today=today_key,
            window_days=history_window_days,
        )
        seen_urls_by_section = build_seen_urls_by_section(
            history_urls_store,
            today=today_key,
            window_days=history_window_days,
        )
        legacy_seen_urls_by_section = load_recent_seen_urls_by_section_from_history(history_window_days)
        for section_key, urls in legacy_seen_urls_by_section.items():
            seen_urls_by_section.setdefault(section_key, set()).update(urls)
        todays_history_sections: dict[str, list[str]] = {}

        report = "# Trend Agent Report\n\n"
        for group in rss_groups:
            category_name = str(group.get("category", "general"))
            category_subgroups = group.get("subgroups", [])
            category_block = ""
            category_has_visible_content = False
            category_unique_urls: set[str] = set()
            category_has_feed_errors = False

            pretty_category = category_name.replace("_", " ").strip().title()
            category_key = normalize_category_key(category_name)
            feed_heading_level = 3
            error_heading = "###"

            has_named_subgroups = any(bool(sg.get("name")) for sg in category_subgroups)
            for subgroup in category_subgroups:
                subgroup_name = subgroup.get("name")
                subgroup_block = ""
                subgroup_has_content = False

                if has_named_subgroups and subgroup_name:
                    subgroup_block += f"{MD_SUBGROUP_PREFIX}{str(subgroup_name).strip()}\n\n"

                subgroup_feeds = subgroup.get("feeds", [])
                for feed_def in subgroup_feeds:
                    feed_urls = [str(u).strip() for u in (feed_def.get("urls") or []) if isinstance(u, str) and str(u).strip()]
                    primary_url = feed_urls[0] if feed_urls else ""
                    try:
                        fetch_result, failover_entry = fetch_feed_with_failover(
                            category_name=category_name,
                            subgroup_name=str(subgroup_name).strip() if subgroup_name else None,
                            feed_def=feed_def,
                            fetch_count=fetch_count,
                            failover_state=failover_state,
                            switch_after_failures=failover_switch_after_failures,
                        )
                        feed_title = resolve_display_feed_title(
                            feed_def.get("name"),
                            fetch_result.get("feed_title"),
                            primary_url,
                        )
                        feed_items = list(fetch_result.get("items", []))
                        feed_source = feed_title
                        deduped_items: list[dict] = []
                        recall_seen_urls: set[str] = set()
                        duplicate_count = 0
                        for item in feed_items:
                            raw_link = str(item.get("link", "") or "")
                            canonical_url = canonicalize_url(raw_link or str(item.get("normalized_url", "") or ""))
                            published_dt = item.get("published_dt")
                            published_at = (
                                published_dt.isoformat(timespec="seconds")
                                if isinstance(published_dt, datetime) and published_dt != datetime.min
                                else ""
                            )
                            item_id = make_item_id({
                                "canonical_url": canonical_url,
                                "source": feed_source,
                                "title": item.get("title", ""),
                                "published_at": published_at,
                            })
                            item_copy = dict(item)
                            item_copy["item_id"] = item_id
                            item_copy["canonical_url"] = canonical_url

                            if recall_enabled and recall_conn is not None:
                                try:
                                    if has_seen(recall_conn, item_id):
                                        duplicate_count += 1
                                        if canonical_url:
                                            recall_seen_urls.add(canonical_url)
                                        deduped_items.append(item_copy)
                                        continue
                                    mark_seen(
                                        recall_conn,
                                        {
                                            "item_id": item_id,
                                            "source": feed_source,
                                            "url": canonical_url or raw_link,
                                            "title": str(item.get("title", "") or ""),
                                            "published_at": published_at,
                                        },
                                    )
                                except Exception:
                                    logger.exception(
                                        "Recall dedupe failed for feed %s; proceeding without recall for this item",
                                        feed_source,
                                    )
                                    deduped_items.append(item_copy)
                                    continue
                            deduped_items.append(item_copy)
                        if recall_enabled and recall_conn is not None:
                            try:
                                recall_commit(recall_conn)
                            except Exception:
                                logger.exception("Recall commit failed; continuing run")
                        status["metrics"]["items_duplicates"] += duplicate_count
                        section_key = normalize_section_key(category_name, feed_title)
                        seen_for_section = set(seen_urls_by_section.get(section_key, set()))
                        effective_seen_urls = (
                            recall_seen_urls
                            if recall_enabled
                            else seen_for_section
                        )
                        selected_items, section_note = select_feed_items(
                            deduped_items,
                            effective_seen_urls,
                            display_count=max_per_feed,
                            freshness_window_days=history_window_days,
                        )
                        feed_md = feed_items_to_markdown(
                            feed_title,
                            selected_items,
                            heading_level=feed_heading_level,
                            section_note=section_note,
                        )
                        item_count = len(selected_items)
                        selected_norm_urls = [
                            norm for norm in (str(item.get("normalized_url", "") or "") for item in selected_items) if norm
                        ]
                        todays_history_sections[section_key] = selected_norm_urls
                        for norm in selected_norm_urls:
                            category_unique_urls.add(norm)
                        if feed_md:
                            subgroup_block += feed_md
                            subgroup_has_content = True
                        status["metrics"]["feeds_ok"] += 1
                        status["metrics"]["items_total"] += item_count
                        status["metrics"]["items_new"] += item_count
                        status["dedupe"]["categories"][section_key] = {
                            "category": category_key,
                            "subgroup": str(subgroup_name).strip() if subgroup_name else None,
                            "section": feed_title,
                            "feed_key": fetch_result.get("feed_key"),
                            "request_url": fetch_result.get("request_url"),
                            "final_url": fetch_result.get("final_url"),
                            "http_status": fetch_result.get("http_status"),
                            "active_url_index": fetch_result.get("active_url_index"),
                            "configured_urls": fetch_result.get("configured_urls"),
                            "display_count": max_per_feed,
                            "fetched_count": len(feed_items),
                            "entries_count": int(fetch_result.get("entries_count", len(feed_items)) or 0),
                            "selected_count": item_count,
                            "fresh_count": sum(1 for item in selected_items if not item.get("is_repeat")),
                            "repeat_count": sum(1 for item in selected_items if item.get("is_repeat")),
                            "normalized_urls": selected_norm_urls,
                        }
                    except Exception as feed_err:
                        status["metrics"]["feeds_failed"] += 1
                        logger.exception(f"Failed to fetch RSS: {primary_url}")
                        error_title = resolve_display_feed_title(
                            feed_def.get("name"),
                            None,
                            primary_url or "",
                        )
                        error_note = f"Feed error: {type(feed_err).__name__}: {feed_err}"
                        error_md = feed_items_to_markdown(
                            error_title or "Feed",
                            [],
                            heading_level=feed_heading_level,
                            section_note=error_note,
                        )
                        status["events"].append({
                            "type": "feed_error",
                            "url": primary_url,
                            "feed": error_title,
                            "error": f"{type(feed_err).__name__}: {feed_err}",
                        })
                        feed_key = make_feed_runtime_key(
                            category_name=category_name,
                            subgroup_name=str(subgroup_name).strip() if subgroup_name else None,
                            feed_def=feed_def,
                        )
                        reason = f"{type(feed_err).__name__}: {feed_err}"
                        if recall_enabled and recall_conn is not None:
                            try:
                                record_feed_failure(
                                    recall_conn,
                                    feed_key=feed_key,
                                    source=error_title or "Feed",
                                    reason=reason,
                                    failed_at=datetime.now().isoformat(timespec="seconds"),
                                )
                                recall_commit(recall_conn)
                            except Exception:
                                logger.exception("Failed to record feed failure in recall DB; continuing")
                        rollup = feed_failure_rollup.setdefault(
                            feed_key,
                            {"count": 0, "last_reason": None, "last_failed_at": None},
                        )
                        rollup["count"] = int(rollup.get("count", 0) or 0) + 1
                        rollup["last_reason"] = reason
                        rollup["last_failed_at"] = datetime.now().isoformat(timespec="seconds")
                        subgroup_block += error_md or f"{error_heading} {error_title or 'Feed'}\n\n{MD_FEED_NOTE_PREFIX}{error_note}\n\n"
                        subgroup_has_content = True
                        category_has_feed_errors = True

                if subgroup_has_content:
                    category_block += subgroup_block
                    category_has_visible_content = True

            if not category_unique_urls and not category_has_visible_content:
                category_block = f"_No new items in the last {history_window_days} days._\n\n"
                category_has_visible_content = True

            header_note = (
                f" (No new items in last {history_window_days} days)"
                if (not category_unique_urls and not category_has_feed_errors)
                else ""
            )
            if category_has_visible_content:
                category_header = f"## {pretty_category}{header_note}\n\n"
                report += category_header + category_block

        filename = f"trend_report_{run_id}.md"
        output_path = REPORT_DIR / filename
        output_path.write_text(report, encoding="utf-8")
        output_html = HTML_REPORT_DIR / f"trend_report_{run_id}.html"
        html_report = md_to_simple_html(
            report,
            title="Trend Agent Report",
            memory=status["agent"].get("memory"),
            run_snapshot=status.get("metrics"),
        )
        output_html.write_text(html_report, encoding="utf-8")

        history_urls_store = prune_history_urls_store(
            history_urls_store,
            today=today_key,
            window_days=history_window_days,
        )
        existing_today = history_urls_store.get(today_key.isoformat(), {})
        if not isinstance(existing_today, dict):
            existing_today = {}
        merged_today = dict(existing_today)
        for k, v in todays_history_sections.items():
            merged_today[k] = sorted(set(v))
        history_urls_store[today_key.isoformat()] = merged_today
        history_urls_store = prune_history_urls_store(
            history_urls_store,
            today=today_key,
            window_days=history_window_days,
        )
        save_history_urls_store(history_urls_path, history_urls_store)
        save_feed_failover_state(failover_state_path, failover_state)

        logger.info(f"Saved report: {output_path}")
        logger.info(f"Saved HTML: {output_html}")
        status["outputs"]["md_path"] = str(output_path)
        status["outputs"]["html_path"] = str(output_html)

        if dev_mode:
            logger.info("DEV mode enabled; skipping external deliveries")
            delivery_results = {"email_sent": False, "discord_sent": False}
        else:
            delivery_results = deliver_to_all(
                run_id,
                output_html,
                html_report,
                report,
                status,
                config,
            )
        status["outputs"].update(delivery_results)

        # Open the generated HTML report in the default browser (non-fatal)
        if dev_mode:
            logger.info("DEV mode enabled; skipping browser open")
        elif not bool(runtime_config.get("open_browser", True)):
            logger.info("open_browser disabled in config; skipping browser open")
        else:
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
        ops_memory = update_ops_after_run(
            load_ops_memory(ops_memory_path),
            {
                "id": status["run"]["id"],
                "state": status["run"]["state"],
                "started_at": status["run"]["started_at"],
                "finished_at": status["run"]["finished_at"],
                "duration_seconds": status["run"]["duration_seconds"],
                "feeds_ok": status["metrics"]["feeds_ok"],
                "feeds_failed": status["metrics"]["feeds_failed"],
                "items_new": status["metrics"]["items_new"],
                "items_duplicates": status["metrics"]["items_duplicates"],
                "error": status["run"]["error"],
            },
            feed_failure_rollup,
        )
        save_ops_memory_atomic(ops_memory_path, ops_memory)
        status["agent"]["memory"] = {
            "agent_id": ops_memory.get("agent_id"),
            "totals": ops_memory.get("totals", {}),
            "streaks": ops_memory.get("streaks", {}),
            "health": ops_memory.get("health", {}),
        }
        try:
            run_files = persist_run_memory_files(status)
            status["outputs"].update(run_files)
        except Exception:
            logger.exception("persist_run_memory_files failed (non-fatal)")
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
        ops_memory = update_ops_after_run(
            load_ops_memory(ops_memory_path),
            {
                "id": status["run"]["id"],
                "state": status["run"]["state"],
                "started_at": status["run"]["started_at"],
                "finished_at": status["run"]["finished_at"],
                "duration_seconds": status["run"]["duration_seconds"],
                "feeds_ok": status["metrics"]["feeds_ok"],
                "feeds_failed": status["metrics"]["feeds_failed"],
                "items_new": status["metrics"]["items_new"],
                "items_duplicates": status["metrics"]["items_duplicates"],
                "error": status["run"]["error"],
            },
            feed_failure_rollup,
        )
        save_ops_memory_atomic(ops_memory_path, ops_memory)
        status["agent"]["memory"] = {
            "agent_id": ops_memory.get("agent_id"),
            "totals": ops_memory.get("totals", {}),
            "streaks": ops_memory.get("streaks", {}),
            "health": ops_memory.get("health", {}),
        }
        try:
            run_files = persist_run_memory_files(status)
            status["outputs"].update(run_files)
        except Exception:
            logger.exception("persist_run_memory_files failed (non-fatal)")
        write_status(status)
        try:
            append_history(status)
        except Exception:
            logger.exception("append_history failed (non-fatal)")

        logger.error("========== Trend Agent RUN FAILED ==========")
        logger.error(err_text)
        logger.error(tb)
    finally:
        _release_run_file_lock(lock_fd)
        RUN_TRIGGER_LOCK.release()
        if recall_enabled and recall_conn is not None:
            try:
                recall_commit(recall_conn)
            except Exception:
                logger.exception("recall commit failed during shutdown; ignoring")
            recall_close(recall_conn)
    return 1
      
def write_json_atomic(path: Path, data):
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
    "dedupe": status.get("dedupe", {}),
    "agent_memory": status.get("agent", {}).get("memory"),
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

def run_telegram_mode() -> int:
    runtime_secrets = _load_runtime_secrets()
    if runtime_secrets is None:
        return 1
    token = str(runtime_secrets.get("telegram_bot_token") or "").strip()
    if not token:
        logger.error("telegram_bot_token not set; Telegram polling disabled")
        return 1
    try:
        with acquire_lock("telegram"):
            reset_health_state()
            preload_fast_voice_model(logger=logger)
            logger.info("Telegram polling started")
            run_telegram_forever(token=token, message_handler=handle_telegram_message, logger=logger)
    except KeyboardInterrupt:
        logger.info("Telegram stay-alive interrupted by user; shutting down cleanly")
    except TelegramConflictError:
        logger.error("Telegram polling stopped due to getUpdates conflict. Exiting.")
        return 1
    except RuntimeAlreadyRunning as exc:
        logger.info("Telegram already running (pid=%s). Exiting.", exc.pid)
    except Exception:
        logger.exception("Failed to run Telegram polling")
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TrendAgent runner")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--telegram", action="store_true", help="Start Telegram polling only and keep it alive")
    mode.add_argument("--once", action="store_true", help="Run the full report pipeline once and exit")
    mode.add_argument("--dev", action="store_true", help="Run once locally without external deliveries or browser open")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.telegram:
        raise SystemExit(run_telegram_mode())
    if args.dev:
        raise SystemExit(main(start_telegram=False, dev_mode=True))
    if args.once:
        raise SystemExit(main(start_telegram=False))
    raise SystemExit(main(start_telegram=False))

