from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

import feedparser

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import main as trend_main


def _load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_config_feed_urls(config: dict):
    groups = trend_main.collect_rss_groups(config)
    for group in groups:
        category = str(group.get("category", "general"))
        for subgroup in group.get("subgroups", []):
            subgroup_name = subgroup.get("name")
            for feed_def in subgroup.get("feeds", []):
                urls = [u for u in (feed_def.get("urls") or []) if isinstance(u, str) and u.strip()]
                for idx, url in enumerate(urls):
                    yield {
                        "category": category,
                        "subgroup": subgroup_name,
                        "feed_id": feed_def.get("id"),
                        "feed_name": feed_def.get("name"),
                        "candidate_index": idx,
                        "url": url.strip(),
                    }


def _classify_url(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    result = {
        "request_url": url,
        "final_url": None,
        "http_status": None,
        "content_type": None,
        "classification": None,
        "entry_count": 0,
        "newest_published_date": None,
        "error": None,
        "bozo_exception": None,
        "response_head": None,
    }
    try:
        parsed_result = trend_main.fetch_rss_entries_detailed(url, fetch_count=30)
        items = list(parsed_result.get("items", []))
        if items:
            newest_dt = None
            for item in items:
                dt = item.get("published_dt")
                if isinstance(dt, trend_main.datetime) and dt != trend_main.datetime.min:
                    if newest_dt is None or dt > newest_dt:
                        newest_dt = dt
            result["classification"] = "OK"
            result["entry_count"] = len(items)
            result["newest_published_date"] = newest_dt.isoformat(timespec="seconds") if newest_dt else None
            result["http_status"] = parsed_result.get("http_status")
            result["final_url"] = parsed_result.get("final_url")
            result["content_type"] = parsed_result.get("content_type")
            return result
    except Exception:
        pass

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_bytes = resp.read()
            status = getattr(resp, "status", None) or resp.getcode()
            content_type = str(resp.headers.get("Content-Type", "") or "")
            final_url = str(getattr(resp, "geturl", lambda: url)() or url)
        result["final_url"] = final_url
        result["http_status"] = status
        result["content_type"] = content_type
        if status != 200:
            msg = f"HTTP {status}"
            result["error"] = msg
            if status == 404:
                result["classification"] = "DEAD(404)"
            elif status == 403:
                result["classification"] = "BLOCKED(403)"
            else:
                result["classification"] = "ERROR"
            return result
    except urllib.error.HTTPError as e:
        msg = f"HTTP {e.code} {e.reason}"
        result["error"] = msg
        result["http_status"] = e.code
        result["final_url"] = str(getattr(e, "geturl", lambda: url)() or url)
        if "HTTP 404" in msg:
            result["classification"] = "DEAD(404)"
        elif "HTTP 403" in msg:
            result["classification"] = "BLOCKED(403)"
        else:
            result["classification"] = "ERROR"
        return result
    except Exception as e:  # pragma: no cover - defensive
        result["error"] = f"{type(e).__name__}: {e}"
        result["classification"] = "ERROR"
        return result

    head = raw_bytes[:200].decode("utf-8", errors="replace")
    fp = feedparser.parse(raw_bytes)
    entries = getattr(fp, "entries", []) or []
    bozo_exc = getattr(fp, "bozo_exception", None) if getattr(fp, "bozo", 0) == 1 else None
    if bozo_exc is not None:
        result["classification"] = "PARSE_ERROR"
        result["bozo_exception"] = str(bozo_exc)
        result["error"] = f"Parse error: {bozo_exc}"
        result["response_head"] = head
        return result

    newest_dt = None
    for entry in entries:
        dt = trend_main._entry_published_dt(entry)  # noqa: SLF001
        if dt and dt != trend_main.datetime.min and (newest_dt is None or dt > newest_dt):
            newest_dt = dt

    if entries:
        result["classification"] = "OK"
        result["entry_count"] = len(entries)
        result["newest_published_date"] = newest_dt.isoformat(timespec="seconds") if newest_dt else None
        return result

    # Allow sitemap-based XML feeds (White House/Reuters) as OK if our sitemap parser can extract items.
    sitemap_items = trend_main._parse_sitemap_entries(raw_bytes, source_url=url, fetch_count=30)  # noqa: SLF001
    if sitemap_items:
        result["classification"] = "OK"
        result["entry_count"] = len(sitemap_items)
        newest_sitemap_dt = None
        for item in sitemap_items:
            dt = item.get("published_dt")
            if isinstance(dt, trend_main.datetime) and dt != trend_main.datetime.min:
                if newest_sitemap_dt is None or dt > newest_sitemap_dt:
                    newest_sitemap_dt = dt
        result["newest_published_date"] = (
            newest_sitemap_dt.isoformat(timespec="seconds") if newest_sitemap_dt else None
        )
        return result

    result["classification"] = "PARSE_ERROR"
    result["error"] = "No entries parsed"
    result["response_head"] = head
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate all feed URLs in config.json")
    parser.add_argument("--config", default=str(Path("Json") / "config.json"))
    parser.add_argument("--output", default=str(Path("Json") / "feed_health.json"))
    args = parser.parse_args()

    config_path = Path(args.config)
    output_path = Path(args.output)

    config = _load_config(config_path)
    rows = []
    for row in _iter_config_feed_urls(config):
        result = _classify_url(row["url"])
        rows.append({**row, **result})

    summary = {}
    for row in rows:
        summary[row["classification"]] = summary.get(row["classification"], 0) + 1

    report = {
        "config_path": str(config_path.resolve()),
        "checked_at": trend_main.datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "results": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {output_path} ({len(rows)} URLs)")
    print(f"{'CLASS':<13} {'HTTP':<5} {'CAT':<10} {'SUBGROUP':<18} {'FEED':<20} URL")
    print("-" * 120)
    for row in rows:
        label = str(row["classification"])
        cat = str(row.get("category") or "-")
        sub = str(row.get("subgroup") or "-")
        name = str(row.get("feed_name") or row.get("feed_id") or "(unnamed)")
        print(
            f"{label:<13} {str(row.get('http_status') or '-'): <5} "
            f"{cat[:10]:<10} {sub[:18]:<18} {name[:20]:<20} {row['request_url']}"
        )
        if row.get("error"):
            print(f"  reason={row['error']}")
        if row.get("response_head"):
            print(f"  head={row['response_head']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
