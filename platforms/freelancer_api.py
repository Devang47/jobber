"""Freelancer.com job monitor via public API."""

import logging
import ssl

try:
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - exercised only in dependency-light test envs
    aiohttp = None

try:
    import certifi
except ModuleNotFoundError:  # pragma: no cover - exercised only in dependency-light test envs
    certifi = None

from api_logger import log_api_event
from .base import PlatformJob

logger = logging.getLogger(__name__)

API_URL = "https://www.freelancer.com/api/projects/0.1/projects/active/"

# Freelancer.com skill IDs for dev jobs
SKILL_IDS = [
    3,    # PHP
    9,    # JavaScript
    13,   # CSS
    17,   # Python
    30,   # Java
    55,   # HTML
    116,  # MySQL
    158,  # React.js
    292,  # TypeScript
    355,  # Node.js
    1004, # Angular
    1098, # Vue.js
    1286, # Next.js
    1317, # Full Stack
    2077, # Web Scraping
]


async def fetch_freelancer_jobs(seen_ids: set[str]) -> list[PlatformJob]:
    """Fetch latest developer jobs from Freelancer.com."""
    if aiohttp is None:
        raise RuntimeError("aiohttp is required to fetch Freelancer jobs")
    ssl_ctx = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    new_jobs = []

    params = {
        "compact": "true",
        "job_details": "true",
        "limit": "30",
        "offset": "0",
        "sort_field": "time_submitted",
        "full_description": "true",
        "project_types[]": ["fixed", "hourly"],
    }
    # Add skill filters
    for sid in SKILL_IDS[:10]:  # Limit to avoid URL too long
        params.setdefault("jobs[]", [])
        if isinstance(params["jobs[]"], list):
            params["jobs[]"].append(str(sid))

    try:
        async with aiohttp.ClientSession(connector=conn) as session:
            async with session.get(
                API_URL, params=params, ssl=ssl_ctx,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    log_api_event("freelancer", "projects", resp.status, payload=data)
                    projects = data.get("result", {}).get("projects", [])

                    for proj in projects:
                        pid = str(proj.get("id", ""))
                        if pid in seen_ids:
                            continue
                        seen_ids.add(pid)

                        # Extract skills
                        skills = [
                            j.get("name", "")
                            for j in proj.get("jobs", [])
                            if j.get("name")
                        ]

                        # Budget
                        budget_info = proj.get("budget", {})
                        budget = None
                        if budget_info:
                            min_b = budget_info.get("minimum", 0)
                            max_b = budget_info.get("maximum", 0)
                            currency = proj.get("currency", {}).get("code", "USD")
                            if min_b and max_b:
                                budget = f"${min_b}-${max_b} {currency}"
                            elif max_b:
                                budget = f"${max_b} {currency}"

                        proj_type = proj.get("type", "fixed")
                        title = proj.get("title", "Untitled")
                        desc = proj.get("preview_description", "") or proj.get("description", "")
                        url = f"https://www.freelancer.com/projects/{proj.get('seo_url', pid)}"

                        new_jobs.append(PlatformJob(
                            platform="freelancer",
                            title=title,
                            description=desc[:500],
                            skills=skills,
                            budget=budget,
                            job_type=proj_type,
                            url=url,
                            posted_by=None,
                            posted_time=None,
                            location="Remote",
                            job_id=pid,
                        ))
                else:
                    log_api_event("freelancer", "projects", resp.status)
                    logger.debug(f"Freelancer API: HTTP {resp.status}")
    except Exception as e:
        log_api_event("freelancer", "projects", "exception", error=str(e))
        logger.error(f"Freelancer fetch error: {e}")

    logger.info(f"Freelancer: {len(new_jobs)} new jobs")
    return new_jobs
