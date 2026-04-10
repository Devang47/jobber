from dataclasses import dataclass

from platforms.base import PlatformJob


ROLE_WEIGHTS = {
    "software engineer": 24,
    "software engineering": 22,
    "software developer": 20,
    "full stack developer": 24,
    "full stack engineer": 24,
    "full-stack developer": 24,
    "full-stack engineer": 24,
    "full stack": 22,
    "full-stack": 22,
    "fullstack": 22,
    "web developer": 20,
    "web development": 20,
    "frontend developer": 18,
    "frontend engineer": 18,
    "backend developer": 18,
    "backend engineer": 18,
    "front end developer": 18,
    "back end developer": 18,
    "react developer": 18,
    "next.js developer": 18,
    "node.js developer": 18,
    "python developer": 18,
}

STACK_WEIGHTS = {
    "react": 8,
    "next.js": 8,
    "nextjs": 8,
    "node.js": 8,
    "node": 6,
    "python": 7,
    "javascript": 7,
    "typescript": 8,
    "django": 7,
    "flask": 6,
    "express": 6,
    "postgres": 5,
    "mongodb": 5,
    "redis": 4,
    "graphql": 5,
    "rest api": 5,
    "api": 4,
    "docker": 4,
    "aws": 4,
    "tailwind": 4,
    "mern": 8,
}

WEB_CONTEXT_WEIGHTS = {
    "website": 8,
    "web app": 9,
    "webapp": 9,
    "dashboard": 8,
    "landing page": 7,
    "saas": 6,
    "portal": 5,
    "frontend": 6,
    "backend": 6,
}

NEGATIVE_WEIGHTS = {
    "seo": 18,
    "marketing": 16,
    "social media": 18,
    "graphic designer": 20,
    "designer": 16,
    "figma only": 18,
    "video editor": 20,
    "content writer": 20,
    "copywriter": 20,
    "lead generation": 24,
    "appointment setter": 24,
    "virtual assistant": 26,
    "data entry": 26,
    "customer support": 22,
    "sales": 18,
    "bookkeeping": 24,
    "android": 10,
    "ios": 10,
    "swift": 10,
    "kotlin": 10,
    "react native": 12,
    "flutter": 12,
}

HARD_EXCLUDES = (
    "virtual assistant",
    "data entry",
    "appointment setter",
    "lead generation",
    "cold caller",
    "cold calling",
)

MIN_RELEVANCE_SCORE = 18


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


def score_job(job: PlatformJob) -> int:
    return evaluate_job(job).score


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

    has_primary_signal = bool(role_terms) or (len(stack_terms) >= 2 and bool(context_terms))
    hard_excluded = any(term in text for term in HARD_EXCLUDES)
    eligible = has_primary_signal and not hard_excluded and score >= MIN_RELEVANCE_SCORE

    if score >= 40:
        rating = "High"
    elif score >= 28:
        rating = "Strong"
    elif score >= MIN_RELEVANCE_SCORE:
        rating = "Good"
    else:
        rating = "Low"

    return RelevanceResult(eligible=eligible, score=score, rating=rating)
