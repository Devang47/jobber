"""
Fetch messages from the last 24 hours across all monitored servers,
classify them with Groq, and send matching jobs to Telegram.
"""

import asyncio
import ssl
import json
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import certifi
from groq import AsyncGroq

from config import Config
from prefilter import PreFilter
from classifier import JobClassifier
from notifier import TelegramNotifier
from logger_setup import setup_logging

logger = logging.getLogger("fetch_recent")

DISCORD_API = "https://discord.com/api/v10"


def snowflake_from_time(dt: datetime) -> int:
    """Convert a datetime to a Discord snowflake ID for message filtering."""
    discord_epoch = 1420070400000  # ms
    ts_ms = int(dt.timestamp() * 1000)
    return (ts_ms - discord_epoch) << 22


async def get_guild_channels(session: aiohttp.ClientSession, token: str, guild_id: int, ssl_ctx) -> list[dict]:
    """Fetch text channels from a guild."""
    url = f"{DISCORD_API}/guilds/{guild_id}/channels"
    headers = {"Authorization": token}
    try:
        async with session.get(url, headers=headers, ssl=ssl_ctx) as resp:
            if resp.status == 200:
                channels = await resp.json()
                # Type 0 = text channel, type 5 = announcement
                return [ch for ch in channels if ch.get("type") in (0, 5)]
            else:
                logger.warning(f"Failed to get channels for guild {guild_id}: HTTP {resp.status}")
                return []
    except Exception as e:
        logger.error(f"Error fetching channels for guild {guild_id}: {e}")
        return []


async def get_messages_after(session: aiohttp.ClientSession, token: str, channel_id: int, after_snowflake: int, ssl_ctx) -> list[dict]:
    """Fetch messages from a channel after a given snowflake ID."""
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    headers = {"Authorization": token}
    params = {"after": str(after_snowflake), "limit": 100}
    all_messages = []

    try:
        # Paginate to get all messages (max 100 per request)
        while True:
            async with session.get(url, headers=headers, params=params, ssl=ssl_ctx) as resp:
                if resp.status == 200:
                    messages = await resp.json()
                    if not messages:
                        break
                    all_messages.extend(messages)
                    if len(messages) < 100:
                        break
                    # Next page: get messages after the newest one
                    params["after"] = messages[0]["id"]
                    await asyncio.sleep(0.5)  # Rate limit respect
                elif resp.status == 403:
                    # No permission for this channel, skip silently
                    break
                elif resp.status == 429:
                    retry_after = (await resp.json()).get("retry_after", 5)
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                else:
                    logger.warning(f"Channel {channel_id}: HTTP {resp.status}")
                    break
    except Exception as e:
        logger.error(f"Error fetching messages from channel {channel_id}: {e}")

    return all_messages


async def main():
    config = Config.from_env()
    setup_logging(config.log_level)

    logger.info("=== Fetching last 24 hours of messages ===")

    prefilter = PreFilter(config)
    classifier = JobClassifier(config)
    notifier = TelegramNotifier(config)

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    after_snowflake = snowflake_from_time(cutoff)

    jobs_found = []
    total_messages = 0
    total_filtered = 0

    async with aiohttp.ClientSession(connector=conn) as session:
        for guild_id in config.server_ids:
            logger.info(f"Fetching channels for server {guild_id}...")
            channels = await get_guild_channels(session, config.discord_token, guild_id, ssl_ctx)
            logger.info(f"  Found {len(channels)} text channels")

            for channel in channels:
                ch_id = int(channel["id"])
                ch_name = channel.get("name", str(ch_id))

                messages = await get_messages_after(session, config.discord_token, ch_id, after_snowflake, ssl_ctx)
                if not messages:
                    continue

                total_messages += len(messages)
                logger.info(f"  #{ch_name}: {len(messages)} messages")

                for msg in messages:
                    msg["_server_name"] = str(guild_id)
                    msg["_channel_name"] = ch_name
                    msg["guild_id"] = str(guild_id)

                    if not prefilter.should_classify(msg):
                        continue

                    total_filtered += 1
                    logger.info(f"    Pre-filter passed: {msg.get('author', {}).get('username')} in #{ch_name}")

                    job = await classifier.classify(msg)
                    if job:
                        jobs_found.append(job)
                        logger.info(f"    JOB FOUND: {job.title}")

                    await asyncio.sleep(0.5)  # Groq rate limit

    logger.info(f"\n=== Results ===")
    logger.info(f"Total messages scanned: {total_messages}")
    logger.info(f"Passed pre-filter: {total_filtered}")
    logger.info(f"Jobs found: {len(jobs_found)}")

    if not jobs_found:
        logger.info("No remote freelancing jobs found in the last 24 hours.")
        # Send a summary to Telegram
        await send_summary(notifier, total_messages, 0)
        return

    # Send each job to Telegram
    for i, job in enumerate(jobs_found, 1):
        logger.info(f"Sending job {i}/{len(jobs_found)} to Telegram: {job.title}")
        await notifier.notify(job)
        await asyncio.sleep(2)  # Don't spam Telegram

    await send_summary(notifier, total_messages, len(jobs_found))


async def send_summary(notifier, total_messages, jobs_count):
    """Send a summary message to Telegram."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = (
        f"*Discord Job Monitor - 24h Scan Complete*\n\n"
        f"Messages scanned: {total_messages}\n"
        f"Jobs found & sent: {jobs_count}\n"
        f"Scan time: {now}"
    )
    await notifier.send_text(msg)


if __name__ == "__main__":
    asyncio.run(main())
