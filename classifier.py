import json
import logging
import asyncio

from groq import AsyncGroq

from models import JobPosting
from config import Config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a SOFTWARE DEVELOPER job classifier. You analyze Discord messages and determine if they contain a remote freelancing job specifically for SOFTWARE DEVELOPERS.

ONLY match jobs that require coding/development skills such as:
- Full stack, frontend, or backend development
- Web development (React, Next.js, Vue, Angular, etc.)
- Backend (Node.js, Python, Django, Flask, Laravel, etc.)
- Mobile development (React Native, Flutter, etc.)
- Automation, scripting, bot development, web scraping
- DevOps, CI/CD, cloud infrastructure
- Database, API development
- SaaS/webapp building

REJECT and return {"is_job": false} for:
- Non-technical jobs (virtual assistant, data entry, sales, social media, design-only, moderation, etc.)
- Jobs seeking someone to work for them (not hire)
- Vague "remote worker" or "personal assistant" posts
- Partnership/MLM/commission-only offers
- Anything that doesn't require writing code

Response format (strict JSON, no markdown, no code fences):
{
    "is_job": true,
    "title": "Brief job title",
    "description": "1-2 sentence summary of the role",
    "skills": ["skill1", "skill2"],
    "pay": "Budget or rate if mentioned, else null",
    "deadline": "Application deadline if mentioned, else null",
    "contact_info": "How to apply - DM, email, link, etc. if mentioned, else null",
    "job_type": "freelance|contract|part-time|full-time",
    "experience_level": "junior|mid|senior|any if mentioned, else null",
    "raw_snippet": "First 200 chars of the original message"
}"""

USER_PROMPT_TEMPLATE = """Analyze this Discord message. Is it a remote freelancing job that requires SOFTWARE DEVELOPMENT skills (coding, web dev, automation, etc.)?

Server: {server_name}
Channel: #{channel_name}
Author: {author}
Message:
---
{content}
---

Respond with JSON only."""


class JobClassifier:
    def __init__(self, config: Config):
        self._client = AsyncGroq(api_key=config.groq_api_key)
        self._model = config.groq_model

    async def classify(self, message: dict) -> JobPosting | None:
        """Send message to Groq API for classification. Returns JobPosting if matched."""
        content = message.get("content", "")
        author = message.get("author", {}).get("username", "unknown")

        user_prompt = USER_PROMPT_TEMPLATE.format(
            server_name=message.get("_server_name", "Unknown"),
            channel_name=message.get("_channel_name", "Unknown"),
            author=author,
            content=content[:2000],
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=1024,
            )

            result_text = response.choices[0].message.content.strip()
            result = json.loads(result_text)

            if not result.get("is_job"):
                return None

            return JobPosting(
                title=result.get("title", "Untitled"),
                description=result.get("description", ""),
                skills=result.get("skills", []),
                pay=result.get("pay"),
                deadline=result.get("deadline"),
                contact_info=result.get("contact_info"),
                job_type=result.get("job_type", "freelance"),
                experience_level=result.get("experience_level"),
                raw_snippet=result.get("raw_snippet", content[:200]),
                source_server=message.get("_server_name", "Unknown"),
                source_channel=message.get("_channel_name", "Unknown"),
                source_author=author,
                source_author_id=message.get("author", {}).get("id", ""),
                message_url=self._build_message_url(message),
            )

        except json.JSONDecodeError:
            logger.warning("Groq returned invalid JSON, skipping message")
            return None
        except Exception as e:
            if "rate_limit" in str(e).lower():
                logger.warning("Groq rate limited, backing off 10s...")
                await asyncio.sleep(10)
                return None
            logger.error(f"Classification error: {e}")
            return None

    def _build_message_url(self, message: dict) -> str:
        guild_id = message.get("guild_id", "")
        channel_id = message.get("channel_id", "")
        message_id = message.get("id", "")
        return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
