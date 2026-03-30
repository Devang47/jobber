import argparse
import asyncio
import logging
import re
import signal

from config import Config
from discord_gateway import DiscordGateway
from prefilter import PreFilter
from classifier import JobClassifier
from notifier import WhatsAppNotifier
from auto_apply import AutoApply
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
        help="Classify jobs but don't send WhatsApp notifications",
    )
    parser.add_argument(
        "--log-all-messages", action="store_true",
        help="Log every incoming message (not just pre-filtered ones)",
    )
    return parser.parse_args()


async def whatsapp_reply_listener(notifier: WhatsAppNotifier, auto_apply: AutoApply, shutdown_event: asyncio.Event):
    """Poll Green API for incoming WhatsApp replies and handle apply/open commands."""
    logger.info("WhatsApp reply listener started — listening for 'apply' commands")

    while not shutdown_event.is_set():
        try:
            notification = await notifier.receive_notification()

            if notification is None:
                await asyncio.sleep(3)
                continue

            receipt_id = notification.get("receiptId")
            body = notification.get("body", {})
            type_webhook = body.get("typeWebhook", "")

            # Handle incoming text messages
            if type_webhook == "incomingMessageReceived":
                msg_data = body.get("messageData", {})

                # Text message reply
                text = ""
                if msg_data.get("typeMessage") == "textMessage":
                    text = msg_data.get("textMessageData", {}).get("textMessage", "")
                elif msg_data.get("typeMessage") == "extendedTextMessage":
                    text = msg_data.get("extendedTextMessageData", {}).get("text", "")
                elif msg_data.get("typeMessage") == "buttonsResponseMessage":
                    # Button click
                    btn_data = msg_data.get("buttonsResponseMessage", {})
                    text = btn_data.get("selectedButtonId", "")

                text = text.strip().lower()

                # Match "apply <job_id>" or "apply_<job_id>"
                apply_match = re.match(r"apply[_ ]?([a-f0-9]{6})", text)
                open_match = re.match(r"open[_ ]?([a-f0-9]{6})", text)

                if apply_match:
                    job_id = apply_match.group(1)
                    await handle_apply(notifier, auto_apply, job_id)

                elif open_match:
                    job_id = open_match.group(1)
                    await handle_open(notifier, job_id)

            # Always acknowledge the notification
            if receipt_id:
                await notifier.delete_notification(receipt_id)

        except Exception as e:
            logger.error(f"Reply listener error: {e}")
            await asyncio.sleep(5)


async def handle_apply(notifier: WhatsAppNotifier, auto_apply: AutoApply, job_id: str):
    """Handle an apply command from WhatsApp."""
    job = notifier.pending_jobs.get(job_id)
    if not job:
        await notifier.send_text(f"Job *{job_id}* not found or expired. It may have been from a previous session.")
        return

    await notifier.send_text(f"Applying to *{job.title}*... generating your message and sending DM to {job.source_author} on Discord.")

    success = await auto_apply.apply(job)

    if success:
        await notifier.send_text(
            f"*Applied successfully!*\n\n"
            f"DM sent to *{job.source_author}* on Discord for the *{job.title}* position.\n"
            f"Check your Discord DMs to continue the conversation."
        )
    else:
        await notifier.send_text(
            f"*Failed to apply.*\n\n"
            f"Couldn't send DM to {job.source_author}. They may have DMs disabled.\n"
            f"Try reaching out manually: {job.message_url}"
        )


async def handle_open(notifier: WhatsAppNotifier, job_id: str):
    """Handle an open command from WhatsApp."""
    job = notifier.pending_jobs.get(job_id)
    if not job:
        await notifier.send_text(f"Job *{job_id}* not found or expired.")
        return

    await notifier.send_text(f"*{job.title}*\n\nOpen in Discord:\n{job.message_url}")


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
        logger.info("DRY-RUN mode — jobs will be detected but NOT sent to WhatsApp")
    if args.log_all_messages:
        logger.info("LOG-ALL-MESSAGES mode — every message will be logged")

    prefilter = PreFilter(config)
    classifier = JobClassifier(config)
    notifier = WhatsAppNotifier(config)
    auto_apply = AutoApply(config)
    gateway = DiscordGateway(config.discord_token, config.server_ids)

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Start WhatsApp reply listener in background
    reply_task = asyncio.create_task(
        whatsapp_reply_listener(notifier, auto_apply, shutdown_event)
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

                # Step 3: Send WhatsApp notification
                if args.dry_run:
                    logger.info(f"[DRY-RUN] Would send WhatsApp: {job.title}")
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
