from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("trend_agent")


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_items(
          item_id TEXT PRIMARY KEY,
          source TEXT,
          url TEXT,
          title TEXT,
          published_at TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          seen_count INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seen_items_source ON seen_items(source)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seen_items_last_seen_at ON seen_items(last_seen_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feed_failures(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          feed_key TEXT,
          source TEXT,
          reason TEXT,
          failed_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feed_failures_feed_key ON feed_failures(feed_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feed_failures_failed_at ON feed_failures(failed_at)"
    )
    conn.commit()
    logger.info("recall db initialized: %s", path)
    return conn


def has_seen(conn: sqlite3.Connection, item_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_items WHERE item_id = ? LIMIT 1",
        (item_id,),
    ).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, item: dict) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    item_id = str(item.get("item_id") or "")
    if not item_id:
        return
    row = conn.execute(
        "SELECT seen_count, first_seen_at FROM seen_items WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO seen_items(
              item_id, source, url, title, published_at, first_seen_at, last_seen_at, seen_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                item_id,
                str(item.get("source") or ""),
                str(item.get("url") or ""),
                str(item.get("title") or ""),
                str(item.get("published_at") or ""),
                now,
                now,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE seen_items
            SET source = ?,
                url = ?,
                title = ?,
                published_at = ?,
                last_seen_at = ?,
                seen_count = COALESCE(seen_count, 0) + 1
            WHERE item_id = ?
            """,
            (
                str(item.get("source") or ""),
                str(item.get("url") or ""),
                str(item.get("title") or ""),
                str(item.get("published_at") or ""),
                now,
                item_id,
            ),
        )


def record_feed_failure(
    conn: sqlite3.Connection,
    feed_key: str,
    source: str,
    reason: str,
    failed_at: str | None = None,
) -> None:
    ts = failed_at or datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO feed_failures(feed_key, source, reason, failed_at) VALUES (?, ?, ?, ?)",
        (feed_key, source, reason, ts),
    )


def commit(conn: sqlite3.Connection) -> None:
    conn.commit()


def close(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass

