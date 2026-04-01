"""
Fetch dev jobs from last 24h, generate personalized messages,
and send everything to Telegram so the user can apply manually.
"""

import asyncio
import re
import ssl
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import certifi
from groq import AsyncGroq

from config import Config
from notifier import TelegramNotifier
from logger_setup import setup_logging

logger = logging.getLogger("mass_apply")

DISCORD_API = "https://discord.com/api/v10"

JOB_CHANNEL_KEYWORDS = [
    "job", "hire", "hiring", "work", "gig", "freelance", "project",
    "looking-for", "commission", "paid", "dev", "developer", "code",
    "request", "opportunity", "position", "vacancy", "contract",
    "outsource", "need", "seeking", "wanted",
]

DEV_KEYWORDS = re.compile(
    r"(developer|frontend|backend|full.?stack|react|next\.?js|node\.?js|python|javascript|"
    r"typescript|web\s*dev|software|engineer|api|database|devops|automation|scraping|"
    r"bot|webapp|saas|mern|django|flask|laravel|vue|angular|tailwind|postgres|mongodb|"
    r"aws|docker|html|css|php|java\b|golang|rust|ruby|wordpress|shopify|"
    r"mobile\s*app|react\s*native|flutter|ios\s*dev|android\s*dev|"
    r"website|web\s*app|landing\s*page|dashboard|crud|restful|graphql|"
    r"github|deploy|server|cloud|linux|figma.to.code|psd.to)",
    re.IGNORECASE,
)

HIRING_KEYWORDS = re.compile(
    r"(hiring|looking\s*for|need\s*a|seeking|want\s*a|searching\s*for|"
    r"require|needed|build\s*(me|us|my)|create\s*(me|us|my)|"
    r"help\s*(me|us)|budget|pay|rate|\$\d|usd|per\s*hour|dm\s*me|"
    r"apply|contact|reach\s*out|interested|position|opening|vacancy)",
    re.IGNORECASE,
)

# Filter out self-promotion (devs advertising themselves, NOT hiring)
SELF_PROMO = re.compile(
    r"("
    # "I am a developer", "I'm a full stack dev", "I'm a freelancer"
    r"i\s*('m|am)\s*.{0,50}(developer|dev|engineer|freelancer|designer|coder|programmer)\b|"
    r"i\s*am\s*.{0,50}(developer|dev|engineer|freelancer|designer|coder|programmer)\b|"
    # "I can build", "I will build", "I will create", "I can develop", "I will develop"
    r"i\s*(can|will|would)\s*(build|create|develop|design|make|code|deliver|help\s*you)|"
    # "I specialize", "I offer", "I provide"
    r"i\s*(specialize|offer|provide|focus\s*on|work\s*with)|"
    # "Hire me", "my portfolio", "my services", "check out my"
    r"hire\s*me|my\s*(portfolio|services|work|github|website|rates?)|"
    r"check\s*(out\s*)?my|here\s*('s|is)\s*my|look\s*at\s*my|"
    # "I'm available", "I'm open to", "I'm looking for work/clients"
    r"i\s*('m|am)\s*(available|open\s*to|looking\s*for\s*(work|client|project|gig|opportunity))|"
    # "I need a client", "I'm seeking clients"
    r"i\s*(need|want)\s*(a\s*)?(client|project|work|gig)|"
    # "DM me for services", "Contact me for"
    r"(dm|contact|message|reach)\s*(me|out)\s*(for|if\s*you\s*need)|"
    # "I have X years experience"
    r"i\s*have\s*\d+\s*(year|yr)|"
    # "my expertise", "my experience", "my stack", "my skills"
    r"my\s*(expertise|experience|stack|skills|tech\s*stack)"
    r")",
    re.IGNORECASE,
)

APPLICATION_PROMPT = """Write a short Discord DM to apply for this freelance dev job.
You are Manas — full stack dev (React, Next.js, Node.js, Python, automation, bots, web scraping, DevOps).

Rules:
- 60-100 words MAX
- Sound human, NOT AI — this is Discord
- Reference something specific about their project
- Mention 1-2 matching skills from your stack
- Show availability ("can start today", "got bandwidth this week")
- End with ONE clear next step
- NO emojis, NO "Dear Sir", NO "Best regards", NO bullet points
- Vary the tone — casual but professional
- Sound confident, not desperate

Job post:
{content}

Write ONLY the DM, nothing else."""


def snowflake_from_time(dt: datetime) -> int:
    discord_epoch = 1420070400000
    ts_ms = int(dt.timestamp() * 1000)
    return (ts_ms - discord_epoch) << 22


def is_job_channel(name: str) -> bool:
    return any(kw in name.lower() for kw in JOB_CHANNEL_KEYWORDS)


def is_dev_hiring_post(content: str) -> bool:
    """Return True only if someone is HIRING a developer (not self-promotion)."""
    if len(content) < 40:
        return False
    if SELF_PROMO.search(content):
        return False
    return bool(DEV_KEYWORDS.search(content)) and bool(HIRING_KEYWORDS.search(content))


async def get_guild_channels(session, token, guild_id, ssl_ctx):
    url = f"{DISCORD_API}/guilds/{guild_id}/channels"
    headers = {"Authorization": token}
    try:
        async with session.get(url, headers=headers, ssl=ssl_ctx) as resp:
            if resp.status == 200:
                return [ch for ch in (await resp.json()) if ch.get("type") in (0, 5)]
            return []
    except Exception:
        return []


async def get_messages_after(session, token, channel_id, after_snowflake, ssl_ctx):
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    headers = {"Authorization": token}
    params = {"after": str(after_snowflake), "limit": 100}
    all_msgs = []
    try:
        while True:
            async with session.get(url, headers=headers, params=params, ssl=ssl_ctx) as resp:
                if resp.status == 200:
                    msgs = await resp.json()
                    if not msgs:
                        break
                    all_msgs.extend(msgs)
                    if len(msgs) < 100:
                        break
                    params["after"] = msgs[0]["id"]
                    await asyncio.sleep(0.5)
                elif resp.status == 429:
                    await asyncio.sleep((await resp.json()).get("retry_after", 5))
                else:
                    break
    except Exception:
        pass
    return all_msgs


async def generate_message(groq_client, content: str) -> str | None:
    prompt = APPLICATION_PROMPT.format(content=content[:500])
    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip().strip('"')
    except Exception as e:
        logger.error(f"Message generation failed: {e}")
        return None


async def main():
    config = Config.from_env()
    setup_logging("INFO")

    logger.info("=" * 60)
    logger.info("  FETCHING DEV JOBS + GENERATING APPLY MESSAGES")
    logger.info("=" * 60)

    notifier = TelegramNotifier(config)
    groq_client = AsyncGroq(api_key=config.groq_api_key)

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    after_snowflake = snowflake_from_time(cutoff)

    await notifier.send_text(
        "*Job Hunt Started*\n\n"
        "Scanning all servers for dev jobs posted in the last 24h...\n"
        "Will send each job with a ready-to-paste application message."
    )

    # Phase 1: Collect dev hiring posts
    jobs = []
    seen_authors = set()
    total_messages = 0

    async with aiohttp.ClientSession(connector=conn) as session:
        for guild_id in config.server_ids:
            channels = await get_guild_channels(session, config.discord_token, guild_id, ssl_ctx)
            job_channels = [ch for ch in channels if is_job_channel(ch.get("name", ""))]
            if not job_channels:
                job_channels = channels

            for channel in job_channels:
                ch_id = int(channel["id"])
                ch_name = channel.get("name", str(ch_id))
                messages = await get_messages_after(session, config.discord_token, ch_id, after_snowflake, ssl_ctx)
                if not messages:
                    continue
                total_messages += len(messages)

                for msg in messages:
                    if msg.get("author", {}).get("bot", False):
                        continue
                    content = msg.get("content", "")
                    author = msg.get("author", {}).get("username", "?")
                    author_id = msg.get("author", {}).get("id", "")

                    if not author_id or author_id in seen_authors:
                        continue
                    if not is_dev_hiring_post(content):
                        continue

                    seen_authors.add(author_id)
                    message_url = f"https://discord.com/channels/{guild_id}/{ch_id}/{msg['id']}"

                    jobs.append({
                        "content": content,
                        "author": author,
                        "channel": ch_name,
                        "url": message_url,
                    })
                    logger.info(f"  HIRING POST: {author} in #{ch_name}")

    logger.info(f"\nScanned {total_messages} messages, found {len(jobs)} dev hiring posts")

    if not jobs:
        await notifier.send_text(
            f"*Scan Complete*\n\n"
            f"Scanned {total_messages} messages.\n"
            f"No developer hiring posts found in the last 24 hours.\n"
            f"The live monitor is still running — you'll get alerts for new ones."
        )
        return

    await notifier.send_text(
        f"*Found {len(jobs)} dev jobs!*\n\n"
        f"Generating personalized messages for each one..."
    )

    # Phase 2: Generate messages and send to Telegram
    sent = 0
    for i, job in enumerate(jobs, 1):
        logger.info(f"[{i}/{len(jobs)}] Generating message for {job['author']}...")

        app_msg = await generate_message(groq_client, job["content"])
        await asyncio.sleep(2)

        if not app_msg:
            continue

        # Format the Telegram notification
        content_preview = job["content"][:300].replace("*", "").replace("_", "")
        telegram_msg = (
            f"*JOB {i}/{len(jobs)}*\n\n"
            f"*Posted by:* @{job['author']}\n"
            f"*Channel:* #{job['channel']}\n\n"
            f"*Their post:*\n{content_preview}\n\n"
            f"---\n\n"
            f"*Your message (copy & paste):*\n\n"
            f"{app_msg}\n\n"
            f"---\n"
            f"*Open in Discord:* {job['url']}"
        )

        await notifier.send_text(telegram_msg)
        sent += 1
        logger.info(f"  Sent to Telegram!")
        await asyncio.sleep(2)

    # Final summary
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    await notifier.send_text(
        f"*ALL DONE*\n\n"
        f"*Jobs found:* {len(jobs)}\n"
        f"*Messages generated:* {sent}\n\n"
        f"Open each Discord link, DM the poster, paste the message.\n"
        f"Go get that job!\n\n"
        f"_{now}_"
    )

    logger.info(f"\nDONE — {sent} jobs with messages sent to Telegram")


if __name__ == "__main__":
    asyncio.run(main())
