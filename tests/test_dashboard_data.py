import json
import tempfile
import unittest
from pathlib import Path

import api_logger
from dashboard_data import build_dashboard_snapshot, load_api_status, load_scheduler_snapshot
from schedule_store import ScheduleStore


class DashboardDataTests(unittest.TestCase):
    def test_load_scheduler_snapshot_reads_sqlite_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "schedule_state.db"
            store = ScheduleStore(path=db_path)
            store.add_subscription(321, "reddit")
            store.set_seen_ids(321, "reddit", {"job-a", "job-b"})
            store.set_run_state(
                321,
                "reddit",
                last_run_at="2026-04-11T10:00:00+00:00",
                last_result_count=4,
                last_error=None,
            )
            snapshot = load_scheduler_snapshot(db_path)
            store.close()

            self.assertEqual(snapshot["summary"]["scheduled_platforms"], 1)
            self.assertEqual(snapshot["platform_rows"][0]["seen_job_count"], 2)
            self.assertEqual(snapshot["platform_rows"][0]["last_result_count"], 4)

    def test_api_status_and_dashboard_snapshot_read_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_root = Path(temp_dir) / "api"
            monitor_log = Path(temp_dir) / "monitor.log"
            monitor_log.write_text("line-1\nline-2\n", encoding="utf-8")

            original_root = api_logger.LOG_ROOT
            api_logger.LOG_ROOT = log_root
            try:
                api_logger.log_api_event("reddit", "listings", 200, payload={"ok": True})
                api_logger.log_api_event("telegram", "sendMessage", "exception", error="boom")
            finally:
                api_logger.LOG_ROOT = original_root

            status = load_api_status(log_root)
            snapshot = build_dashboard_snapshot(
                Path(temp_dir) / "missing.db",
                log_root,
                monitor_log,
            )

            self.assertEqual(status["summary"]["sources"], 2)
            self.assertEqual(len(snapshot["recent_api_logs"]), 2)
            self.assertEqual(snapshot["monitor_log_tail"][-1], "line-2")

    def test_api_status_marks_source_unhealthy_when_recent_errors_accumulate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_root = Path(temp_dir) / "api"

            original_root = api_logger.LOG_ROOT
            api_logger.LOG_ROOT = log_root
            try:
                for _ in range(17):
                    api_logger.log_api_event("discord", "channel_messages", "exception", error="boom")
                api_logger.log_api_event("discord", "history_run", 200, payload={"jobs_found": 3})
            finally:
                api_logger.LOG_ROOT = original_root

            status = load_api_status(log_root)
            discord = next(source for source in status["sources"] if source["source"] == "discord")

            self.assertEqual(discord["latest_status"], "200")
            self.assertEqual(discord["recent_error_count"], 17)
            self.assertFalse(discord["healthy"])
            self.assertEqual(discord["health"], "degraded")
