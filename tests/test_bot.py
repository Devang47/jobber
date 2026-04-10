import asyncio
import tempfile
import unittest
from pathlib import Path

from bot import JobBot
from config import Config
from platforms.base import PlatformJob
from schedule_store import ScheduleStore


class FakeNotifier:
    def __init__(self):
        self.text_messages = []
        self.button_messages = []
        self.job_messages = []
        self.pending_jobs = {}
        self.pending_proposals = {}
        self.callback_answers = []

    async def setup_commands(self):
        return None

    async def close(self):
        return None

    async def get_updates(self, timeout=10):
        return []

    async def send_text(self, text, chat_id=None, parse_mode="HTML"):
        self.text_messages.append((chat_id, text, parse_mode))
        return {"chat_id": chat_id, "text": text}

    async def send_with_buttons(self, text, buttons, chat_id=None):
        self.button_messages.append((chat_id, text, buttons))
        return {"chat_id": chat_id, "text": text, "buttons": buttons}

    async def send_job_card(self, job_card, job, job_id="", url="", chat_id=None):
        self.job_messages.append((chat_id, job_card, url))
        if chat_id is not None and job_id:
            self.pending_jobs[(chat_id, job_id)] = job
        return {"chat_id": chat_id, "text": job_card}

    async def answer_callback(self, callback_query_id, text=""):
        self.callback_answers.append((callback_query_id, text))
        return None


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


def make_job(title="React dashboard build", description="Need a React developer to build a dashboard."):
    return PlatformJob(
        platform="reddit",
        title=title,
        description=description,
        skills=["React", "TypeScript"],
        budget="$500",
        job_type="fixed",
        url="https://example.com/job-1",
        posted_by="u/client",
        posted_time="2026-04-10T10:30:00+00:00",
        location="Remote",
        job_id="job-1",
    )


class JobBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ScheduleStore(path=Path(self.temp_dir.name) / "schedule_state.db")
        self.notifier = FakeNotifier()
        self.fetch_calls = 0
        self.proposal_calls = 0

        async def fetch_reddit(seen_ids):
            self.fetch_calls += 1
            if "job-1" in seen_ids:
                return []
            seen_ids.add("job-1")
            return [make_job()]

        async def proposal_generator(job, profile):
            self.proposal_calls += 1
            return f"Proposal for {job.title} as {profile.get('name') or 'Manas'}"

        self.bot = JobBot(
            config=make_config(),
            notifier=self.notifier,
            schedule_store=self.store,
            fetchers={
                "reddit": fetch_reddit,
                "discord": fetch_reddit,
                "wellfound": fetch_reddit,
                "upwork": fetch_reddit,
                "freelancer": fetch_reddit,
            },
            proposal_generator=proposal_generator,
            interval_seconds=3600,
        )

    async def asyncTearDown(self):
        await self.bot.shutdown_schedules()
        self.store.close()
        self.temp_dir.cleanup()

    async def test_start_defaults_to_core_bundle(self):
        await self.bot.handle_command("/start", "42", 42)
        self.assertTrue(self.store.is_subscribed(42, "discord"))
        self.assertTrue(self.store.is_subscribed(42, "reddit"))
        self.assertIn((42, "discord"), self.bot.scheduled_jobs)
        self.assertIn((42, "reddit"), self.bot.scheduled_jobs)

    async def test_stop_core_stops_both_primary_platforms(self):
        await self.bot.handle_command("/start", "42", 42)
        await self.bot.handle_command("/stop core", "42", 42)
        self.assertFalse(self.store.is_subscribed(42, "discord"))
        self.assertFalse(self.store.is_subscribed(42, "reddit"))

    async def test_platform_cycle_dedupes_seen_jobs(self):
        await self.bot.run_platform_cycle(42, "42", "reddit", scheduled=False)
        first_text_count = len(self.notifier.text_messages)
        first_job_count = len(self.notifier.job_messages)

        await self.bot.run_platform_cycle(42, "42", "reddit", scheduled=False)
        second_text_count = len(self.notifier.text_messages)
        second_job_count = len(self.notifier.job_messages)

        self.assertGreaterEqual(first_text_count, 1)
        self.assertEqual(first_job_count, 1)
        self.assertEqual(second_job_count, 1)
        self.assertEqual(second_text_count, first_text_count + 1)
        self.assertIn("No new jobs found", self.notifier.text_messages[-1][1])

    async def test_overlapping_run_is_skipped(self):
        started = asyncio.Event()
        release = asyncio.Event()
        fetch_calls = {"count": 0}

        async def blocking_fetcher(seen_ids):
            fetch_calls["count"] += 1
            started.set()
            await release.wait()
            if "job-1" in seen_ids:
                return []
            seen_ids.add("job-1")
            return [make_job()]

        self.bot.fetchers["reddit"] = blocking_fetcher

        first_run = asyncio.create_task(self.bot.run_platform_cycle(42, "42", "reddit", scheduled=False))
        await started.wait()
        await self.bot.run_platform_cycle(42, "42", "reddit", scheduled=False)
        release.set()
        await first_run

        self.assertEqual(fetch_calls["count"], 1)
        self.assertTrue(
            any("already has a run in progress" in message[1] for message in self.notifier.text_messages)
        )

    async def test_proposals_are_generated_only_on_callback(self):
        await self.bot.run_platform_cycle(42, "42", "reddit", scheduled=False)
        self.assertEqual(self.proposal_calls, 0)
        self.assertEqual(len(self.notifier.job_messages), 1)

        delivery_id = next(job_id for stored_chat_id, job_id in self.notifier.pending_jobs if stored_chat_id == 42)
        callback = {
            "id": "cb-1",
            "data": f"proposal_{delivery_id}",
            "from": {"id": 42},
            "message": {"chat": {"id": 42}},
        }
        await self.bot.handle_callback(callback)

        self.assertEqual(self.proposal_calls, 1)
        self.assertEqual(self.notifier.text_messages[-1][2], "")
        self.assertIn("Proposal for React dashboard build", self.notifier.text_messages[-1][1])

    async def test_irrelevant_jobs_are_filtered_out(self):
        async def irrelevant_fetcher(seen_ids):
            seen_ids.add("job-2")
            return [
                make_job(
                    title="SEO manager needed",
                    description="Need help with SEO, lead generation, and social media growth.",
                )
            ]

        self.bot.fetchers["reddit"] = irrelevant_fetcher
        await self.bot.run_platform_cycle(42, "42", "reddit", scheduled=False)

        self.assertEqual(len(self.notifier.job_messages), 0)
        self.assertIn("No new jobs found", self.notifier.text_messages[-1][1])
