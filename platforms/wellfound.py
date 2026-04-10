"""Wellfound (AngelList) job monitor via GraphQL API."""

import asyncio
import json
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

# Roles to search for
ROLE_SLUGS = [
    "software-engineer",
    "full-stack-engineer",
    "frontend-engineer",
    "backend-engineer",
    "python-developer",
    "javascript-developer",
    "react-developer",
    "node-js-developer",
    "web-developer",
    "devops-engineer",
    "api-developer",
]

ROLE_URL = "https://wellfound.com/role/r/{slug}"


async def fetch_wellfound_jobs(seen_ids: set[str]) -> list[PlatformJob]:
    """Fetch latest remote dev jobs from Wellfound."""
    if aiohttp is None:
        raise RuntimeError("aiohttp is required to fetch Wellfound jobs")
    ssl_ctx = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    new_jobs = []

    async with aiohttp.ClientSession(connector=conn, headers=headers) as session:
        for slug in ROLE_SLUGS:
            # Wellfound embeds job data as JSON in their HTML pages
            url = ROLE_URL.format(slug=slug)
            try:
                async with session.get(url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        log_api_event("wellfound", "role_page", resp.status, payload=html, role_slug=slug)
                        jobs = parse_wellfound_html(html, seen_ids, slug)
                        new_jobs.extend(jobs)
                    else:
                        log_api_event("wellfound", "role_page", resp.status, role_slug=slug)
                        logger.debug(f"Wellfound {slug}: HTTP {resp.status}")
            except Exception as e:
                log_api_event("wellfound", "role_page", "exception", role_slug=slug, error=str(e))
                logger.debug(f"Wellfound {slug} error: {e}")
            await asyncio.sleep(1)  # Be gentle

    logger.info(f"Wellfound: {len(new_jobs)} new jobs")
    return new_jobs


def parse_wellfound_html(html: str, seen_ids: set[str], role_slug: str) -> list[PlatformJob]:
    """Parse Wellfound job listing page for embedded JSON data."""
    import re
    jobs = []

    # Wellfound embeds Apollo state as JSON in __NEXT_DATA__ script tag
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not match:
        return jobs

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return jobs

    # Try to find job listings in various data structures
    apollo_state = data.get("props", {}).get("pageProps", {}).get("apolloState", {})
    if not apollo_state:
        apollo_state = data.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__", {})

    for key, value in apollo_state.items():
        if not isinstance(value, dict):
            continue

        # Look for JobListing type
        typename = value.get("__typename", "")
        if typename not in ("JobListingSearchResult", "JobListing", "StartupJobPosting"):
            continue

        job_id = str(value.get("id", key))
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        title = value.get("title", "")
        if not title:
            continue

        remote = value.get("remote", False)
        desc = value.get("description", "") or value.get("snippet", "")
        compensation = value.get("compensation", "")
        job_type = value.get("jobType", "")
        slug = value.get("slug", "")

        # Get company info
        company_name = ""
        company_ref = value.get("startup")
        if isinstance(company_ref, dict):
            company_name = company_ref.get("name", "")
        elif isinstance(company_ref, str) and company_ref in apollo_state:
            company_data = apollo_state[company_ref]
            if isinstance(company_data, dict):
                company_name = company_data.get("name", "")

        # Build URL
        if slug and company_name:
            company_slug = company_name.lower().replace(" ", "-").replace(".", "")
            job_url = f"https://wellfound.com/company/{company_slug}/jobs/{job_id}"
        else:
            job_url = f"https://wellfound.com/jobs?role={role_slug}"

        jobs.append(PlatformJob(
            platform="wellfound",
            title=title,
            description=desc[:500] if desc else f"{title} at {company_name}",
            skills=[],
            budget=compensation if compensation else None,
            job_type=job_type,
            url=job_url,
            posted_by=company_name,
            posted_time=None,
            location="Remote" if remote else "Unknown",
            job_id=job_id,
        ))

    return jobs
