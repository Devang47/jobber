"""Base class for all platform job monitors."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PlatformJob:
    """Standardized job format across all platforms."""
    platform: str           # "discord", "reddit", "wellfound", "upwork", "freelancer"
    title: str
    description: str
    skills: list[str]
    budget: Optional[str]
    job_type: Optional[str] # fixed, hourly, contract
    url: str                # Direct link to apply/view
    posted_by: Optional[str]
    posted_time: Optional[str]
    location: Optional[str]
    job_id: str             # Unique ID to avoid duplicates
    source_name: Optional[str] = None
