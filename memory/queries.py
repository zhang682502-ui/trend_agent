from __future__ import annotations

import sqlite3


def get_new_items_since(conn: sqlite3.Connection, ts: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT item_id, source, url, title, published_at, first_seen_at, last_seen_at, seen_count
        FROM seen_items
        WHERE first_seen_at >= ?
        ORDER BY first_seen_at DESC
        """,
        (ts,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_most_frequent_sources(conn: sqlite3.Connection, n: int, days: int) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT source, COUNT(*) AS items
        FROM seen_items
        WHERE last_seen_at >= datetime('now', ?)
        GROUP BY source
        ORDER BY items DESC
        LIMIT ?
        """,
        (f"-{int(days)} day", int(n)),
    ).fetchall()
    return [dict(row) for row in rows]


def get_items_seen_count_gt(conn: sqlite3.Connection, k: int, days: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT item_id, source, url, title, published_at, first_seen_at, last_seen_at, seen_count
        FROM seen_items
        WHERE seen_count > ?
          AND last_seen_at >= datetime('now', ?)
        ORDER BY seen_count DESC, last_seen_at DESC
        """,
        (int(k), f"-{int(days)} day"),
    ).fetchall()
    return [dict(row) for row in rows]


def get_flaky_feeds(conn: sqlite3.Connection, n: int, days: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT feed_key, source, COUNT(*) AS failures, MAX(failed_at) AS last_failed_at
        FROM feed_failures
        WHERE failed_at >= datetime('now', ?)
        GROUP BY feed_key, source
        ORDER BY failures DESC, last_failed_at DESC
        LIMIT ?
        """,
        (f"-{int(days)} day", int(n)),
    ).fetchall()
    return [dict(row) for row in rows]

