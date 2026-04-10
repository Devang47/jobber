"""
Telegram-controlled scheduled job bot.

Core behavior:
- /start <platform> creates a recurring 15-minute job for the current Telegram chat
- /stop <platform> cancels that recurring job
- /scan [platform] runs the same pipeline immediately without scheduling it
"""

import asyncio
import logging
import signal
import uuid
from datetime import datetime, timedelta, timezone
from html import escape as html_escape

from api_logger import log_api_event
from config import Config
from job_relevance import RelevanceResult, evaluate_job
from logger_setup import setup_logging
from notifier import TelegramNotifier
from platforms.base import PlatformJob
from profiles import format_profile, get_profile, list_all_profiles, set_profile_field
from schedule_store import ScheduleStore
from telegram_jobs import format_ranked_platform_job

logger = logging.getLogger("bot")

CORE_PLATFORMS = ("discord", "reddit")
OPTIONAL_PLATFORMS = ("wellfound", "upwork", "freelancer")
SUPPORTED_PLATFORMS = CORE_PLATFORMS + OPTIONAL_PLATFORMS
MAX_JOBS_PER_RUN = 10

HELP_TEXT = """<b>Job Bot</b>

<b>Primary Workflow:</b>
  /start - Schedule the core bundle (Discord + Reddit) every 15 minutes
  /scan - Run your subscribed platforms now, or the core bundle if nothing is subscribed
  /stop core - Stop the core bundle

<b>Bundles And Platforms:</b>
  /start core - Schedule Discord + Reddit together
  /start upwork - Add one optional platform
  /start wellfound - Add one optional platform
  /start freelancer - Add one optional platform
  /start all - Schedule everything
  /stop [core|discord|reddit|wellfound|upwork|freelancer|all]
  /status - Show active schedules and last run info

<b>Manual Runs:</b>
  /scan core - Run Discord + Reddit once right now
  /scan upwork - Run one platform once right now

<b>Your Profile:</b>
  /profile - View your profile
  /set name Your Name
  /set github https://github.com/you
  /set portfolio https://yoursite.com
  /set rate $20-30/hr
  /set skills React, Node, Python

<b>Other:</b>
  /users - List registered users
  /help - Show this message"""


PROPOSAL_PROMPT = """Write a short proposal to apply for this freelance dev job.
You are {name} - full stack dev.

Your profile:
- Stack: React, Next.js, Node.js, Python, Django, Flask, TypeScript, automation, bots, web scraping, DevOps
- Portfolio: {portfolio}
- GitHub: {github}
- Rate: {rate}

Rules:
- 70-120 words MAX
- Open by referencing something SPECIFIC about their project
- Drop 1-2 concrete skills that match what they need
- Mention a similar project you have built or can reference
- Show availability
- End with: "Here's my work: {portfolio}" or "Check my GitHub: {github}"
- Sound like a real human on {platform}
- NO emojis, NO bullet lists, NO "Best regards"
- Be confident, not desperate

Job:
Title: {title}
Description: {description}
Skills needed: {skills}
Budget: {budget}

Write ONLY the message, nothing else."""

def _default_profile() -> dict:
    return {
        "name": "Manas",
        "portfolio": "",
        "github": "",
        "rate": "negotiable",
        "skills": "",
    }


class JobBot:
    def __init__(
        self,
        *,
        config: Config | None = None,
        notifier: TelegramNotifier | None = None,
        schedule_store: ScheduleStore | None = None,
        fetchers: dict[str, object] | None = None,
        proposal_generator=None,
        interval_seconds: int | None = None,
    ):
        self.config = config or Config.from_env()
        self.notifier = notifier or TelegramNotifier(self.config)
        self.schedule_store = schedule_store or ScheduleStore(path=self.config.schedule_db_path)
        self.interval_seconds = interval_seconds or self.config.schedule_interval_seconds
        self.fetchers = fetchers or self._build_default_fetchers()
        self.proposal_generator = proposal_generator or self._generate_proposal_with_groq
        self.shutdown = asyncio.Event()
        self.scheduled_jobs: dict[tuple[int, str], asyncio.Task] = {}
        self._groq_client = None

    def _build_default_fetchers(self) -> dict[str, object]:
        return {
            "discord": self._fetch_discord_jobs,
            "reddit": self._fetch_reddit_jobs,
            "wellfound": self._fetch_wellfound_jobs,
            "upwork": self._fetch_upwork_jobs,
            "freelancer": self._fetch_freelancer_jobs,
        }

    async def _fetch_discord_jobs(self, seen_ids: set[str]) -> list[PlatformJob]:
        from platforms.discord_history import fetch_discord_jobs

        return await fetch_discord_jobs(self.config, seen_ids)

    async def _fetch_reddit_jobs(self, seen_ids: set[str]) -> list[PlatformJob]:
        from platforms.reddit import fetch_reddit_jobs

        return await fetch_reddit_jobs(seen_ids)

    async def _fetch_wellfound_jobs(self, seen_ids: set[str]) -> list[PlatformJob]:
        from platforms.wellfound import fetch_wellfound_jobs

        return await fetch_wellfound_jobs(seen_ids)

    async def _fetch_upwork_jobs(self, seen_ids: set[str]) -> list[PlatformJob]:
        from platforms.upwork import fetch_upwork_jobs

        return await fetch_upwork_jobs(seen_ids)

    async def _fetch_freelancer_jobs(self, seen_ids: set[str]) -> list[PlatformJob]:
        from platforms.freelancer_api import fetch_freelancer_jobs

        return await fetch_freelancer_jobs(seen_ids)

    async def run(self):
        setup_logging(self.config.log_level)
        logger.info("Job Bot starting with scheduled platform runners")

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.shutdown.set)

        await self.notifier.setup_commands()
        await self.restore_schedules()

        while not self.shutdown.is_set():
            try:
                updates = await self.notifier.get_updates(timeout=15)
                for update in updates:
                    if "callback_query" in update:
                        await self.handle_callback(update["callback_query"])
                        continue

                    message = update.get("message", {})
                    text = message.get("text", "").strip()
                    user = message.get("from", {})
                    user_id = str(user.get("id", ""))
                    chat_id = message.get("chat", {}).get("id")
                    if text and chat_id is not None:
                        await self.handle_command(text, user_id, int(chat_id))
            except Exception as exc:
                logger.error(f"Bot loop error: {exc}", exc_info=True)
                await asyncio.sleep(3)

        await self.shutdown_schedules()
        await self.notifier.close()
        self.schedule_store.close()
        logger.info("Job Bot stopped")

    async def restore_schedules(self) -> None:
        for chat_id, platform in self.schedule_store.list_subscriptions():
            self._ensure_schedule(chat_id, platform)
        logger.info(f"Restored {len(self.scheduled_jobs)} scheduled platform jobs")

    async def shutdown_schedules(self) -> None:
        tasks = list(self.scheduled_jobs.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.scheduled_jobs.clear()

    async def handle_callback(self, callback: dict):
        callback_id = callback.get("id", "")
        data = callback.get("data", "")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        user_id = str(callback.get("from", {}).get("id", ""))

        if data.startswith("copy_"):
            proposal = self.notifier.pending_proposals.get((chat_id, data[5:]))
            if proposal:
                await self.notifier.send_text(proposal, chat_id, parse_mode="")
                await self.notifier.answer_callback(callback_id, "Proposal sent below.")
            else:
                await self.notifier.answer_callback(callback_id, "Proposal expired or not found.")
            return

        if data.startswith("proposal_") and chat_id is not None:
            delivery_id = data[9:]
            cached = self.notifier.pending_proposals.get((chat_id, delivery_id))
            if cached:
                await self.notifier.answer_callback(callback_id, "Proposal ready.")
                await self.notifier.send_text(cached, chat_id, parse_mode="")
                return

            job = self.notifier.pending_jobs.get((chat_id, delivery_id))
            if job is None:
                await self.notifier.answer_callback(callback_id, "Job expired or not found.")
                return

            await self.notifier.answer_callback(callback_id, "Generating proposal...")
            proposal = await self.generate_proposal(job, get_profile(user_id))
            if not proposal:
                await self.notifier.send_text(
                    "Could not generate a proposal right now. Please try again in a moment.",
                    chat_id,
                )
                return

            self.notifier.pending_proposals[(chat_id, delivery_id)] = proposal
            await self.notifier.send_text(proposal, chat_id, parse_mode="")
            return

        await self.notifier.answer_callback(callback_id)

    async def handle_command(self, text: str, user_id: str, chat_id: int):
        command = text.strip()
        normalized = command.lower()
        if "@" in normalized:
            normalized = normalized.split("@")[0]
            command = command.split("@")[0]

        logger.info(f"Command from {user_id} in {chat_id}: {normalized}")

        if normalized == "/help":
            await self.notifier.send_text(HELP_TEXT, chat_id)
        elif normalized == "/start":
            await self.handle_start(chat_id, "core")
        elif normalized == "/status":
            await self.send_status(chat_id)
        elif normalized == "/users":
            await self.notifier.send_text(list_all_profiles(), chat_id)
        elif normalized == "/profile":
            await self.notifier.send_text(format_profile(user_id), chat_id)
        elif normalized.startswith("/set "):
            await self.handle_set(normalized[5:].strip(), user_id, command[5:].strip(), chat_id)
        elif normalized == "/scan":
            await self.handle_scan(chat_id, user_id, "")
        elif normalized.startswith("/scan "):
            platform = normalized[5:].strip() if len(normalized) > 5 else ""
            await self.handle_scan(chat_id, user_id, platform)
        elif normalized.startswith("/start "):
            platform = normalized[7:].strip()
            await self.handle_start(chat_id, platform)
        elif normalized == "/stop":
            await self.handle_stop(chat_id, "core")
        elif normalized.startswith("/stop "):
            platform = normalized[6:].strip()
            await self.handle_stop(chat_id, platform)
        else:
            await self.notifier.send_text(HELP_TEXT, chat_id)

    async def handle_set(self, args: str, user_id: str, original_args: str, chat_id: int):
        parts = args.split(" ", 1)
        if len(parts) < 2:
            await self.notifier.send_text(
                "Usage: /set name Your Name\nFields: name, github, portfolio, rate, skills",
                chat_id,
            )
            return

        field = parts[0].lower()
        original_parts = original_args.split(" ", 1)
        value = original_parts[1] if len(original_parts) > 1 else parts[1]

        valid_fields = ["name", "github", "portfolio", "rate", "skills"]
        if field not in valid_fields:
            await self.notifier.send_text(
                f"Unknown field: <i>{html_escape(field)}</i>\nValid: {', '.join(valid_fields)}",
                chat_id,
            )
            return

        set_profile_field(user_id, field, value)
        await self.notifier.send_text(
            f"Updated <b>{html_escape(field)}</b> to: {html_escape(value)}",
            chat_id,
        )

    async def handle_start(self, chat_id: int, platform_arg: str):
        platforms = self._resolve_platforms(platform_arg)
        if platforms is None:
            await self.notifier.send_text(self._platform_usage_text("start"), chat_id)
            return

        newly_started = []
        already_running = []
        for platform in platforms:
            if self.schedule_store.is_subscribed(chat_id, platform):
                already_running.append(platform)
                continue
            self.schedule_store.add_subscription(chat_id, platform)
            self._ensure_schedule(chat_id, platform)
            newly_started.append(platform)

        lines = []
        if newly_started:
            joined = ", ".join(platform.title() for platform in newly_started)
            lines.append(f"Scheduled every 15 minutes: <b>{html_escape(joined)}</b>.")
            lines.append("The first run starts immediately.")
        if already_running:
            joined = ", ".join(platform.title() for platform in already_running)
            lines.append(f"Already scheduled: <b>{html_escape(joined)}</b>.")

        await self.notifier.send_text("\n".join(lines), chat_id)

    async def handle_stop(self, chat_id: int, platform_arg: str):
        if platform_arg == "all":
            current = self.schedule_store.get_subscriptions(chat_id)
            self.schedule_store.remove_all_subscriptions(chat_id)
            for platform in current:
                await self._cancel_schedule(chat_id, platform)
            await self.notifier.send_text("Stopped all scheduled platform jobs for this chat.", chat_id)
            return

        platforms = self._resolve_platforms(platform_arg)
        if platforms is None:
            await self.notifier.send_text(self._platform_usage_text("stop"), chat_id)
            return

        stopped = []
        not_running = []
        for platform in platforms:
            if not self.schedule_store.is_subscribed(chat_id, platform):
                not_running.append(platform)
                continue
            self.schedule_store.remove_subscription(chat_id, platform)
            await self._cancel_schedule(chat_id, platform)
            stopped.append(platform)

        lines = []
        if stopped:
            lines.append(
                f"Stopped scheduled updates for <b>{html_escape(', '.join(platform.title() for platform in stopped))}</b>."
            )
        if not_running:
            lines.append(
                f"Not currently scheduled: <b>{html_escape(', '.join(platform.title() for platform in not_running))}</b>."
            )
        await self.notifier.send_text("\n".join(lines), chat_id)

    async def handle_scan(self, chat_id: int, user_id: str, platform_arg: str):
        if platform_arg:
            platforms = self._resolve_platforms(platform_arg)
            if platforms is None:
                await self.notifier.send_text(self._platform_usage_text("scan"), chat_id)
                return
        else:
            platforms = self.schedule_store.get_subscriptions(chat_id) or list(CORE_PLATFORMS)

        await self.notifier.send_text(
            f"Running an immediate scan for: <b>{html_escape(', '.join(p.title() for p in platforms))}</b>.",
            chat_id,
        )
        for platform in platforms:
            await self.run_platform_cycle(chat_id, user_id, platform, scheduled=False)

    async def send_status(self, chat_id: int):
        subscriptions = self.schedule_store.get_subscriptions(chat_id)
        if not subscriptions:
            await self.notifier.send_text("No active schedules for this chat. Use /start or /start core.", chat_id)
            return

        lines = ["<b>Scheduled Platforms</b>"]
        for platform in subscriptions:
            state = self.schedule_store.get_run_state(chat_id, platform)
            last_run = state.get("last_run_at", "never")
            result_count = state.get("last_result_count", 0)
            error = state.get("last_error")

            if last_run != "never":
                next_run = self._compute_next_run(last_run)
                lines.append(
                    f"\n<b>{platform.title()}</b>\n"
                    f"Last run: {html_escape(last_run)}\n"
                    f"Last result count: {result_count}\n"
                    f"Next run: {html_escape(next_run)}"
                )
            else:
                lines.append(f"\n<b>{platform.title()}</b>\nLast run: never\nNext run: starting soon")

            if error:
                lines.append(f"Last error: {html_escape(error)}")

        await self.notifier.send_text("\n".join(lines), chat_id)

    def _compute_next_run(self, last_run_at: str) -> str:
        try:
            last_run = datetime.fromisoformat(last_run_at)
        except ValueError:
            return "unknown"
        next_run = last_run + timedelta(seconds=self.interval_seconds)
        return next_run.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _resolve_platforms(self, platform_arg: str) -> list[str] | None:
        platform = platform_arg.strip().lower()
        if not platform:
            return None
        if platform in {"core", "primary"}:
            return list(CORE_PLATFORMS)
        if platform == "all":
            return list(SUPPORTED_PLATFORMS)
        if platform not in SUPPORTED_PLATFORMS:
            return None
        return [platform]

    def _platform_usage_text(self, verb: str) -> str:
        return (
            f"Usage: /{verb} <platform>\n"
            f"Available: core, {', '.join(SUPPORTED_PLATFORMS)}, all"
        )

    def _ensure_schedule(self, chat_id: int, platform: str) -> None:
        key = (chat_id, platform)
        task = self.scheduled_jobs.get(key)
        if task and not task.done():
            return
        self.scheduled_jobs[key] = asyncio.create_task(self._schedule_loop(chat_id, platform))

    async def _cancel_schedule(self, chat_id: int, platform: str) -> None:
        key = (chat_id, platform)
        task = self.scheduled_jobs.get(key)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.scheduled_jobs.pop(key, None)

    async def _schedule_loop(self, chat_id: int, platform: str) -> None:
        key = (chat_id, platform)
        logger.info(f"Starting schedule loop for chat={chat_id} platform={platform}")
        try:
            while not self.shutdown.is_set() and self.schedule_store.is_subscribed(chat_id, platform):
                await self.run_platform_cycle(chat_id, str(chat_id), platform, scheduled=True)
                try:
                    await asyncio.wait_for(self.shutdown.wait(), timeout=self.interval_seconds)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.info(f"Cancelled schedule loop for chat={chat_id} platform={platform}")
            raise
        finally:
            current = self.scheduled_jobs.get(key)
            if current is asyncio.current_task():
                self.scheduled_jobs.pop(key, None)

    async def run_platform_cycle(self, chat_id: int, user_id: str, platform: str, *, scheduled: bool) -> None:
        started_at = datetime.now(timezone.utc)
        started_at_iso = started_at.isoformat()
        claimed = self.schedule_store.claim_run(chat_id, platform, started_at=started_at_iso)
        if not claimed:
            logger.info(f"Skipping overlapping run for chat={chat_id} platform={platform}")
            log_api_event(
                "scheduler",
                "platform_run_skipped",
                "already_running",
                chat_id=chat_id,
                platform=platform,
                scheduled=scheduled,
            )
            if not scheduled:
                await self.notifier.send_text(
                    f"<b>{platform.title()}</b> already has a run in progress. Try again in a moment.",
                    chat_id,
                )
            return

        seen_ids = self.schedule_store.get_seen_ids(chat_id, platform)
        fetcher = self.fetchers[platform]

        try:
            jobs = await fetcher(seen_ids)
            self.schedule_store.set_seen_ids(chat_id, platform, seen_ids)
            scored_jobs = sorted(
                (
                    (relevance, job)
                    for job in jobs
                    for relevance in [evaluate_job(job)]
                    if relevance.eligible
                ),
                key=lambda item: item[0].score,
                reverse=True,
            )
            limited_jobs = scored_jobs[:MAX_JOBS_PER_RUN]

            await self._send_run_results(chat_id, user_id, platform, limited_jobs, len(scored_jobs), scheduled=scheduled)
            self.schedule_store.set_run_state(
                chat_id,
                platform,
                last_run_at=started_at_iso,
                last_result_count=len(scored_jobs),
                last_error=None,
            )
            log_api_event(
                "scheduler",
                "platform_run",
                "success",
                payload={"jobs_found": len(scored_jobs)},
                chat_id=chat_id,
                platform=platform,
                scheduled=scheduled,
            )
        except Exception as exc:
            error_text = str(exc)
            logger.error(f"Scheduled run failed for chat={chat_id} platform={platform}: {exc}", exc_info=True)
            self.schedule_store.set_run_state(
                chat_id,
                platform,
                last_run_at=started_at_iso,
                last_result_count=0,
                last_error=error_text,
            )
            log_api_event(
                "scheduler",
                "platform_run",
                "exception",
                chat_id=chat_id,
                platform=platform,
                scheduled=scheduled,
                error=error_text,
            )
            if not scheduled:
                await self.notifier.send_text(
                    f"Scan failed for <b>{platform.title()}</b>: {html_escape(error_text)}",
                    chat_id,
                )
        finally:
            self.schedule_store.release_run(chat_id, platform)

    async def _send_run_results(
        self,
        chat_id: int,
        user_id: str,
        platform: str,
        scored_jobs: list[tuple[RelevanceResult, PlatformJob]],
        total_count: int,
        *,
        scheduled: bool,
    ) -> None:
        if not scored_jobs:
            if not scheduled:
                await self.notifier.send_text(
                    f"No new jobs found for <b>{platform.title()}</b>.",
                    chat_id,
                )
            return

        run_type = "Scheduled update" if scheduled else "Manual scan"
        await self.notifier.send_text(
            f"<b>{run_type}: {platform.title()}</b>\n"
            f"New jobs found: {total_count}\n"
            f"Sending top {len(scored_jobs)} matches.",
            chat_id,
        )

        for score, job in scored_jobs:
            await self.send_platform_job(job, score, chat_id, user_id)
            await asyncio.sleep(0.2)

    async def send_platform_job(self, job: PlatformJob, relevance: RelevanceResult, chat_id: int, user_id: str):
        del user_id
        job_card = format_ranked_platform_job(job, relevance)
        delivery_id = uuid.uuid4().hex[:6]
        await self.notifier.send_job_card(
            job_card,
            job,
            job_id=delivery_id,
            url=job.url,
            chat_id=chat_id,
        )

    async def generate_proposal(self, job: PlatformJob, profile: dict | None = None) -> str | None:
        selected_profile = profile if profile and profile.get("name") else _default_profile()
        return await self.proposal_generator(job, selected_profile)

    async def _generate_proposal_with_groq(self, job: PlatformJob, profile: dict) -> str | None:
        if self._groq_client is None:
            try:
                from groq import AsyncGroq
            except ModuleNotFoundError:
                log_api_event("groq", "proposal", "missing_dependency", platform=job.platform)
                logger.warning("Groq SDK is not installed; skipping proposal generation")
                return None
            self._groq_client = AsyncGroq(api_key=self.config.groq_api_key)

        skills_str = ", ".join(job.skills) if job.skills else "Not specified"
        prompt = PROPOSAL_PROMPT.format(
            name=profile.get("name", "Manas"),
            platform=job.platform.title(),
            title=job.title,
            description=job.description[:400],
            skills=skills_str,
            budget=job.budget or "Not specified",
            portfolio=profile.get("portfolio", ""),
            github=profile.get("github", ""),
            rate=profile.get("rate", "negotiable"),
        )

        try:
            response = await self._groq_client.chat.completions.create(
                model=self.config.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=250,
            )
            content = response.choices[0].message.content.strip().strip('"')
            log_api_event(
                "groq",
                "proposal",
                "success",
                payload={"preview": content[:250]},
                platform=job.platform,
            )
            return content
        except Exception as exc:
            log_api_event("groq", "proposal", "exception", platform=job.platform, error=str(exc))
            logger.error(f"Proposal generation failed: {exc}")
            return None


if __name__ == "__main__":
    asyncio.run(JobBot().run())
