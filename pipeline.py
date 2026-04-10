"""
Job Pipeline — monitors Reddit + Wellfound every 2 minutes.
Sends dev jobs to Telegram with personalized proposals.
Runs alongside main.py (Discord monitor).
"""

import asyncio
import logging
import signal

from groq import AsyncGroq

from config import Config
from notifier import TelegramNotifier
from logger_setup import setup_logging
from platforms.base import PlatformJob
from platforms.reddit import fetch_reddit_jobs
from platforms.wellfound import fetch_wellfound_jobs
from telegram_jobs import format_pipeline_job

logger = logging.getLogger("pipeline")

POLL_INTERVAL = 120  # 2 minutes

APPLICATION_PROMPT = """Write a short proposal/message to apply for this freelance dev job.
You are Manas — full stack dev (React, Next.js, Node.js, Python, automation, bots, web scraping, DevOps).

Rules:
- 60-100 words MAX
- Reference their specific project/need
- Mention 1-2 matching skills
- Show availability
- End with a clear next step
- Sound human and confident
- NO emojis, NO formalities
- For Reddit: casual, like a Reddit comment/DM
- For Wellfound: slightly more professional but still brief

Job:
Title: {title}
Company: {company}
Description: {description}
Skills: {skills}
Budget: {budget}
Platform: {platform}

Write ONLY the message, nothing else."""


async def generate_proposal(groq_client, job: PlatformJob) -> str | None:
    skills_str = ", ".join(job.skills) if job.skills else "Not specified"
    prompt = APPLICATION_PROMPT.format(
        platform=job.platform.title(),
        title=job.title,
        company=job.posted_by or "Unknown",
        description=job.description[:400],
        skills=skills_str,
        budget=job.budget or "Not specified",
    )
    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip().strip('"')
    except Exception as e:
        logger.error(f"Proposal generation failed: {e}")
        return None

async def run_pipeline():
    config = Config.from_env()
    setup_logging(config.log_level)

    logger.info("=" * 60)
    logger.info("  JOB PIPELINE — Reddit + Wellfound")
    logger.info("=" * 60)

    notifier = TelegramNotifier(config)
    groq_client = AsyncGroq(api_key=config.groq_api_key)

    seen_ids = {
        "reddit": set(),
        "wellfound": set(),
    }

    shutdown = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    await notifier.send_text(
        "*Job Pipeline Started*\n\n"
        "Monitoring 2 platforms every 2 minutes:\n"
        "• Reddit (12 dev subreddits)\n"
        "• Wellfound (11 dev role categories)\n\n"
        "Discord monitor running separately.\n"
        "Each job comes with a ready-to-paste proposal."
    )

    first_run = True

    while not shutdown.is_set():
        logger.info("--- Polling Reddit + Wellfound ---")

        results = await asyncio.gather(
            fetch_reddit_jobs(seen_ids["reddit"]),
            fetch_wellfound_jobs(seen_ids["wellfound"]),
            return_exceptions=True,
        )

        all_jobs = []
        for i, result in enumerate(results):
            name = ["Reddit", "Wellfound"][i]
            if isinstance(result, Exception):
                logger.error(f"{name} error: {result}")
            elif result:
                all_jobs.extend(result)

        if not all_jobs:
            logger.info("No new jobs this cycle")
            if first_run:
                await notifier.send_text("First scan complete — no new jobs yet. Checking again in 2 minutes.")
                first_run = False
        else:
            logger.info(f"Found {len(all_jobs)} new jobs")

            jobs_to_send = all_jobs[:15]

            for job in jobs_to_send:
                proposal = await generate_proposal(groq_client, job)
                await asyncio.sleep(2)

                if not proposal:
                    continue

                job_card = format_pipeline_job(job)
                await notifier.send_job_with_proposal(job_card, proposal)
                await asyncio.sleep(1)

            if len(all_jobs) > 15:
                await notifier.send_text(
                    f"_{len(all_jobs) - 15} more jobs found but capped at 15 per cycle._"
                )

            first_run = False

        try:
            await asyncio.wait_for(shutdown.wait(), timeout=POLL_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("Pipeline stopped.")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
