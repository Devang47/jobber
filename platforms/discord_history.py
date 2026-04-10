import asyncio
import logging
import ssl
from datetime import datetime, timedelta, timezone

try:
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - exercised only in dependency-light test envs
    aiohttp = None

try:
    import certifi
except ModuleNotFoundError:  # pragma: no cover - exercised only in dependency-light test envs
    certifi = None

from api_logger import log_api_event
from classifier import JobClassifier
from config import Config
from platforms.base import PlatformJob
from prefilter import PreFilter


logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"


def _ssl_context() -> ssl.SSLContext:
    if certifi is None:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _ensure_http_dependencies() -> None:
    if aiohttp is None:
        raise RuntimeError("aiohttp is required to fetch Discord history")


def snowflake_from_time(dt: datetime) -> int:
    discord_epoch = 1420070400000
    ts_ms = int(dt.timestamp() * 1000)
    return (ts_ms - discord_epoch) << 22


async def get_guild_channels(session, token: str, guild_id: int, ssl_ctx) -> list[dict]:
    url = f"{DISCORD_API}/guilds/{guild_id}/channels"
    headers = {"Authorization": token}
    try:
        async with session.get(url, headers=headers, ssl=ssl_ctx) as resp:
            data = await resp.json(content_type=None) if resp.status == 200 else None
            log_api_event(
                "discord",
                "guild_channels",
                resp.status,
                payload=data,
                guild_id=guild_id,
            )
            if resp.status == 200 and isinstance(data, list):
                return [channel for channel in data if channel.get("type") in (0, 5)]
    except Exception as exc:
        log_api_event("discord", "guild_channels", "exception", guild_id=guild_id, error=str(exc))
        logger.error(f"Error fetching channels for guild {guild_id}: {exc}")
    return []


async def get_messages_after(session, token: str, channel_id: int, after_snowflake: int, ssl_ctx) -> list[dict]:
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    headers = {"Authorization": token}
    params = {"after": str(after_snowflake), "limit": 100}
    all_messages: list[dict] = []

    try:
        while True:
            async with session.get(url, headers=headers, params=params, ssl=ssl_ctx) as resp:
                if resp.status == 200:
                    payload = await resp.json(content_type=None)
                    log_api_event(
                        "discord",
                        "channel_messages",
                        resp.status,
                        payload=payload,
                        channel_id=channel_id,
                    )
                    if not payload:
                        break
                    all_messages.extend(payload)
                    if len(payload) < 100:
                        break
                    params["after"] = payload[0]["id"]
                    await asyncio.sleep(0.3)
                elif resp.status == 403:
                    log_api_event("discord", "channel_messages", resp.status, channel_id=channel_id)
                    break
                elif resp.status == 429:
                    payload = await resp.json(content_type=None)
                    retry_after = payload.get("retry_after", 5)
                    log_api_event(
                        "discord",
                        "channel_messages",
                        resp.status,
                        payload=payload,
                        channel_id=channel_id,
                    )
                    await asyncio.sleep(retry_after)
                else:
                    log_api_event("discord", "channel_messages", resp.status, channel_id=channel_id)
                    break
    except Exception as exc:
        log_api_event("discord", "channel_messages", "exception", channel_id=channel_id, error=str(exc))
        logger.error(f"Error fetching messages from channel {channel_id}: {exc}")

    return all_messages


async def fetch_discord_jobs(config: Config, seen_ids: set[str]) -> list[PlatformJob]:
    _ensure_http_dependencies()

    ssl_ctx = _ssl_context()
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    after_snowflake = snowflake_from_time(cutoff)

    prefilter = PreFilter(config)
    classifier = JobClassifier(config)
    jobs: list[PlatformJob] = []

    async with aiohttp.ClientSession(connector=conn) as session:
        for guild_id in config.server_ids:
            channels = await get_guild_channels(session, config.discord_token, guild_id, ssl_ctx)
            for channel in channels:
                channel_id = int(channel["id"])
                channel_name = channel.get("name", str(channel_id))
                messages = await get_messages_after(
                    session,
                    config.discord_token,
                    channel_id,
                    after_snowflake,
                    ssl_ctx,
                )
                for message in messages:
                    message_id = str(message.get("id", ""))
                    if not message_id or message_id in seen_ids:
                        continue

                    message["_server_name"] = str(guild_id)
                    message["_channel_name"] = channel_name
                    message["guild_id"] = str(guild_id)

                    if not prefilter.should_classify(message):
                        continue

                    seen_ids.add(message_id)
                    classified = await classifier.classify(message)
                    if classified is None:
                        continue

                    jobs.append(
                        PlatformJob(
                            platform="discord",
                            title=classified.title,
                            description=classified.description,
                            skills=classified.skills,
                            budget=classified.pay,
                            job_type=classified.job_type,
                            url=classified.message_url,
                            posted_by=classified.source_author,
                            posted_time=message.get("timestamp"),
                            location=classified.source_server,
                            job_id=message_id,
                        )
                    )
    log_api_event(
        "discord",
        "history_run",
        "success",
        payload={"jobs_found": len(jobs)},
        servers=len(config.server_ids),
    )
    return jobs
