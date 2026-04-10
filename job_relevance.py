from platforms.base import PlatformJob


SKILL_WEIGHTS = {
    "react": 10,
    "next.js": 10,
    "nextjs": 10,
    "node": 8,
    "node.js": 8,
    "python": 9,
    "javascript": 8,
    "typescript": 9,
    "vue": 7,
    "angular": 5,
    "django": 7,
    "flask": 7,
    "express": 7,
    "full stack": 10,
    "fullstack": 10,
    "full-stack": 10,
    "frontend": 8,
    "backend": 8,
    "web dev": 8,
    "automation": 9,
    "scraping": 9,
    "web scraping": 9,
    "bot": 9,
    "api": 7,
    "rest": 6,
    "graphql": 7,
    "websocket": 7,
    "postgres": 6,
    "mongodb": 6,
    "redis": 5,
    "firebase": 5,
    "docker": 6,
    "aws": 6,
    "devops": 6,
    "ci/cd": 5,
    "tailwind": 6,
    "saas": 8,
    "landing page": 7,
    "dashboard": 7,
    "website": 7,
    "web app": 8,
    "webapp": 8,
    "wordpress": 4,
    "shopify": 4,
    "laravel": 5,
    "php": 4,
    "mern": 9,
    "payment": 6,
    "stripe": 6,
    "vercel": 5,
}


def score_job(job: PlatformJob) -> int:
    text = (job.title + " " + job.description + " " + " ".join(job.skills)).lower()
    score = 0
    for skill, weight in SKILL_WEIGHTS.items():
        if skill in text:
            score += weight
    return score


def priority_label(score: int) -> tuple[str, str]:
    if score >= 30:
        return "PERFECT FIT", "🔥🔥🔥"
    if score >= 20:
        return "Great Match", "🔥🔥"
    if score >= 10:
        return "Good Match", "🔥"
    return "Match", "📋"
