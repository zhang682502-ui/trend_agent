from pathlib import Path
import json
from datetime import datetime
import traceback
import re
import html as html_lib
import logging
from logging.handlers import RotatingFileHandler
import feedparser


BASE_DIR = Path(__file__).resolve().parent

STATUS_PATH  = BASE_DIR / "status.json"
HISTORY_PATH = BASE_DIR / "history.json"
LOG_DIR      = BASE_DIR / "logs"              
LOG_DIR.mkdir(exist_ok=True)

STATUS_PATH = BASE_DIR / "status.json"
CONFIG_PATH = BASE_DIR / "config.json"


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


def fetch_rss(url: str, max_per_feed: int) -> tuple[str, int]:
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

    md = ""
    md += f"## {feed_title}\n\n"

    entries = getattr(feed, "entries", [])
    if not entries:
        logger.info(f"No entries found for: {feed_title}")
        md += "- (No entries)\n\n"
        return md, 0

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
    """
    极简 markdown -> HTML
    支持：
      - # / ## 标题
      - - 列表
      - [text](url) 链接
    """
    lines = md_text.splitlines()
    out = []
    out.append("<!doctype html><html><head><meta charset='utf-8'>")
    out.append(f"<title>{html_lib.escape(title)}</title>")
    out.append(
        "<style>"
        "body{font-family:system-ui,Segoe UI,Arial;max-width:900px;margin:24px auto;padding:0 16px;line-height:1.5}"
        "a{word-break:break-all}"
        "</style>"
    )
    out.append("</head><body>")

    in_ul = False
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    for raw in lines:
        line = raw.rstrip()

        if line.startswith("# "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h1>{html_lib.escape(line[2:].strip())}</h1>")
            continue

        if line.startswith("## "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h2>{html_lib.escape(line[3:].strip())}</h2>")
            continue

        if line.startswith("- "):
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

        # 空行：结束列表
        if line.strip() == "":
            if in_ul:
                out.append("</ul>")
                in_ul = False
            continue

        # 普通段落
        if in_ul:
            out.append("</ul>")
            in_ul = False
        out.append(f"<p>{html_lib.escape(line)}</p>")

    if in_ul:
        out.append("</ul>")

    out.append("</body></html>")
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
        "log_dir": str(LOG_DIR),
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

        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        rss_urls = config["rss_urls"]
        max_per_feed = config.get("max_per_feed", 5)

        logger.info(f"Loaded {len(rss_urls)} RSS URLs, max_per_feed={max_per_feed}")
        status["inputs"]["max_per_feed"] = max_per_feed
        status["inputs"]["rss_url_count"] = len(rss_urls)

        report = "# Trend Agent Report\n\n"
        for url in rss_urls:
            try:
                feed_md, item_count = fetch_rss(url, max_per_feed)
                report += feed_md
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
                report += f"## Feed Error\n\n- Failed to fetch: {url}\n\n"

        filename = f"trend_report_{run_id}.md"
        output_path = BASE_DIR / filename
        output_path.write_text(report, encoding="utf-8")
        output_html = output_path.with_suffix(".html")
        output_html.write_text(md_to_simple_html(report, title="Trend Agent Report"),encoding="utf-8",)

        logger.info(f"Saved report: {output_path}")
        logger.info(f"Saved HTML: {output_html}")
        status["outputs"]["md_path"] = str(output_path)
        status["outputs"]["html_path"] = str(output_html)

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
