from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape as html_escape

from job_relevance import RelevanceResult
from platforms.base import PlatformJob


PLATFORM_TAGS = {
    "discord": ("DC", "🔵"),
    "reddit": ("RD", "🟠"),
    "wellfound": ("WF", "🟣"),
    "upwork": ("UP", "🟢"),
    "freelancer": ("FL", "🟤"),
}


def _format_posted_time(posted_time: str | None) -> str:
    if not posted_time:
        return "Unknown"

    normalized = posted_time.strip()
    try:
        if normalized.endswith("Z"):
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(normalized)
        except (TypeError, ValueError):
            return normalized

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_ranked_platform_job(job: PlatformJob, relevance: RelevanceResult) -> str:
    tag, emoji = PLATFORM_TAGS.get(job.platform, ("??", "📋"))
    skills_str = ", ".join(job.skills[:5]) if job.skills else "See description"
    description = job.description[:500] if job.description else "No description provided."

    lines = [
        f"{emoji} <b>[{tag}] {html_escape(relevance.rating)} Relevance</b>",
        "",
        f"<b>Title:</b> {html_escape(job.title)}",
        f"<b>Platform:</b> {html_escape(job.platform.title())}",
        f"<b>Posted:</b> {html_escape(_format_posted_time(job.posted_time))}",
        f"<b>Relevance:</b> {html_escape(relevance.rating)} ({relevance.score})",
    ]
    if job.posted_by:
        lines.append(f"<b>By:</b> {html_escape(job.posted_by)}")
    if job.budget:
        lines.append(f"<b>Budget:</b> {html_escape(job.budget)}")
    if job.job_type:
        lines.append(f"<b>Type:</b> {html_escape(job.job_type)}")
    if job.skills:
        lines.append(f"<b>Skills:</b> {html_escape(skills_str)}")
    if job.location:
        lines.append(f"<b>Location:</b> {html_escape(job.location)}")
    lines.extend([
        "",
        "<b>Job Description:</b>",
        html_escape(description),
    ])
    return "\n".join(lines)


def format_pipeline_job(job: PlatformJob) -> str:
    tag = "RD" if job.platform == "reddit" else "WF"
    skills_str = ", ".join(job.skills[:5]) if job.skills else "See description"

    lines = [
        f"*[{tag}] {job.platform.upper()} — NEW DEV JOB*",
        "",
        f"*Title:* {job.title}",
    ]

    if job.posted_by:
        lines.append(f"*Company/By:* {job.posted_by}")
    if job.budget:
        lines.append(f"*Budget:* {job.budget}")
    if job.job_type:
        lines.append(f"*Type:* {job.job_type}")
    if job.skills:
        lines.append(f"*Skills:* {skills_str}")

    lines.extend([
        "",
        f"*Description:*",
        job.description[:300],
        "",
        f"*Apply here:* {job.url}",
    ])

    return "\n".join(lines)
