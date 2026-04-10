from dataclasses import dataclass

from platforms.base import PlatformJob


ROLE_WEIGHTS = {
    "software engineer": 3,
    "software developer": 3,
    "full stack": 3,
    "full-stack": 3,
    "fullstack": 3,
    "web developer": 3,
    "web development": 3,
    "frontend developer": 2,
    "frontend engineer": 2,
    "backend developer": 2,
    "backend engineer": 2,
    "react developer": 2,
    "next.js developer": 2,
    "node.js developer": 2,
    "python developer": 2,
}

STACK_WEIGHTS = {
    "react": 2,
    "next.js": 2,
    "nextjs": 2,
    "node.js": 2,
    "node": 1,
    "python": 2,
    "javascript": 2,
    "typescript": 2,
    "django": 2,
    "flask": 1,
    "express": 1,
    "postgres": 1,
    "mongodb": 1,
    "graphql": 1,
    "api": 1,
    "docker": 1,
    "aws": 1,
    "tailwind": 1,
    "mern": 2,
}

WEB_CONTEXT_WEIGHTS = {
    "website": 1,
    "web app": 1,
    "webapp": 1,
    "dashboard": 1,
    "landing page": 1,
    "saas": 1,
    "portal": 1,
    "frontend": 1,
    "backend": 1,
}

NEGATIVE_WEIGHTS = {
    "seo": 3,
    "marketing": 2,
    "social media": 3,
    "graphic designer": 4,
    "figma only": 4,
    "video editor": 4,
    "content writer": 4,
    "copywriter": 4,
    "lead generation": 5,
    "appointment setter": 5,
    "virtual assistant": 5,
    "data entry": 5,
    "customer support": 4,
    "sales": 3,
    "bookkeeping": 5,
    "react native": 2,
    "flutter": 2,
}

HARD_EXCLUDES = (
    "virtual assistant",
    "data entry",
    "appointment setter",
    "lead generation",
    "cold caller",
    "cold calling",
)

MIN_RELEVANCE_SCORE = 3


@dataclass(frozen=True)
class RelevanceResult:
    eligible: bool
    score: int
    rating: str


def _normalized_job_text(job: PlatformJob) -> str:
    return " ".join(
        part
        for part in [job.title, job.description, " ".join(job.skills), job.budget or "", job.job_type or ""]
        if part
    ).lower()


def _matching_terms(text: str, weighted_terms: dict[str, int]) -> list[str]:
    return [term for term in weighted_terms if term in text]


def _weighted_score(matches: list[str], weighted_terms: dict[str, int]) -> int:
    return sum(weighted_terms[term] for term in matches)


def evaluate_job(job: PlatformJob) -> RelevanceResult:
    text = _normalized_job_text(job)
    role_terms = _matching_terms(text, ROLE_WEIGHTS)
    stack_terms = _matching_terms(text, STACK_WEIGHTS)
    context_terms = _matching_terms(text, WEB_CONTEXT_WEIGHTS)
    negative_terms = _matching_terms(text, NEGATIVE_WEIGHTS)

    positive_score = (
        _weighted_score(role_terms, ROLE_WEIGHTS)
        + _weighted_score(stack_terms, STACK_WEIGHTS)
        + _weighted_score(context_terms, WEB_CONTEXT_WEIGHTS)
    )
    negative_score = _weighted_score(negative_terms, NEGATIVE_WEIGHTS)
    score = positive_score - negative_score

    has_primary_signal = bool(role_terms) or len(stack_terms) >= 2 or (bool(stack_terms) and bool(context_terms))
    hard_excluded = any(term in text for term in HARD_EXCLUDES)
    eligible = has_primary_signal and not hard_excluded and score >= MIN_RELEVANCE_SCORE

    if score >= 8:
        rating = "High"
    elif score >= 5:
        rating = "Strong"
    elif score >= MIN_RELEVANCE_SCORE:
        rating = "Good"
    else:
        rating = "Low"

    return RelevanceResult(eligible=eligible, score=score, rating=rating)
