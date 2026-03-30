import asyncio
import logging
import ssl
import uuid
from datetime import datetime

import aiohttp
import certifi

from models import JobPosting
from config import Config

logger = logging.getLogger(__name__)


class WhatsAppNotifier:
    """Send WhatsApp notifications to multiple numbers via Green API."""

    def __init__(self, config: Config):
        self._phones = [p.replace("+", "").replace("-", "") for p in config.whatsapp_phones]
        self._instance_id = config.greenapi_instance_id
        self._token = config.greenapi_token
        self._base_url = f"https://api.green-api.com/waInstance{self._instance_id}"
        self.pending_jobs: dict[str, JobPosting] = {}

    def _ssl_ctx(self):
        return ssl.create_default_context(cafile=certifi.where())

    async def _send_to_phone(self, phone: str, text: str):
        """Send a message to a single phone number."""
        ssl_ctx = self._ssl_ctx()
        conn = aiohttp.TCPConnector(ssl=ssl_ctx)
        url = f"{self._base_url}/sendMessage/{self._token}"
        clean = phone.replace("+", "").replace("-", "")
        try:
            async with aiohttp.ClientSession(connector=conn) as session:
                payload = {"chatId": f"{clean}@c.us", "message": text}
                async with session.post(url, json=payload, ssl=ssl_ctx) as resp:
                    if resp.status != 200:
                        logger.error(f"Send to {clean} failed: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Send to {clean} failed: {e}")

    async def send_text(self, text: str, target_phones: list[str] | None = None) -> bool:
        """Send a message. If target_phones given, send only to those; otherwise send to all."""
        phones = target_phones or self._phones
        for phone in phones:
            await self._send_to_phone(phone, text)
        return True

    async def send_job_with_proposal(self, job_card: str, proposal: str, target_phones: list[str] | None = None):
        """Send job card, then proposal as separate copyable message."""
        await self.send_text(job_card, target_phones)
        await asyncio.sleep(0.5)
        await self.send_text(f"📋 *Copy & send this:*\n\n{proposal}", target_phones)

    async def notify(self, job: JobPosting) -> bool:
        """Send a Discord job notification."""
        job_id = uuid.uuid4().hex[:6]
        job.job_id = job_id
        self.pending_jobs[job_id] = job
        message = self._format_message(job)
        await self.send_text(message)
        logger.info(f"Sent: {job.title} [{job_id}]")
        return True

    async def receive_notification(self) -> dict | None:
        ssl_ctx = self._ssl_ctx()
        conn = aiohttp.TCPConnector(ssl=ssl_ctx)
        url = f"{self._base_url}/receiveNotification/{self._token}"
        try:
            async with aiohttp.ClientSession(connector=conn) as session:
                async with session.get(url, ssl=ssl_ctx) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return None

    async def delete_notification(self, receipt_id: int) -> None:
        ssl_ctx = self._ssl_ctx()
        conn = aiohttp.TCPConnector(ssl=ssl_ctx)
        url = f"{self._base_url}/deleteNotification/{self._token}/{receipt_id}"
        try:
            async with aiohttp.ClientSession(connector=conn) as session:
                await session.delete(url, ssl=ssl_ctx)
        except Exception:
            pass

    def _format_message(self, job: JobPosting) -> str:
        skills_str = ", ".join(job.skills) if job.skills else "Not specified"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            "*NEW DEV JOB FOUND*",
            "",
            f"*Title:* {job.title}",
            f"*Type:* {job.job_type}",
            f"*Pay:* {job.pay or 'Not specified'}",
            f"*Skills:* {skills_str}",
            "",
            f"*Description:* {job.description}",
        ]
        if job.deadline:
            lines.append(f"*Deadline:* {job.deadline}")
        if job.contact_info:
            lines.append(f"*Contact:* {job.contact_info}")
        if job.experience_level:
            lines.append(f"*Experience:* {job.experience_level}")
        lines.extend([
            "",
            f"*Source:* {job.source_server} > #{job.source_channel}",
            f"*Posted by:* {job.source_author}",
            f"*Link:* {job.message_url}",
            "",
            f"_Detected at {now}_",
        ])
        return "\n".join(lines)
