import argparse
import asyncio
import logging
import re
import signal

from config import Config
from discord_gateway import DiscordGateway
from prefilter import PreFilter
from classifier import JobClassifier
from notifier import TelegramNotifier
from logger_setup import setup_logging

logger = logging.getLogger("main")


def parse_args():
    parser = argparse.ArgumentParser(description="Discord Job Monitor")
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging (shows all messages, heartbeats, API calls)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Classify jobs but don't send Telegram notifications",
    )
    parser.add_argument(
        "--log-all-messages", action="store_true",
        help="Log every incoming message (not just pre-filtered ones)",
    )
    return parser.parse_args()


async def telegram_reply_listener(notifier: TelegramNotifier, shutdown_event: asyncio.Event):
    """Poll Telegram for incoming replies and handle apply/open commands."""
    logger.info("Telegram reply listener started — listening for commands")

    while not shutdown_event.is_set():
        try:
            updates = await notifier.get_updates(timeout=10)

            for update in updates:
                message = update.get("message", {})
                text = message.get("text", "").strip().lower()
                chat_id = message.get("chat", {}).get("id")

                if not text:
                    continue

                # Match "apply <job_id>" or "/apply <job_id>"
                apply_match = re.match(r"/?apply[_ ]?([a-f0-9]{6})", text)
                open_match = re.match(r"/?open[_ ]?([a-f0-9]{6})", text)

                if apply_match:
                    job_id = apply_match.group(1)
                    await handle_open(notifier, job_id, chat_id)

                elif open_match:
                    job_id = open_match.group(1)
                    await handle_open(notifier, job_id, chat_id)

        except Exception as e:
            logger.error(f"Reply listener error: {e}")
            await asyncio.sleep(5)


async def handle_open(notifier: TelegramNotifier, job_id: str, chat_id: int):
    """Handle an open command from Telegram."""
    job = notifier.pending_jobs.get((chat_id, job_id))
    if not job:
        await notifier.send_text(f"Job *{job_id}* not found or expired.", chat_id)
        return

    await notifier.send_text(f"*{job.title}*\n\nOpen in Discord:\n{job.message_url}", chat_id)


async def main():
    args = parse_args()
    config = Config.from_env()

    log_level = "DEBUG" if args.debug else config.log_level
    setup_logging(log_level)

    logger.info("Starting Discord Job Monitor...")
    logger.info(f"Monitoring {len(config.server_ids)} server(s)")
    if args.debug:
        logger.info("DEBUG mode enabled — verbose logging active")
    if args.dry_run:
        logger.info("DRY-RUN mode — jobs will be detected but NOT sent to Telegram")
    if args.log_all_messages:
        logger.info("LOG-ALL-MESSAGES mode — every message will be logged")

    prefilter = PreFilter(config)
    classifier = JobClassifier(config)
    notifier = TelegramNotifier(config)
    gateway = DiscordGateway(config.discord_token, config.server_ids)

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Start Telegram reply listener in background
    reply_task = asyncio.create_task(
        telegram_reply_listener(notifier, shutdown_event)
    )

    reconnect_attempts = 0

    while not shutdown_event.is_set() and reconnect_attempts < config.max_reconnect_attempts:
        try:
            if gateway.can_resume:
                logger.info("Resuming Discord Gateway session...")
                await gateway.resume()
            else:
                logger.info("Connecting to Discord Gateway...")
                await gateway.connect()

            reconnect_attempts = 0  # Reset on successful connection

            async for message in gateway.listen():
                if shutdown_event.is_set():
                    break

                author = message.get("author", {}).get("username", "?")
                channel = message.get("_channel_name", "?")
                server = message.get("_server_name", "?")
                content = message.get("content", "")

                # Log every message if flag is set
                if args.log_all_messages:
                    preview = content[:80].replace("\n", " ")
                    logger.debug(f"[MSG] {server}/#{channel} | {author}: {preview}")

                # Step 1: Pre-filter
                if not prefilter.should_classify(message):
                    continue

                logger.info(f"Pre-filter passed: {author} in #{channel}")
                if args.debug:
                    logger.debug(f"  Content: {content[:200]}")

                # Step 2: Classify with Groq
                job = await classifier.classify(message)
                if job is None:
                    logger.debug(f"Not a dev job, skipping: {author} in #{channel}")
                    continue

                logger.info(f"JOB DETECTED: {job.title} ({job.job_type})")
                if args.debug:
                    logger.debug(f"  Skills: {', '.join(job.skills)}")
                    logger.debug(f"  Pay: {job.pay or 'N/A'}")
                    logger.debug(f"  Contact: {job.contact_info or 'N/A'}")
                    logger.debug(f"  Link: {job.message_url}")

                # Step 3: Send Telegram notification
                if args.dry_run:
                    logger.info(f"[DRY-RUN] Would send Telegram: {job.title}")
                else:
                    await notifier.notify(job)

        except ConnectionError as e:
            reconnect_attempts += 1
            delay = min(config.reconnect_delay * reconnect_attempts, 60)
            logger.warning(
                f"Connection lost: {e} — "
                f"reconnecting in {delay}s ({reconnect_attempts}/{config.max_reconnect_attempts})"
            )
            await gateway.close()
            await asyncio.sleep(delay)

        except Exception as e:
            reconnect_attempts += 1
            logger.error(f"Unexpected error: {e}", exc_info=True)
            await gateway.close()
            await asyncio.sleep(config.reconnect_delay)

    if reconnect_attempts >= config.max_reconnect_attempts:
        logger.critical("Max reconnection attempts reached. Exiting.")

    # Cleanup
    reply_task.cancel()
    try:
        await reply_task
    except asyncio.CancelledError:
        pass
    await gateway.close()
    logger.info("Discord Job Monitor stopped.")


if __name__ == "__main__":
    asyncio.run(main())
