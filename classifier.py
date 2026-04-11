import re

from config import Config
from job_relevance import evaluate_job
from models import JobPosting
from platforms.base import PlatformJob

ROLE_TERMS = (
    "software engineer",
    "software developer",
    "web developer",
    "web development",
    "full stack",
    "full-stack",
    "fullstack",
    "frontend developer",
    "frontend engineer",
    "backend developer",
    "backend engineer",
)

STACK_PATTERNS = {
    "React": r"\breact\b",
    "Next.js": r"\bnext(?:\.js|js)\b",
    "Node.js": r"\bnode(?:\.js|js)?\b",
    "Svelte": r"\bsvelte\b",
    "Sveltekit": r"\bsveltekit\b",
    "Python": r"\bpython\b",
    "TypeScript": r"\btypescript\b",
    "JavaScript": r"\bjavascript\b",
    "Django": r"\bdjango\b",
    "Flask": r"\bflask\b",
    "Express": r"\bexpress\b",
    "PostgreSQL": r"\bpostgres(?:ql)?\b",
    "MongoDB": r"\bmongodb\b",
    "GraphQL": r"\bgraphql\b",
    "Docker": r"\bdocker\b",
    "AWS": r"\baws\b",
    "Tailwind": r"\btailwind\b",
}

HIRING_TERMS = (
    "hiring",
    "looking for",
    "need a",
    "need an",
    "need someone",
    "seeking",
    "budget",
    "rate",
    "paid",
    "dm me",
    "contact me",
    "apply",
    "contract",
    "freelance",
)

BLOCKED_TERMS = (
    "virtual assistant",
    "data entry",
    "lead generation",
    "appointment setter",
    "customer support",
    "sales rep",
    "video editor",
    "graphic designer",
    "social media manager",
)

PAY_PATTERN = re.compile(
    r"(\$[\d,.]+(?:\s*[-–]\s*\$?[\d,.]+)?(?:\s*/\s*(?:hr|hour|week|month))?|"
    r"[\d,.]+\s*(?:usd|usdt|eur)(?:\s*/\s*(?:hr|hour|week|month))?)",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
DISCORD_PATTERN = re.compile(r"(discord(?:\s*[:\-]\s*|\s+)([A-Za-z0-9_.-]+))", re.IGNORECASE)
DEADLINE_PATTERN = re.compile(
    r"\b(?:deadline|by|before)\s+((?:tomorrow|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|[A-Za-z]{3,9}\s+\d{1,2}))",
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_title(content: str) -> str:
    lines = [_clean_text(line) for line in content.splitlines() if _clean_text(line)]
    if lines:
        title = re.sub(r"^\[[^\]]+\]\s*", "", lines[0])
    else:
        title = _clean_text(content[:120])
    if len(title) > 120:
        title = title[:117].rstrip() + "..."
    return title or "Untitled"


def _extract_description(content: str) -> str:
    cleaned = _clean_text(content)
    return cleaned[:500]


def _extract_skills(content: str) -> list[str]:
    skills = [name for name, pattern in STACK_PATTERNS.items() if re.search(pattern, content, re.IGNORECASE)]
    return skills[:8]


def _extract_pay(content: str) -> str | None:
    match = PAY_PATTERN.search(content)
    return match.group(1).strip().rstrip(".,;:") if match else None


def _extract_contact_info(content: str) -> str | None:
    email_match = EMAIL_PATTERN.search(content)
    if email_match:
        return email_match.group(0)

    url_match = URL_PATTERN.search(content)
    if url_match:
        return url_match.group(0)

    discord_match = DISCORD_PATTERN.search(content)
    if discord_match:
        return discord_match.group(1).strip()

    lowered = content.lower()
    if "dm me" in lowered or "message me" in lowered:
        return "DM on Discord"
    return None


def _extract_deadline(content: str) -> str | None:
    match = DEADLINE_PATTERN.search(content)
    return match.group(1).strip() if match else None


def _extract_job_type(content: str) -> str:
    lowered = content.lower()
    if "full-time" in lowered or "full time" in lowered:
        return "full-time"
    if "part-time" in lowered or "part time" in lowered:
        return "part-time"
    if "freelance" in lowered:
        return "freelance"
    return "contract"


def _extract_experience_level(content: str) -> str | None:
    lowered = content.lower()
    if "senior" in lowered:
        return "senior"
    if "junior" in lowered:
        return "junior"
    if "mid-level" in lowered or "mid level" in lowered or "mid-level" in lowered or "mid " in lowered:
        return "mid"
    return None


def _looks_like_hiring_post(content: str) -> bool:
    lowered = content.lower()
    if any(term in lowered for term in BLOCKED_TERMS):
        return False
    if any(term in lowered for term in HIRING_TERMS):
        return True
    return False


class JobClassifier:
    def __init__(self, config: Config):
        del config

    async def classify(self, message: dict) -> JobPosting | None:
        """Deterministically classify and extract Discord job posts."""
        content = message.get("content", "")
        author = message.get("author", {}).get("username", "unknown")
        if not _looks_like_hiring_post(content):
            return None

        title = _extract_title(content)
        description = _extract_description(content)
        skills = _extract_skills(content)
        pay = _extract_pay(content)
        contact_info = _extract_contact_info(content)
        deadline = _extract_deadline(content)
        job_type = _extract_job_type(content)
        experience_level = _extract_experience_level(content)

        candidate = PlatformJob(
            platform="discord",
            title=title,
            description=description,
            skills=skills,
            budget=pay,
            job_type=job_type,
            url=self._build_message_url(message),
            posted_by=author,
            posted_time=message.get("timestamp"),
            location=message.get("_server_name", "Unknown"),
            job_id=str(message.get("id", "")),
            source_name=message.get("_channel_name", "Unknown"),
        )
        if not evaluate_job(candidate).eligible:
            return None

        return JobPosting(
            title=title,
            description=description,
            skills=skills,
            pay=pay,
            deadline=deadline,
            contact_info=contact_info,
            job_type=job_type,
            experience_level=experience_level,
            raw_snippet=content[:200],
            source_server=message.get("_server_name", "Unknown"),
            source_channel=message.get("_channel_name", "Unknown"),
            source_author=author,
            source_author_id=message.get("author", {}).get("id", ""),
            message_url=self._build_message_url(message),
        )

    def _build_message_url(self, message: dict) -> str:
        guild_id = message.get("guild_id", "")
        channel_id = message.get("channel_id", "")
        message_id = message.get("id", "")
        return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
