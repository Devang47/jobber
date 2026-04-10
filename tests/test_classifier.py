import unittest

from classifier import JobClassifier
from config import Config


def make_config() -> Config:
    return Config(
        discord_token="token",
        server_ids=[1],
        groq_api_key="groq",
        groq_model="model",
        telegram_bot_token="telegram",
        telegram_chat_id=None,
        min_message_length=50,
        prefilter_keywords=["hiring"],
        log_level="INFO",
        reconnect_delay=5,
        max_reconnect_attempts=10,
        telegram_cooldown=30,
        schedule_interval_seconds=900,
        schedule_db_path="schedule_state.db",
    )


class JobClassifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_classifies_dev_job_without_ai(self):
        classifier = JobClassifier(make_config())
        message = {
            "id": "m1",
            "guild_id": "g1",
            "channel_id": "c1",
            "_server_name": "Server",
            "_channel_name": "jobs",
            "author": {"username": "client", "id": "u1"},
            "content": (
                "[Hiring] Need a full stack developer to build a React dashboard with Node.js APIs. "
                "Budget is $1500. DM me if interested."
            ),
        }

        job = await classifier.classify(message)
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.pay, "$1500")
        self.assertIn("React", job.skills)
        self.assertIn("Node.js", job.skills)
        self.assertEqual(job.contact_info, "DM on Discord")

    async def test_rejects_irrelevant_non_dev_job(self):
        classifier = JobClassifier(make_config())
        message = {
            "id": "m2",
            "guild_id": "g1",
            "channel_id": "c1",
            "_server_name": "Server",
            "_channel_name": "jobs",
            "author": {"username": "client", "id": "u1"},
            "content": (
                "Hiring virtual assistant for data entry, lead generation, and customer support. "
                "Paid weekly, DM me."
            ),
        }

        job = await classifier.classify(message)
        self.assertIsNone(job)
