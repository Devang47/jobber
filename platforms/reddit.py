"""Reddit job monitor — r/forhire, r/freelance, r/remotejs, etc."""

import logging
import ssl
import re
import time

import aiohttp
import certifi

from .base import PlatformJob

logger = logging.getLogger(__name__)

# Subreddits to monitor for dev freelancing jobs
SUBREDDITS = [
    "forhire",
    "freelance_forhire",
    "remotejs",
    "webdev",
    "reactjs",
    "node",
    "django",
    "nextjs",
    "SideProject",
    "startups",
    "remotework",
    "WorkOnline",
]

DEV_KEYWORDS = re.compile(
    r"(developer|frontend|backend|full.?stack|react|next\.?js|node|python|javascript|"
    r"typescript|web\s*dev|software|api|automation|scraping|bot|webapp|saas|"
    r"django|flask|laravel|vue|angular|website|web\s*app|landing\s*page|dashboard|"
    r"mobile\s*app|react\s*native|flutter|wordpress|shopify)",
    re.IGNORECASE,
)

# Only [Hiring] posts, not [For Hire]
HIRING_TAG = re.compile(r"\[hiring\]", re.IGNORECASE)
FOR_HIRE_TAG = re.compile(r"\[for\s*hire\]", re.IGNORECASE)


async def fetch_reddit_jobs(seen_ids: set[str]) -> list[PlatformJob]:
    """Fetch latest hiring posts from freelancing subreddits."""
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    headers = {"User-Agent": "JobMonitor/1.0"}
    new_jobs = []

    async with aiohttp.ClientSession(connector=conn, headers=headers) as session:
        for sub in SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/new.json?limit=25"
            try:
                async with session.get(url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        posts = data.get("data", {}).get("children", [])

                        for post in posts:
                            p = post.get("data", {})
                            post_id = p.get("id", "")
                            title = p.get("title", "")
                            selftext = p.get("selftext", "")
                            permalink = p.get("permalink", "")
                            author = p.get("author", "")
                            created = p.get("created_utc", 0)

                            if post_id in seen_ids:
                                continue

                            # Skip posts older than 24 hours
                            if created and (time.time() - created) > 86400:
                                continue

                            # Skip [For Hire] posts (people looking for work)
                            if FOR_HIRE_TAG.search(title):
                                continue

                            # Must be [Hiring] OR contain dev keywords
                            is_hiring = bool(HIRING_TAG.search(title))
                            has_dev = bool(DEV_KEYWORDS.search(title + " " + selftext))

                            if not (is_hiring and has_dev):
                                continue

                            seen_ids.add(post_id)

                            # Extract budget from title/text
                            budget = None
                            budget_match = re.search(r"\$[\d,.]+(?:\s*[-–]\s*\$?[\d,.]+)?(?:\s*/\s*hr)?", title + " " + selftext)
                            if budget_match:
                                budget = budget_match.group(0)

                            new_jobs.append(PlatformJob(
                                platform="reddit",
                                title=title,
                                description=selftext[:500],
                                skills=[],
                                budget=budget,
                                job_type=None,
                                url=f"https://reddit.com{permalink}",
                                posted_by=f"u/{author}",
                                posted_time=None,
                                location="Remote",
                                job_id=post_id,
                            ))
                    elif resp.status == 429:
                        logger.debug(f"Reddit rate limited on r/{sub}")
                    else:
                        logger.debug(f"Reddit r/{sub}: HTTP {resp.status}")
            except Exception as e:
                logger.debug(f"Reddit r/{sub} error: {e}")

    logger.info(f"Reddit: {len(new_jobs)} new jobs")
    return new_jobs
