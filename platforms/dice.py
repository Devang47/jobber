"""Dice.com job monitor via public API."""

import logging
import ssl

import aiohttp
import certifi

from .base import PlatformJob

logger = logging.getLogger(__name__)

API_URL = "https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search"

SEARCH_QUERIES = [
    "remote freelance developer",
    "remote react developer",
    "remote python developer",
    "remote full stack developer",
    "remote contract developer",
]


async def fetch_dice_jobs(seen_ids: set[str]) -> list[PlatformJob]:
    """Fetch latest dev jobs from Dice."""
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    headers = {"x-api-key": "1YAt0R9wBg4WfsF9VB2778F5CHLAPMVW3WAZcKd8"}  # Dice public key
    new_jobs = []

    async with aiohttp.ClientSession(connector=conn, headers=headers) as session:
        for query in SEARCH_QUERIES:
            params = {
                "q": query,
                "countryCode2": "US",
                "radius": "100",
                "radiusUnit": "mi",
                "page": "1",
                "pageSize": "20",
                "facets": "employmentType|postedDate",
                "filters.postedDate": "ONE",  # Last 24 hours
                "filters.isRemote": "true",
            }
            try:
                async with session.get(
                    API_URL, params=params, ssl=ssl_ctx,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for job in data.get("data", []):
                            job_id = job.get("id", "")
                            if job_id in seen_ids:
                                continue
                            seen_ids.add(job_id)

                            title = job.get("title", "")
                            company = job.get("companyName", "")
                            desc = job.get("summary", "") or job.get("description", "")
                            detail_url = job.get("detailsPageUrl", f"https://www.dice.com/job-detail/{job_id}")
                            emp_type = job.get("employmentType", "")
                            posted = job.get("postedDate", "")

                            skills = [s.get("name", "") for s in job.get("skills", []) if s.get("name")]

                            salary = None
                            if job.get("salary"):
                                salary = job["salary"]
                            elif job.get("compensationSummary"):
                                salary = job["compensationSummary"]

                            new_jobs.append(PlatformJob(
                                platform="dice",
                                title=title,
                                description=desc[:500],
                                skills=skills,
                                budget=salary,
                                job_type=emp_type,
                                url=detail_url,
                                posted_by=company,
                                posted_time=posted,
                                location="Remote",
                                job_id=job_id,
                            ))
                    else:
                        logger.debug(f"Dice: HTTP {resp.status} for '{query}'")
            except Exception as e:
                logger.debug(f"Dice error for '{query}': {e}")

    logger.info(f"Dice: {len(new_jobs)} new jobs")
    return new_jobs
