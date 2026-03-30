"""Upwork job monitor via RSS feeds."""

import logging
import ssl
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import quote

import aiohttp
import certifi

from .base import PlatformJob

logger = logging.getLogger(__name__)

# Dev-related search queries to rotate
SEARCH_QUERIES = [
    "react developer",
    "full stack developer",
    "node.js developer",
    "python developer",
    "next.js developer",
    "web scraping automation",
    "bot development",
    "frontend developer",
    "backend developer",
    "javascript developer",
    "typescript developer",
    "web application",
]

BASE_RSS_URL = "https://www.upwork.com/ab/feed/jobs/rss"


def build_rss_url(query: str) -> str:
    return f"{BASE_RSS_URL}?q={quote(query)}&sort=recency&paging=0%3B20"


def parse_rss(xml_text: str) -> list[dict]:
    """Parse Upwork RSS XML into job dicts."""
    jobs = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            desc = unescape(item.findtext("description", ""))
            pub_date = item.findtext("pubDate", "")

            # Extract budget from description
            budget = None
            for line in desc.split("<br"):
                clean = unescape(line).replace("/>", "").strip()
                if "Budget" in clean or "Hourly" in clean:
                    budget = clean.split(":")[-1].strip() if ":" in clean else clean

            # Clean description
            import re
            clean_desc = re.sub(r"<[^>]+>", " ", desc)
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()

            jobs.append({
                "title": title,
                "description": clean_desc[:500],
                "link": link,
                "budget": budget,
                "pub_date": pub_date,
            })
    except ET.ParseError as e:
        logger.error(f"RSS parse error: {e}")
    return jobs


async def fetch_upwork_jobs(seen_ids: set[str]) -> list[PlatformJob]:
    """Fetch latest Upwork jobs across all search queries."""
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    new_jobs = []

    async with aiohttp.ClientSession(connector=conn) as session:
        for query in SEARCH_QUERIES:
            url = build_rss_url(query)
            try:
                async with session.get(url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        xml_text = await resp.text()
                        raw_jobs = parse_rss(xml_text)

                        for job in raw_jobs:
                            job_id = job["link"].split("~")[-1] if "~" in job["link"] else job["link"]
                            if job_id in seen_ids:
                                continue
                            seen_ids.add(job_id)

                            new_jobs.append(PlatformJob(
                                platform="upwork",
                                title=job["title"],
                                description=job["description"],
                                skills=[],
                                budget=job["budget"],
                                job_type="hourly" if "Hourly" in (job["budget"] or "") else "fixed",
                                url=job["link"],
                                posted_by=None,
                                posted_time=job["pub_date"],
                                location="Remote",
                                job_id=job_id,
                            ))
                    elif resp.status == 403:
                        logger.debug(f"Upwork RSS blocked for query: {query}")
                    else:
                        logger.debug(f"Upwork RSS {resp.status} for: {query}")
            except Exception as e:
                logger.debug(f"Upwork fetch error for '{query}': {e}")

    logger.info(f"Upwork: {len(new_jobs)} new jobs")
    return new_jobs
