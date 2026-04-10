import json
import tempfile
import unittest
from pathlib import Path

import api_logger


class ApiLoggerTests(unittest.TestCase):
    def test_log_api_event_writes_jsonl_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_root = api_logger.LOG_ROOT
            api_logger.LOG_ROOT = Path(temp_dir)
            try:
                api_logger.log_api_event(
                    "reddit",
                    "listings",
                    200,
                    payload={"items": ["a", "b"]},
                    subreddit="forhire",
                )
                files = list(Path(temp_dir).glob("reddit-*.jsonl"))
                self.assertEqual(len(files), 1)
                lines = files[0].read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["source"], "reddit")
                self.assertEqual(entry["action"], "listings")
                self.assertEqual(entry["status"], "200")
                self.assertEqual(entry["metadata"]["subreddit"], "forhire")
            finally:
                api_logger.LOG_ROOT = original_root
