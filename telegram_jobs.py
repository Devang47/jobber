from html import escape as html_escape

from job_relevance import priority_label
from platforms.base import PlatformJob


PLATFORM_TAGS = {
    "discord": ("DC", "🔵"),
    "reddit": ("RD", "🟠"),
    "wellfound": ("WF", "🟣"),
    "upwork": ("UP", "🟢"),
    "freelancer": ("FL", "🟤"),
}


def format_ranked_platform_job(job: PlatformJob, score: int) -> str:
    tag, emoji = PLATFORM_TAGS.get(job.platform, ("??", "📋"))
    label, match_emoji = priority_label(score)
    skills_str = ", ".join(job.skills[:5]) if job.skills else "See description"

    lines = [
        f"{emoji} <b>[{tag}] {match_emoji} {label}</b>",
        "",
        f"<b>Title:</b> {html_escape(job.title)}",
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
    lines.extend(["", html_escape(job.description[:350])])
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
