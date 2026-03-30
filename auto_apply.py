"""
Auto-apply module: generates a human-like application message using Groq
and sends it as a Discord DM to the job poster.
"""

import logging
import ssl

import aiohttp
import certifi
from groq import AsyncGroq

from models import JobPosting
from config import Config

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"

APPLICATION_PROMPT = """Write a short, high-converting Discord DM to apply for this freelance job.
You are Manas — a full stack developer who can ship fast. Your stack:
- Frontend: React, Next.js, Vue, Tailwind CSS, TypeScript
- Backend: Node.js, Python, Django, Flask, Express, REST APIs, GraphQL
- Databases: PostgreSQL, MongoDB, Redis, Firebase
- Automation: Web scraping, bots, browser automation, scripting
- DevOps: Docker, AWS, CI/CD, Vercel, Railway
- Other: Payment integrations, real-time apps (WebSocket), SaaS products

Rules for the message:
- Keep it 60-100 words MAX — busy clients skip long messages
- Sound like a real person on Discord, NOT an AI or template
- Open with something specific about THEIR project — show you actually read their post
- Drop 1-2 concrete relevant skills/past work that match what they need
- Create urgency or show availability ("I can start today", "I've got bandwidth this week")
- End with ONE clear next step ("want to hop on a quick call?" or "I can send a quick mockup/prototype")
- NO emojis, NO "Dear", NO "Best regards", NO bullet points
- Vary the opening — don't always start with "Hey" (use "Hey", "Yo", "Hi there", or jump straight in)
- If pay is mentioned, don't discuss it — just show value
- Sound confident, not desperate

Job details:
Title: {title}
Description: {description}
Skills needed: {skills}
Pay: {pay}
Original message snippet: {snippet}

Write ONLY the DM message, nothing else."""


class AutoApply:
    def __init__(self, config: Config):
        self._discord_token = config.discord_token
        self._groq_client = AsyncGroq(api_key=config.groq_api_key)
        self._groq_model = config.groq_model

    async def apply(self, job: JobPosting) -> bool:
        """Generate application message and send as Discord DM to the job poster."""
        if not job.source_author_id:
            logger.error(f"No author ID for job {job.title}, can't send DM")
            return False

        # Step 1: Generate personalized message
        message = await self._generate_message(job)
        if not message:
            logger.error("Failed to generate application message")
            return False

        logger.info(f"Generated application for '{job.title}':\n  {message[:100]}...")

        # Step 2: Send Discord DM
        success = await self._send_discord_dm(job.source_author_id, message)
        if success:
            logger.info(f"Application sent to {job.source_author} for '{job.title}'")
        return success

    async def _generate_message(self, job: JobPosting) -> str | None:
        """Use Groq to generate a human-like application message."""
        skills_str = ", ".join(job.skills) if job.skills else "Not specified"

        prompt = APPLICATION_PROMPT.format(
            title=job.title,
            description=job.description,
            skills=skills_str,
            pay=job.pay or "Not mentioned",
            snippet=job.raw_snippet[:300],
        )

        try:
            response = await self._groq_client.chat.completions.create(
                model=self._groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq message generation failed: {e}")
            return None

    async def _send_discord_dm(self, user_id: str, message: str) -> bool:
        """Open a DM channel with a user and send a message."""
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        conn = aiohttp.TCPConnector(ssl=ssl_ctx)
        headers = {
            "Authorization": self._discord_token,
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession(connector=conn) as session:
                # Step 1: Create/open DM channel
                dm_url = f"{DISCORD_API}/users/@me/channels"
                dm_payload = {"recipient_id": user_id}

                async with session.post(
                    dm_url, json=dm_payload, headers=headers, ssl=ssl_ctx
                ) as resp:
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        logger.error(f"Failed to open DM channel: HTTP {resp.status} - {body}")
                        return False
                    dm_data = await resp.json()
                    channel_id = dm_data["id"]

                # Step 2: Send message
                msg_url = f"{DISCORD_API}/channels/{channel_id}/messages"
                msg_payload = {"content": message}

                async with session.post(
                    msg_url, json=msg_payload, headers=headers, ssl=ssl_ctx
                ) as resp:
                    if resp.status in (200, 201):
                        return True
                    body = await resp.text()
                    logger.error(f"Failed to send DM: HTTP {resp.status} - {body}")
                    return False

        except Exception as e:
            logger.error(f"Discord DM error: {e}")
            return False
