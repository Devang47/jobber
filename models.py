from dataclasses import dataclass
from typing import Optional


@dataclass
class JobPosting:
    title: str
    description: str
    skills: list[str]
    pay: Optional[str]
    deadline: Optional[str]
    contact_info: Optional[str]
    job_type: str
    experience_level: Optional[str]
    raw_snippet: str
    source_server: str
    source_channel: str
    source_author: str
    source_author_id: str
    message_url: str
    job_id: str = ""
