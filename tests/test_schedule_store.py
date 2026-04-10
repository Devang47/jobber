import tempfile
import unittest
from pathlib import Path

from schedule_store import ScheduleStore


class ScheduleStoreTests(unittest.TestCase):
    def test_subscriptions_and_seen_ids_persist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "schedule_state.db"
            store = ScheduleStore(path=path)
            store.add_subscription(123, "reddit")
            store.set_seen_ids(123, "reddit", {"job-1", "job-2"})
            store.set_run_state(
                123,
                "reddit",
                last_run_at="2026-04-10T10:00:00+00:00",
                last_result_count=2,
                last_error=None,
            )

            restored = ScheduleStore(path=path)
            self.assertTrue(restored.is_subscribed(123, "reddit"))
            self.assertEqual(restored.get_seen_ids(123, "reddit"), {"job-1", "job-2"})
            self.assertEqual(restored.get_run_state(123, "reddit")["last_result_count"], 2)
            store.close()
            restored.close()

    def test_run_claim_prevents_duplicate_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "schedule_state.db"
            store = ScheduleStore(path=path)
            self.assertTrue(store.claim_run(123, "reddit", started_at="2026-04-11T10:00:00+00:00"))
            self.assertTrue(store.is_run_active(123, "reddit"))
            self.assertFalse(store.claim_run(123, "reddit", started_at="2026-04-11T10:01:00+00:00"))
            store.release_run(123, "reddit")
            self.assertFalse(store.is_run_active(123, "reddit"))
            self.assertTrue(store.claim_run(123, "reddit", started_at="2026-04-11T10:02:00+00:00"))
            store.close()
