"""Indeed job monitor via web scraping."""

import logging
import ssl
import re
from urllib.parse import quote

import aiohttp
import certifi

from .base import PlatformJob

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "remote freelance developer",
    "remote react developer contract",
    "remote python developer freelance",
    "remote full stack developer",
    "remote node.js developer contract",
]

BASE_URL = "https://www.indeed.com/jobs"


async def fetch_indeed_jobs(seen_ids: set[str]) -> list[PlatformJob]:
    """Fetch latest dev jobs from Indeed."""
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    new_jobs = []

    async with aiohttp.ClientSession(connector=conn, headers=headers) as session:
        for query in SEARCH_QUERIES:
            url = f"{BASE_URL}?q={quote(query)}&sort=date&fromage=1"
            try:
                async with session.get(url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        jobs = parse_indeed_html(html, seen_ids)
                        new_jobs.extend(jobs)
                    else:
                        logger.debug(f"Indeed: HTTP {resp.status} for '{query}'")
            except Exception as e:
                logger.debug(f"Indeed error for '{query}': {e}")

    logger.info(f"Indeed: {len(new_jobs)} new jobs")
    return new_jobs


def parse_indeed_html(html: str, seen_ids: set[str]) -> list[PlatformJob]:
    """Parse Indeed search results HTML."""
    jobs = []

    # Find job cards using regex (no BeautifulSoup dependency)
    # Indeed wraps jobs in data-jk attributes
    job_blocks = re.findall(
        r'data-jk="([^"]+)".*?jobTitle[^>]*>.*?<span[^>]*>([^<]+)</span>.*?'
        r'companyName[^>]*>([^<]+)<.*?'
        r'(?:companyLocation[^>]*>([^<]+)<)?',
        html, re.DOTALL,
    )

    for match in job_blocks:
        jk = match[0]
        if jk in seen_ids:
            continue
        seen_ids.add(jk)

        title = match[1].strip()
        company = match[2].strip() if len(match) > 2 else ""
        location = match[3].strip() if len(match) > 3 and match[3] else "Remote"

        jobs.append(PlatformJob(
            platform="indeed",
            title=title,
            description=f"{title} at {company}",
            skills=[],
            budget=None,
            job_type="contract",
            url=f"https://www.indeed.com/viewjob?jk={jk}",
            posted_by=company,
            posted_time=None,
            location=location,
            job_id=jk,
        ))

    return jobs
