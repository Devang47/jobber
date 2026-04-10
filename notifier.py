from __future__ import annotations

import asyncio
import logging
import ssl
import uuid
from datetime import datetime
from html import escape

try:
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - exercised only in dependency-light test envs
    aiohttp = None

try:
    import certifi
except ModuleNotFoundError:  # pragma: no cover - exercised only in dependency-light test envs
    certifi = None

from api_logger import log_api_event
from models import JobPosting
from config import Config

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"

# Bot commands to register with Telegram
BOT_COMMANDS = [
    {"command": "help", "description": "Show all commands"},
    {"command": "start", "description": "Schedule the core bundle"},
    {"command": "stop", "description": "Stop core or other scheduled platforms"},
    {"command": "scan", "description": "Run an immediate scan"},
    {"command": "status", "description": "Show active scheduled platforms"},
    {"command": "profile", "description": "View your profile"},
    {"command": "set", "description": "Set profile field (name/github/portfolio/rate/skills)"},
    {"command": "users", "description": "List registered users"},
]


def html_escape(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return escape(text, quote=False)


class TelegramNotifier:
    """Send notifications to Telegram chats via Bot API."""

    def __init__(self, config: Config):
        self._token = config.telegram_bot_token
        self._chat_id = config.telegram_chat_id
        self._base_url = TELEGRAM_API.format(token=self._token)
        self._update_offset = 0
        self._session: aiohttp.ClientSession | None = None
        self.pending_jobs: dict[tuple[int, str], object] = {}
        self.pending_proposals: dict[tuple[int, str], str] = {}

    def _ssl_ctx(self):
        if certifi is None:
            return ssl.create_default_context()
        return ssl.create_default_context(cafile=certifi.where())

    def _ensure_http_dependencies(self) -> None:
        if aiohttp is None:
            raise RuntimeError("aiohttp is required for Telegram notifications")

    async def _get_session(self) -> aiohttp.ClientSession:
        self._ensure_http_dependencies()
        if self._session is None or self._session.closed:
            ssl_ctx = self._ssl_ctx()
            conn = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._session = aiohttp.ClientSession(connector=conn)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, **params) -> dict:
        session = await self._get_session()
        url = f"{self._base_url}/{method}"
        try:
            async with session.post(url, json=params, ssl=self._ssl_ctx()) as resp:
                data = await resp.json(content_type=None)
                log_api_event("telegram", method, resp.status, payload=data)
                if not data.get("ok"):
                    logger.error(f"Telegram API {method} error: {data}")
                    return {
                        "ok": False,
                        "error_code": data.get("error_code"),
                        "description": data.get("description", ""),
                    }
                return {"ok": True, "result": data.get("result")}
        except Exception as e:
            log_api_event("telegram", method, "exception", error=str(e))
            logger.error(f"Telegram {method} failed: {e}")
            return {"ok": False, "description": str(e), "error_kind": "exception"}

    async def setup_commands(self):
        """Register bot commands with Telegram so they show in the menu."""
        await self._request("setMyCommands", commands=BOT_COMMANDS)
        logger.info("Bot commands registered with Telegram")

    def _resolve_chat_id(self, chat_id: int | None) -> int | None:
        target = chat_id if chat_id is not None else self._chat_id
        if target is None:
            logger.warning("Skipping Telegram send because no target chat_id was provided")
        return target

    # --- Sending messages ---

    async def send_text(self, text: str, chat_id: int | None = None, parse_mode: str = "HTML") -> dict | None:
        """Send a text message."""
        target = self._resolve_chat_id(chat_id)
        if target is None:
            return None
        return await self._request(
            "sendMessage",
            chat_id=target,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )

    async def send_with_buttons(self, text: str, buttons: list[list[dict]], chat_id: int | None = None) -> dict | None:
        """Send a message with an inline keyboard."""
        target = self._resolve_chat_id(chat_id)
        if target is None:
            return None
        return await self._request(
            "sendMessage",
            chat_id=target,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup={"inline_keyboard": buttons},
        )

    async def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        """Acknowledge a callback query (button press)."""
        await self._request(
            "answerCallbackQuery",
            callback_query_id=callback_query_id,
            text=text,
        )

    async def send_job_card(self, job_card: str, job: object, job_id: str = "", url: str = "", chat_id: int | None = None):
        """Send a job card with buttons for opening the link and generating a proposal."""
        target = self._resolve_chat_id(chat_id)
        if target is None:
            return None

        if job_id:
            self.pending_jobs[(target, job_id)] = job

        buttons = []
        row = []
        if url:
            row.append({"text": "Open Link", "url": url})
        if job_id:
            row.append({"text": "Generate Proposal", "callback_data": f"proposal_{job_id}"})
        if row:
            buttons.append(row)

        if buttons:
            return await self.send_with_buttons(job_card, buttons, target)
        return await self.send_text(job_card, target)

    async def send_job_with_proposal(self, job_card: str, proposal: str, job_id: str = "",
                                     url: str = "", chat_id: int | None = None):
        """Send a single job card with inline buttons for Open Link and Copy Proposal."""
        target = self._resolve_chat_id(chat_id)
        if target is None:
            return None

        if job_id:
            self.pending_proposals[(target, job_id)] = proposal

        buttons = []
        row = []
        if url:
            row.append({"text": "Open Link", "url": url})
        if job_id:
            row.append({"text": "Copy Proposal", "callback_data": f"copy_{job_id}"})
        if row:
            buttons.append(row)

        if buttons:
            return await self.send_with_buttons(job_card, buttons, target)
        return await self.send_text(job_card, target)

    async def notify(self, job: JobPosting, chat_id: int | None = None) -> bool:
        """Send a Discord job notification with inline buttons."""
        target = self._resolve_chat_id(chat_id)
        if target is None:
            return False

        job_id = uuid.uuid4().hex[:6]
        job.job_id = job_id
        self.pending_jobs[(target, job_id)] = job
        message = self._format_job_html(job)

        buttons = [[
            {"text": "Open in Discord", "url": job.message_url},
        ]]

        response = await self.send_with_buttons(message, buttons, target)
        if response and response.get("ok"):
            logger.info(f"Sent: {job.title} [{job_id}]")
            return True
        return False

    # --- Receiving updates ---

    async def get_updates(self, timeout: int = 10) -> list[dict]:
        """Long-poll for new messages and callback queries."""
        session = await self._get_session()
        url = f"{self._base_url}/getUpdates"
        params = {
            "offset": self._update_offset,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        try:
            async with session.post(url, json=params, ssl=self._ssl_ctx(),
                                    timeout=aiohttp.ClientTimeout(total=timeout + 10)) as resp:
                data = await resp.json(content_type=None)
                log_api_event("telegram", "getUpdates", resp.status, payload=data)
                if not data.get("ok"):
                    return []
                updates = data.get("result", [])
                if updates:
                    self._update_offset = updates[-1]["update_id"] + 1
                return updates
        except asyncio.TimeoutError:
            return []
        except Exception as e:
            log_api_event("telegram", "getUpdates", "exception", error=str(e))
            logger.error(f"getUpdates failed: {e}")
            return []

    # --- Formatting ---

    def _format_job_html(self, job: JobPosting) -> str:
        skills_str = ", ".join(job.skills) if job.skills else "Not specified"
        now = datetime.now().strftime("%H:%M")

        title = html_escape(job.title)
        desc = html_escape(job.description[:400])
        jtype = html_escape(job.job_type)
        pay = html_escape(job.pay or "Not specified")
        skills_str = html_escape(skills_str)

        lines = [
            f"<b>NEW DEV JOB FOUND</b>",
            "",
            f"<b>Title:</b>  {title}",
            f"<b>Type:</b>  {jtype}",
            f"<b>Pay:</b>  {pay}",
            f"<b>Skills:</b>  {skills_str}",
            "",
            f"{desc}",
        ]
        if job.deadline:
            lines.append(f"<b>Deadline:</b>  {html_escape(job.deadline)}")
        if job.contact_info:
            lines.append(f"<b>Contact:</b>  {html_escape(job.contact_info)}")
        if job.experience_level:
            lines.append(f"<b>Experience:</b>  {html_escape(job.experience_level)}")
        lines.extend([
            "",
            f"<b>Source:</b>  {html_escape(job.source_server)} &gt; #{html_escape(job.source_channel)}",
            f"<b>Posted by:</b>  {html_escape(job.source_author)}",
            "",
            f"<i>Detected at {now}</i>",
        ])
        return "\n".join(lines)
