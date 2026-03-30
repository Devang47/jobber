"""
Per-user profile management.
Each user (by phone number) has their own name, GitHub, portfolio, rate, skills.
Stored in profiles.json.
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

PROFILES_FILE = os.path.join(os.path.dirname(__file__), "profiles.json")

DEFAULT_PROFILE = {
    "name": "",
    "github": "",
    "portfolio": "",
    "rate": "",
    "skills": "",
}


def load_profiles() -> dict[str, dict]:
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_profiles(profiles: dict[str, dict]):
    with open(PROFILES_FILE, "w") as f:
        json.dump(profiles, f, indent=2)


def get_profile(phone: str) -> dict:
    profiles = load_profiles()
    return profiles.get(phone, DEFAULT_PROFILE.copy())


def set_profile_field(phone: str, field: str, value: str):
    profiles = load_profiles()
    if phone not in profiles:
        profiles[phone] = DEFAULT_PROFILE.copy()
    profiles[phone][field] = value
    save_profiles(profiles)


def find_phone_by_name(name: str) -> str | None:
    """Find a phone number by profile name (case-insensitive)."""
    profiles = load_profiles()
    name_lower = name.lower().strip()
    for phone, profile in profiles.items():
        if profile.get("name", "").lower().strip() == name_lower:
            return phone
    return None


def list_all_profiles() -> str:
    """List all registered users."""
    profiles = load_profiles()
    if not profiles:
        return "*No users registered yet.*"
    lines = ["*Registered Users:*\n"]
    for phone, p in profiles.items():
        name = p.get("name", "Unknown")
        lines.append(f"• *{name}* — {phone}")
    return "\n".join(lines)


def format_profile(phone: str) -> str:
    p = get_profile(phone)
    if not any(p.values()):
        return "*No profile set yet.*\n\nSet up with:\n• *set name Your Name*\n• *set github https://github.com/you*\n• *set portfolio https://yoursite.com*\n• *set rate $20-30/hr*\n• *set skills React, Node.js, Python*"

    lines = ["*Your Profile:*\n"]
    lines.append(f"*Name:* {p.get('name') or 'Not set'}")
    lines.append(f"*GitHub:* {p.get('github') or 'Not set'}")
    lines.append(f"*Portfolio:* {p.get('portfolio') or 'Not set'}")
    lines.append(f"*Rate:* {p.get('rate') or 'Not set'}")
    lines.append(f"*Skills:* {p.get('skills') or 'Not set'}")
    return "\n".join(lines)
