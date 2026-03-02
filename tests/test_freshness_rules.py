import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import main


def _item(idx: int, *, days_ago: int = 0, repeat: bool = False) -> dict:
    base = "https://example.com/repeat" if repeat else f"https://example.com/item-{idx}"
    return {
        "title": f"Item {idx}",
        "link": base,
        "normalized_url": main.normalize_url(base),
        "published_dt": datetime(2026, 2, 26, 12, 0, 0) - timedelta(hours=idx, days=days_ago),
    }


class NormalizeUrlTests(unittest.TestCase):
    def test_normalize_url_strips_fragment_and_tracking_params(self):
        raw = (
            "https://Example.com/path/?b=2&utm_source=rss&ref=abc&a=1"
            "&gclid=xyz#section"
        )
        normalized = main.normalize_url(raw)
        self.assertEqual(normalized, "https://example.com/path?a=1&b=2")


class SelectionTests(unittest.TestCase):
    def test_case_a_three_or_more_fresh(self):
        items = [_item(1), _item(2), _item(3), _item(4)]
        selected, note = main.select_feed_items(items, set(), display_count=3)
        self.assertEqual(len(selected), 3)
        self.assertTrue(all(not x.get("is_repeat") for x in selected))
        self.assertIsNone(note)

    def test_case_b_one_fresh_two_repeats(self):
        fresh = _item(1)
        rep2 = _item(2, repeat=True)
        rep3 = _item(3, repeat=True)
        rep4 = _item(4, repeat=True)
        items = [rep4, rep3, fresh, rep2]
        seen = {rep2["normalized_url"], rep3["normalized_url"], rep4["normalized_url"]}
        selected, note = main.select_feed_items(items, seen, display_count=3)
        self.assertEqual(len(selected), 3)
        self.assertFalse(selected[0]["is_repeat"])
        self.assertTrue(selected[1]["is_repeat"])
        self.assertTrue(selected[2]["is_repeat"])
        self.assertIsNotNone(note)
        self.assertIn("appeared within the last 7 days", note)

    def test_case_c_zero_fresh_three_repeats(self):
        items = [_item(1, repeat=True), _item(2, repeat=True), _item(3, repeat=True), _item(4, repeat=True)]
        seen = {x["normalized_url"] for x in items}
        selected, note = main.select_feed_items(items, seen, display_count=3)
        self.assertEqual(len(selected), 3)
        self.assertTrue(all(x["is_repeat"] for x in selected))
        self.assertIsNotNone(note)


class HistoryStoreIntegrationTests(unittest.TestCase):
    def test_second_run_marks_repeats_from_history_store(self):
        section_key = "technology__openai_news"
        items = [_item(1), _item(2), _item(3), _item(4)]

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "history_urls.json"
            store = main.load_history_urls_store(path)
            day1 = date(2026, 2, 20)
            seen_day1 = main.build_seen_urls_by_section(store, today=day1, window_days=7)
            selected1, note1 = main.select_feed_items(items, seen_day1.get(section_key, set()), display_count=3)
            self.assertIsNone(note1)
            store[day1.isoformat()] = {section_key: [x["normalized_url"] for x in selected1]}
            main.save_history_urls_store(path, store)

            store2 = main.load_history_urls_store(path)
            day2 = date(2026, 2, 21)
            seen_day2 = main.build_seen_urls_by_section(store2, today=day2, window_days=7)
            selected2, note2 = main.select_feed_items(items, seen_day2.get(section_key, set()), display_count=3)

            self.assertEqual(len(selected2), 3)
            self.assertEqual(sum(1 for x in selected2 if x["is_repeat"]), 2)
            self.assertEqual(sum(1 for x in selected2 if not x["is_repeat"]), 1)
            self.assertIsNotNone(note2)


class AgentMemoryTests(unittest.TestCase):
    def test_update_agent_memory_success_then_failure(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "agent_memory.json"

            success_status = {
                "run": {
                    "id": "run-1",
                    "state": "SUCCESS",
                    "started_at": "2026-02-27T09:00:00",
                    "finished_at": "2026-02-27T09:00:30",
                    "duration_seconds": 30.0,
                    "error": None,
                },
                "metrics": {"feeds_ok": 10, "feeds_failed": 0, "items_total": 20},
            }
            m1 = main.update_agent_memory(path, success_status)
            self.assertEqual(m1["totals"]["runs"], 1)
            self.assertEqual(m1["totals"]["successes"], 1)
            self.assertEqual(m1["totals"]["failures"], 0)
            self.assertEqual(m1["streaks"]["success"], 1)
            self.assertEqual(m1["streaks"]["failure"], 0)
            self.assertEqual(m1["health"]["state"], "healthy")

            failure_status = {
                "run": {
                    "id": "run-2",
                    "state": "FAILED",
                    "started_at": "2026-02-27T10:00:00",
                    "finished_at": "2026-02-27T10:00:05",
                    "duration_seconds": 5.0,
                    "error": {"message": "boom"},
                },
                "metrics": {"feeds_ok": 0, "feeds_failed": 4, "items_total": 0},
            }
            m2 = main.update_agent_memory(path, failure_status)
            self.assertEqual(m2["totals"]["runs"], 2)
            self.assertEqual(m2["totals"]["successes"], 1)
            self.assertEqual(m2["totals"]["failures"], 1)
            self.assertEqual(m2["streaks"]["success"], 0)
            self.assertEqual(m2["streaks"]["failure"], 1)
            self.assertEqual(m2["health"]["state"], "failed")

            loaded = main.load_agent_memory(path)
            self.assertEqual(loaded["totals"]["runs"], 2)
            self.assertEqual(loaded["last_run"]["id"], "run-2")

    def test_html_memory_dashboard_renders(self):
        md = "# Trend Agent Report\n\n## Technology\n\n### Feed A\n\n- [Item](https://example.com)\n"
        html = main.md_to_simple_html(
            md,
            memory={
                "totals": {"runs": 9, "successes": 8, "failures": 1},
                "streaks": {"success": 3, "failure": 0},
                "health": {"state": "healthy"},
            },
            run_snapshot={"items_total": 12, "feeds_failed": 2},
        )
        self.assertIn("Agent Memory Dashboard", html)
        self.assertIn("Total Runs", html)
        self.assertIn(">9<", html)
        self.assertIn("This Run Feed Errors", html)
        self.assertIn(">2<", html)


if __name__ == "__main__":
    unittest.main()
