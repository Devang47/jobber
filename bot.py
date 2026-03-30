"""
WhatsApp-controlled Job Bot v2.
Monitors Discord, Reddit, Wellfound, Upwork, Freelancer.
Smart job scoring, personalized proposals with portfolio links.

Commands via WhatsApp:
  start reddit / wellfound / discord / upwork / freelancer / all
  stop reddit / wellfound / discord / upwork / freelancer / all
  scan        — One-time scan all platforms
  status      — Show what's running
  help        — Show commands
"""

import asyncio
import logging
import re
import signal

from groq import AsyncGroq

from config import Config
from discord_gateway import DiscordGateway
from prefilter import PreFilter
from classifier import JobClassifier
from notifier import WhatsAppNotifier
from logger_setup import setup_logging
from platforms.base import PlatformJob
from platforms.reddit import fetch_reddit_jobs
from platforms.wellfound import fetch_wellfound_jobs
from platforms.upwork import fetch_upwork_jobs
from platforms.freelancer_api import fetch_freelancer_jobs
from profiles import get_profile, set_profile_field, format_profile, find_phone_by_name, list_all_profiles

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
You are Manas — full stack dev.

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

HELP_TEXT = """*🤖 Job Bot v2*

*Platforms:*
• *start reddit* — 12 dev subreddits
• *start wellfound* — Startup jobs
• *start discord* — 9 freelance servers
• *start upwork* — Upwork RSS feeds
• *start freelancer* — Freelancer.com API
• *start all* — Everything

*Controls:*
• *stop [platform]* or *stop all*
• *scan* — Scan & send to everyone
• *scan Manas* — Scan & send only to Manas
• *alert Dev* — Route live alerts to Dev only
• *alert all* — Route live alerts to everyone
• *status* — What's running
• *users* — List registered users

*Your Profile:*
• *profile* — View your profile
• *set name Your Name*
• *set github https://github.com/you*
• *set portfolio https://yoursite.com*
• *set rate $20-30/hr*
• *set skills React, Node, Python*

• *help* — This menu"""


def score_job(job: PlatformJob) -> int:
    """Score a job based on skill match. Higher = better fit."""
    text = (job.title + " " + job.description + " " + " ".join(job.skills)).lower()
    score = 0
    for skill, weight in SKILL_WEIGHTS.items():
        if skill in text:
            score += weight
    return score


def priority_label(score: int) -> str:
    if score >= 30:
        return "🔥🔥🔥 PERFECT FIT"
    elif score >= 20:
        return "🔥🔥 Great Match"
    elif score >= 10:
        return "🔥 Good Match"
    return "Match"


class JobBot:
    def __init__(self):
        self.config = Config.from_env()
        self.notifier = WhatsAppNotifier(self.config)
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

        # Alert targeting: None = all, or a specific phone number
        self.alert_target: str | None = None

    async def run(self):
        setup_logging(self.config.log_level)
        logger.info("Job Bot v2 starting...")

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.shutdown.set)

        await self.notifier.send_text(HELP_TEXT)

        while not self.shutdown.is_set():
            try:
                notification = await self.notifier.receive_notification()

                if notification is None:
                    await asyncio.sleep(2)
                    continue

                receipt_id = notification.get("receiptId")
                body = notification.get("body", {})
                type_webhook = body.get("typeWebhook", "")

                if type_webhook == "outgoingAPIMessageReceived":
                    pass  # Skip bot's own messages
                elif type_webhook in ("incomingMessageReceived", "outgoingMessageReceived"):
                    msg_data = body.get("messageData", {})
                    sender_data = body.get("senderData", {})
                    sender_phone = sender_data.get("chatId", "").replace("@c.us", "")
                    text = ""
                    msg_type = msg_data.get("typeMessage", "")

                    if msg_type == "textMessage":
                        text = msg_data.get("textMessageData", {}).get("textMessage", "")
                    elif msg_type == "extendedTextMessage":
                        text = msg_data.get("extendedTextMessageData", {}).get("text", "")

                    if text.strip():
                        await self.handle_command(text.strip(), sender_phone)

                if receipt_id:
                    await self.notifier.delete_notification(receipt_id)

            except Exception as e:
                logger.error(f"Bot error: {e}")
                await asyncio.sleep(3)

        await self.stop_all()
        logger.info("Job Bot stopped.")

    async def handle_command(self, text: str, phone: str = ""):
        cmd = text.lower().strip()
        logger.info(f"Command from {phone}: {cmd}")

        if cmd == "help":
            await self.notifier.send_text(HELP_TEXT)
        elif cmd == "status":
            await self.send_status()
        elif cmd == "users":
            await self.notifier.send_text(list_all_profiles())
        elif cmd == "profile":
            await self.notifier.send_text(format_profile(phone))
        elif cmd.startswith("set "):
            await self.handle_set(cmd[4:].strip(), phone, text[4:].strip())
        elif cmd.startswith("scan"):
            name = cmd[4:].strip() if len(cmd) > 4 else ""
            await self.handle_scan(name, phone)
        elif cmd.startswith("alert "):
            await self.handle_alert(cmd[6:].strip())
        elif cmd.startswith("start "):
            await self.start_platform(cmd.replace("start ", "").strip())
        elif cmd.startswith("stop "):
            await self.stop_platform(cmd.replace("stop ", "").strip())
        else:
            await self.notifier.send_text(f"Unknown: _{cmd}_\n\nType *help* for commands.")

    async def handle_alert(self, name: str):
        """Route live alerts to a specific person or all."""
        if name == "all":
            self.alert_target = None
            await self.notifier.send_text("🔔 Live alerts now going to *everyone*.")
            return

        target_phone = find_phone_by_name(name)
        if not target_phone:
            await self.notifier.send_text(
                f"User *{name}* not found.\n\n"
                f"Make sure they've set their name:\n*set name {name}*\n\n"
                f"Type *users* to see registered users."
            )
            return

        self.alert_target = target_phone
        await self.notifier.send_text(f"🔔 Live alerts now going to *{name}* only.\nType *alert all* to send to everyone.")

    async def handle_scan(self, name: str, sender_phone: str):
        """Scan and optionally target a specific user."""
        target_phone = None
        if name:
            target_phone = find_phone_by_name(name)
            if not target_phone:
                await self.notifier.send_text(f"User *{name}* not found. Type *users* to see registered users.")
                return
        await self.run_scan(target_phone or sender_phone)

    async def handle_set(self, args: str, phone: str, original_args: str):
        """Handle 'set field value' commands."""
        parts = args.split(" ", 1)
        if len(parts) < 2:
            await self.notifier.send_text("Usage: *set name Your Name*\nFields: name, github, portfolio, rate, skills")
            return

        field = parts[0].lower()
        # Use original case for the value
        orig_parts = original_args.split(" ", 1)
        value = orig_parts[1] if len(orig_parts) > 1 else parts[1]

        valid_fields = ["name", "github", "portfolio", "rate", "skills"]
        if field not in valid_fields:
            await self.notifier.send_text(f"Unknown field: _{field}_\nValid: {', '.join(valid_fields)}")
            return

        set_profile_field(phone, field, value)
        await self.notifier.send_text(f"✓ *{field}* set to: {value}")
        logger.info(f"Profile updated for {phone}: {field} = {value}")

    async def send_status(self):
        lines = ["*📊 Bot Status*\n"]
        for name, active in self.active_platforms.items():
            icon = "🟢" if active else "⚪"
            lines.append(f"{icon} *{name.title()}* — {'Running' if active else 'Stopped'}")
        await self.notifier.send_text("\n".join(lines))

    async def start_platform(self, name: str):
        if name == "all":
            for p in self.active_platforms:
                await self.start_platform(p)
            return

        if name not in self.active_platforms:
            await self.notifier.send_text(f"Unknown: _{name}_\nAvailable: reddit, wellfound, discord, upwork, freelancer")
            return

        if self.active_platforms[name]:
            await self.notifier.send_text(f"*{name.title()}* already running.")
            return

        self.active_platforms[name] = True
        fetchers = {
            "reddit": self.poll_loop("reddit", fetch_reddit_jobs),
            "wellfound": self.poll_loop("wellfound", fetch_wellfound_jobs),
            "upwork": self.poll_loop("upwork", fetch_upwork_jobs),
            "freelancer": self.poll_loop("freelancer", fetch_freelancer_jobs),
            "discord": self.run_discord(),
        }
        self.platform_tasks[name] = asyncio.create_task(fetchers[name])
        await self.notifier.send_text(f"🟢 *{name.title()}* started!")

    async def stop_platform(self, name: str):
        if name == "all":
            await self.stop_all()
            await self.notifier.send_text("⚪ All stopped.")
            return

        if name not in self.active_platforms:
            await self.notifier.send_text(f"Unknown: _{name}_")
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
        await self.notifier.send_text(f"⚪ *{name.title()}* stopped.")

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

    # --- Generic poll loop ---

    async def poll_loop(self, name: str, fetch_fn):
        """Generic polling loop for any platform."""
        logger.info(f"{name} polling started")
        while self.active_platforms.get(name):
            try:
                jobs = await fetch_fn(self.seen_ids.get(name, set()))
                if jobs:
                    # Score and sort — best matches first
                    scored = [(score_job(j), j) for j in jobs]
                    scored.sort(key=lambda x: x[0], reverse=True)
                    target = [self.alert_target] if self.alert_target else None
                    await self.send_jobs([(s, j) for s, j in scored[:10]], target)
            except Exception as e:
                logger.error(f"{name} poll error: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    async def run_discord(self):
        """Discord gateway monitor."""
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

    async def run_scan(self, phone: str = ""):
        """One-time scan of all platforms."""
        await self.notifier.send_text("*Scanning all platforms...*")

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
            await self.notifier.send_text("*Scan complete* — no dev jobs found.")
            return

        # Score and sort
        scored = [(score_job(j), j) for j in all_jobs]
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:15]

        await self.notifier.send_text(
            f"*Found {len(all_jobs)} jobs!* Sending top {len(top)} by skill match..."
        )

        target = [phone] if phone else None
        await self.send_jobs(top, target)

    # --- Send jobs with proposals ---

    async def send_jobs(self, scored_jobs: list[tuple[int, PlatformJob]], target_phones: list[str] | None = None):
        for score, job in scored_jobs:
            await self.send_platform_job(job, score, target_phones)
            await asyncio.sleep(2)

    async def send_platform_job(self, job: PlatformJob, score: int, phones: list[str] | None = None):
        tag = {
            "reddit": "RD", "wellfound": "WF",
            "upwork": "UP", "freelancer": "FL",
        }.get(job.platform, "??")

        label = priority_label(score)
        skills_str = ", ".join(job.skills[:5]) if job.skills else "See description"

        lines = [
            f"*[{tag}] {label}*",
            "",
            f"*Title:* {job.title}",
        ]
        if job.posted_by:
            lines.append(f"*By:* {job.posted_by}")
        if job.budget:
            lines.append(f"*Budget:* {job.budget}")
        if job.job_type:
            lines.append(f"*Type:* {job.job_type}")
        if job.skills:
            lines.append(f"*Skills:* {skills_str}")
        lines.extend([
            "",
            job.description[:300],
            "",
            f"*Apply:* {job.url}",
        ])

        job_card = "\n".join(lines)

        # Use target user's profile for proposal, fallback to first phone
        if phones:
            clean = phones[0].replace("+", "").replace("-", "")
        else:
            clean = self._phones[0] if hasattr(self, '_phones') else "919520141813"
        profile = get_profile(clean)
        proposal = await self.generate_proposal(job, profile)

        if proposal:
            await self.notifier.send_job_with_proposal(job_card, proposal, phones)
        else:
            await self.notifier.send_text(job_card, phones)

    async def generate_proposal(self, job: PlatformJob, profile: dict | None = None) -> str | None:
        if not profile or not profile.get("name"):
            profile = get_profile("919520141813")  # Fallback to Manas

        skills_str = ", ".join(job.skills) if job.skills else "Not specified"
        prompt = PROPOSAL_PROMPT.format(
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
