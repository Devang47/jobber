"""
Telegram-controlled Job Bot v2.
Monitors Discord, Reddit, Wellfound, Upwork, Freelancer.
Smart job scoring, personalized proposals with portfolio links.

Commands via Telegram:
  /start reddit / wellfound / discord / upwork / freelancer / all
  /stop reddit / wellfound / discord / upwork / freelancer / all
  /scan        — One-time scan all platforms
  /status      — Show what's running
  /help        — Show commands
"""

import asyncio
import logging
import signal
import uuid
from html import escape as html_escape

from groq import AsyncGroq

from config import Config
from discord_gateway import DiscordGateway
from prefilter import PreFilter
from classifier import JobClassifier
from notifier import TelegramNotifier
from logger_setup import setup_logging
from platforms.base import PlatformJob
from platforms.reddit import fetch_reddit_jobs
from platforms.wellfound import fetch_wellfound_jobs
from platforms.upwork import fetch_upwork_jobs
from platforms.freelancer_api import fetch_freelancer_jobs
from profiles import get_profile, set_profile_field, format_profile, find_user_by_name, list_all_profiles

logger = logging.getLogger("bot")

POLL_INTERVAL = 120  # 2 minutes

# Skills for scoring (weighted)
SKILL_WEIGHTS = {
    "react": 10, "next.js": 10, "nextjs": 10, "node": 8, "node.js": 8,
    "python": 9, "javascript": 8, "typescript": 9, "vue": 7, "angular": 5,
    "django": 7, "flask": 7, "express": 7, "full stack": 10, "fullstack": 10,
    "full-stack": 10, "frontend": 8, "backend": 8, "web dev": 8,
    "automation": 9, "scraping": 9, "web scraping": 9, "bot": 9,
    "api": 7, "rest": 6, "graphql": 7, "websocket": 7,
    "postgres": 6, "mongodb": 6, "redis": 5, "firebase": 5,
    "docker": 6, "aws": 6, "devops": 6, "ci/cd": 5,
    "tailwind": 6, "saas": 8, "landing page": 7, "dashboard": 7,
    "website": 7, "web app": 8, "webapp": 8, "wordpress": 4,
    "shopify": 4, "laravel": 5, "php": 4, "mern": 9,
    "payment": 6, "stripe": 6, "vercel": 5,
}

PROPOSAL_PROMPT = """Write a short proposal to apply for this freelance dev job.
You are {name} — full stack dev.

Your profile:
- Stack: React, Next.js, Node.js, Python, Django, Flask, TypeScript, automation, bots, web scraping, DevOps
- Portfolio: {portfolio}
- GitHub: {github}
- Rate: {rate}

Rules:
- 70-120 words MAX
- Open by referencing something SPECIFIC about their project (not generic)
- Drop 1-2 concrete skills that match what they need
- Mention a similar project you've built or can reference from your portfolio
- Show availability ("can start today", "got bandwidth this week")
- End with: "Here's my work: {portfolio}" or "Check my GitHub: {github}"
- Sound like a real human on {platform} — casual but professional
- NO emojis, NO "Dear", NO bullet lists, NO "Best regards"
- Don't quote your rate unless the budget is clearly stated
- Be confident, not desperate
- Vary the opening — never start the same way twice

Job:
Title: {title}
Description: {description}
Skills needed: {skills}
Budget: {budget}

Write ONLY the message, nothing else."""

HELP_TEXT = """<b>Job Bot v2</b>

<b>Platforms:</b>
  /start reddit — 12 dev subreddits
  /start wellfound — Startup jobs
  /start discord — 9 freelance servers
  /start upwork — Upwork RSS feeds
  /start freelancer — Freelancer.com API
  /start all — Everything

<b>Controls:</b>
  /stop [platform] or /stop all
  /scan — Scan &amp; send jobs
  /scan Manas — Scan for specific user
  /alert Dev — Route live alerts to Dev only
  /alert all — Route live alerts to everyone
  /status — What's running
  /users — List registered users

<b>Your Profile:</b>
  /profile — View your profile
  /set name Your Name
  /set github https://github.com/you
  /set portfolio https://yoursite.com
  /set rate $20-30/hr
  /set skills React, Node, Python"""


def score_job(job: PlatformJob) -> int:
    """Score a job based on skill match. Higher = better fit."""
    text = (job.title + " " + job.description + " " + " ".join(job.skills)).lower()
    score = 0
    for skill, weight in SKILL_WEIGHTS.items():
        if skill in text:
            score += weight
    return score


def priority_label(score: int) -> tuple[str, str]:
    """Returns (label, emoji) for a score."""
    if score >= 30:
        return "PERFECT FIT", "🔥🔥🔥"
    elif score >= 20:
        return "Great Match", "🔥🔥"
    elif score >= 10:
        return "Good Match", "🔥"
    return "Match", "📋"


PLATFORM_TAGS = {
    "reddit": ("RD", "🟠"),
    "wellfound": ("WF", "🟣"),
    "upwork": ("UP", "🟢"),
    "freelancer": ("FL", "🔵"),
}


class JobBot:
    def __init__(self):
        self.config = Config.from_env()
        self.notifier = TelegramNotifier(self.config)
        self.groq = AsyncGroq(api_key=self.config.groq_api_key)
        self.shutdown = asyncio.Event()

        self.active_platforms: dict[str, bool] = {
            "reddit": False,
            "wellfound": False,
            "discord": False,
            "upwork": False,
            "freelancer": False,
        }

        self.platform_tasks: dict[str, asyncio.Task | None] = {
            k: None for k in self.active_platforms
        }

        self.seen_ids: dict[str, set[str]] = {
            "reddit": set(),
            "wellfound": set(),
            "upwork": set(),
            "freelancer": set(),
        }

        # Alert targeting: None = group chat, or a specific user's chat_id
        self.alert_target: int | None = None

    async def run(self):
        setup_logging(self.config.log_level)
        logger.info("Job Bot v2 starting...")

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.shutdown.set)

        # Register commands with Telegram
        await self.notifier.setup_commands()
        await self.notifier.send_text(HELP_TEXT)

        while not self.shutdown.is_set():
            try:
                updates = await self.notifier.get_updates(timeout=15)

                if not updates:
                    continue

                for update in updates:
                    # Handle button presses
                    if "callback_query" in update:
                        await self.handle_callback(update["callback_query"])
                        continue

                    # Handle text commands
                    message = update.get("message", {})
                    text = message.get("text", "").strip()
                    user = message.get("from", {})
                    user_id = str(user.get("id", ""))
                    chat_id = message.get("chat", {}).get("id")

                    if not text:
                        continue

                    await self.handle_command(text, user_id, chat_id)

            except Exception as e:
                logger.error(f"Bot error: {e}")
                await asyncio.sleep(3)

        await self.stop_all()
        await self.notifier.close()
        logger.info("Job Bot stopped.")

    async def handle_callback(self, callback: dict):
        """Handle inline keyboard button presses."""
        cb_id = callback.get("id", "")
        data = callback.get("data", "")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")

        if data.startswith("copy_"):
            job_id = data[5:]
            proposal = self.notifier.pending_proposals.get(job_id)
            if proposal:
                # Send proposal as plain text (no formatting) so user can copy it easily
                await self.notifier.send_text(proposal, chat_id, parse_mode="")
                await self.notifier.answer_callback(cb_id, "Proposal sent below — tap to copy!")
            else:
                await self.notifier.answer_callback(cb_id, "Proposal expired or not found.")
        else:
            await self.notifier.answer_callback(cb_id)

    async def handle_command(self, text: str, user_id: str, chat_id: int):
        cmd = text.lower().strip()
        # Strip bot mention from group commands (e.g. /start@Mannnnnnaaasss_Bot)
        if "@" in cmd:
            cmd = cmd.split("@")[0]
        logger.info(f"Command from {user_id}: {cmd}")

        if cmd == "/help" or cmd == "/start":
            await self.notifier.send_text(HELP_TEXT, chat_id)
        elif cmd == "/status":
            await self.send_status(chat_id)
        elif cmd == "/users":
            await self.notifier.send_text(list_all_profiles(), chat_id)
        elif cmd == "/profile":
            await self.notifier.send_text(format_profile(user_id), chat_id)
        elif cmd.startswith("/set "):
            await self.handle_set(cmd[5:].strip(), user_id, text[5:].strip(), chat_id)
        elif cmd.startswith("/scan"):
            name = cmd[5:].strip() if len(cmd) > 5 else ""
            await self.handle_scan(name, user_id, chat_id)
        elif cmd.startswith("/alert "):
            await self.handle_alert(cmd[7:].strip(), chat_id)
        elif cmd.startswith("/start "):
            await self.start_platform(cmd[7:].strip(), chat_id)
        elif cmd.startswith("/stop "):
            await self.stop_platform(cmd[6:].strip(), chat_id)

    async def handle_alert(self, name: str, chat_id: int):
        if name == "all":
            self.alert_target = None
            await self.notifier.send_text("🔔 Live alerts now going to <b>everyone</b>.", chat_id)
            return

        target_id = find_user_by_name(name)
        if not target_id:
            await self.notifier.send_text(
                f"User <b>{html_escape(name)}</b> not found.\n\n"
                f"Make sure they've set their name:\n/set name {html_escape(name)}\n\n"
                f"Type /users to see registered users.",
                chat_id,
            )
            return

        self.alert_target = int(target_id)
        await self.notifier.send_text(
            f"🔔 Live alerts now going to <b>{html_escape(name)}</b> only.\n"
            f"Type /alert all to send to everyone.",
            chat_id,
        )

    async def handle_scan(self, name: str, user_id: str, chat_id: int):
        target_chat = None
        if name:
            target_id = find_user_by_name(name)
            if not target_id:
                await self.notifier.send_text(
                    f"User <b>{html_escape(name)}</b> not found. Type /users to see registered users.",
                    chat_id,
                )
                return
            target_chat = int(target_id)
        await self.run_scan(target_chat or chat_id, user_id)

    async def handle_set(self, args: str, user_id: str, original_args: str, chat_id: int):
        parts = args.split(" ", 1)
        if len(parts) < 2:
            await self.notifier.send_text(
                "Usage: /set name Your Name\n"
                "Fields: name, github, portfolio, rate, skills",
                chat_id,
            )
            return

        field = parts[0].lower()
        orig_parts = original_args.split(" ", 1)
        value = orig_parts[1] if len(orig_parts) > 1 else parts[1]

        valid_fields = ["name", "github", "portfolio", "rate", "skills"]
        if field not in valid_fields:
            await self.notifier.send_text(
                f"Unknown field: <i>{html_escape(field)}</i>\nValid: {', '.join(valid_fields)}",
                chat_id,
            )
            return

        set_profile_field(user_id, field, value)
        await self.notifier.send_text(f"✅ <b>{html_escape(field)}</b> set to: {html_escape(value)}", chat_id)
        logger.info(f"Profile updated for {user_id}: {field} = {value}")

    async def send_status(self, chat_id: int):
        lines = ["<b>Bot Status</b>\n"]
        for name, active in self.active_platforms.items():
            icon = "🟢" if active else "⚪"
            status = "Running" if active else "Stopped"
            lines.append(f"{icon} <b>{name.title()}</b> — {status}")
        await self.notifier.send_text("\n".join(lines), chat_id)

    async def start_platform(self, name: str, chat_id: int):
        if name == "all":
            for p in self.active_platforms:
                await self.start_platform(p, chat_id)
            return

        if name not in self.active_platforms:
            await self.notifier.send_text(
                f"Unknown: <i>{html_escape(name)}</i>\n"
                f"Available: reddit, wellfound, discord, upwork, freelancer",
                chat_id,
            )
            return

        if self.active_platforms[name]:
            await self.notifier.send_text(f"<b>{name.title()}</b> already running.", chat_id)
            return

        self.active_platforms[name] = True
        fetcher_map = {
            "reddit": lambda: self.poll_loop("reddit", fetch_reddit_jobs),
            "wellfound": lambda: self.poll_loop("wellfound", fetch_wellfound_jobs),
            "upwork": lambda: self.poll_loop("upwork", fetch_upwork_jobs),
            "freelancer": lambda: self.poll_loop("freelancer", fetch_freelancer_jobs),
            "discord": lambda: self.run_discord(),
        }
        self.platform_tasks[name] = asyncio.create_task(fetcher_map[name]())
        await self.notifier.send_text(f"🟢 <b>{name.title()}</b> started!", chat_id)

    async def stop_platform(self, name: str, chat_id: int):
        if name == "all":
            await self.stop_all()
            await self.notifier.send_text("⚪ All platforms stopped.", chat_id)
            return

        if name not in self.active_platforms:
            await self.notifier.send_text(f"Unknown: <i>{html_escape(name)}</i>", chat_id)
            return

        self.active_platforms[name] = False
        task = self.platform_tasks.get(name)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.platform_tasks[name] = None
        await self.notifier.send_text(f"⚪ <b>{name.title()}</b> stopped.", chat_id)

    async def stop_all(self):
        for name in list(self.active_platforms):
            self.active_platforms[name] = False
            task = self.platform_tasks.get(name)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self.platform_tasks[name] = None

    # --- Platform loops ---

    async def poll_loop(self, name: str, fetch_fn):
        logger.info(f"{name} polling started")
        while self.active_platforms.get(name):
            try:
                jobs = await fetch_fn(self.seen_ids.get(name, set()))
                if jobs:
                    scored = [(score_job(j), j) for j in jobs]
                    scored.sort(key=lambda x: x[0], reverse=True)
                    target = self.alert_target
                    await self.send_jobs([(s, j) for s, j in scored[:10]], target)
            except Exception as e:
                logger.error(f"{name} poll error: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    async def run_discord(self):
        logger.info("Discord monitor started")
        prefilter = PreFilter(self.config)
        classifier = JobClassifier(self.config)
        gateway = DiscordGateway(self.config.discord_token, self.config.server_ids)

        while self.active_platforms.get("discord"):
            try:
                await gateway.connect()
                async for message in gateway.listen():
                    if not self.active_platforms.get("discord"):
                        break
                    if not prefilter.should_classify(message):
                        continue
                    job = await classifier.classify(message)
                    if job:
                        await self.notifier.notify(job)
            except ConnectionError as e:
                logger.warning(f"Discord lost: {e}")
                await gateway.close()
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Discord error: {e}")
                await gateway.close()
                await asyncio.sleep(5)
        await gateway.close()

    async def run_scan(self, chat_id: int, user_id: str = ""):
        await self.notifier.send_text("⏳ <b>Scanning all platforms...</b>", chat_id)

        all_jobs = []
        for name, fetch_fn in [
            ("Reddit", fetch_reddit_jobs),
            ("Wellfound", fetch_wellfound_jobs),
            ("Upwork", fetch_upwork_jobs),
            ("Freelancer", fetch_freelancer_jobs),
        ]:
            try:
                jobs = await fetch_fn(set())
                all_jobs.extend(jobs)
                logger.info(f"Scan {name}: {len(jobs)} jobs")
            except Exception as e:
                logger.error(f"Scan {name} error: {e}")

        if not all_jobs:
            await self.notifier.send_text("✅ <b>Scan complete</b> — no dev jobs found.", chat_id)
            return

        scored = [(score_job(j), j) for j in all_jobs]
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:15]

        await self.notifier.send_text(
            f"✅ <b>Found {len(all_jobs)} jobs!</b> Sending top {len(top)} by skill match...",
            chat_id,
        )

        await self.send_jobs(top, chat_id, user_id)

    # --- Send jobs with proposals ---

    async def send_jobs(self, scored_jobs: list[tuple[int, PlatformJob]], chat_id: int | None = None, user_id: str = ""):
        for score, job in scored_jobs:
            await self.send_platform_job(job, score, chat_id, user_id)
            await asyncio.sleep(2)

    async def send_platform_job(self, job: PlatformJob, score: int, chat_id: int | None = None, user_id: str = ""):
        tag, emoji = PLATFORM_TAGS.get(job.platform, ("??", "📋"))
        label, match_emoji = priority_label(score)
        skills_str = ", ".join(job.skills[:5]) if job.skills else "See description"

        title = html_escape(job.title)
        desc = html_escape(job.description[:300])

        lines = [
            f"{emoji} <b>[{tag}] {match_emoji} {label}</b>",
            "",
            f"<b>Title:</b>  {title}",
        ]
        if job.posted_by:
            lines.append(f"<b>By:</b>  {html_escape(job.posted_by)}")
        if job.budget:
            lines.append(f"<b>Budget:</b>  {html_escape(job.budget)}")
        if job.job_type:
            lines.append(f"<b>Type:</b>  {html_escape(job.job_type)}")
        if job.skills:
            lines.append(f"<b>Skills:</b>  {html_escape(skills_str)}")
        lines.extend([
            "",
            desc,
        ])

        job_card = "\n".join(lines)

        # Generate proposal
        profile = get_profile(user_id) if user_id else get_profile("")
        proposal = await self.generate_proposal(job, profile)

        job_id = uuid.uuid4().hex[:6]
        target = chat_id or self.notifier._chat_id

        if proposal:
            await self.notifier.send_job_with_proposal(
                job_card, proposal,
                job_id=job_id, url=job.url, chat_id=target,
            )
        else:
            buttons = [[{"text": "Open Link", "url": job.url}]]
            await self.notifier.send_with_buttons(job_card, buttons, target)

    async def generate_proposal(self, job: PlatformJob, profile: dict | None = None) -> str | None:
        if not profile or not profile.get("name"):
            profile = {"name": "Manas", "portfolio": "", "github": "", "rate": "negotiable", "skills": ""}

        skills_str = ", ".join(job.skills) if job.skills else "Not specified"
        prompt = PROPOSAL_PROMPT.format(
            name=profile.get("name", "Manas"),
            platform=job.platform.title(),
            title=job.title,
            description=job.description[:400],
            skills=skills_str,
            budget=job.budget or "Not specified",
            portfolio=profile.get("portfolio", ""),
            github=profile.get("github", ""),
            rate=profile.get("rate", "negotiable"),
        )
        try:
            resp = await self.groq.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=250,
            )
            return resp.choices[0].message.content.strip().strip('"')
        except Exception as e:
            logger.error(f"Proposal failed: {e}")
            return None


if __name__ == "__main__":
    bot = JobBot()
    asyncio.run(bot.run())
