"""
Per-user profile management.
Each user (by Telegram user ID) has their own name, GitHub, portfolio, rate, skills.
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


def find_user_by_name(name: str) -> str | None:
    """Find a user ID by profile name (case-insensitive)."""
    profiles = load_profiles()
    name_lower = name.lower().strip()
    for user_id, profile in profiles.items():
        if profile.get("name", "").lower().strip() == name_lower:
            return user_id
    return None


def list_all_profiles() -> str:
    """List all registered users (HTML formatted)."""
    profiles = load_profiles()
    if not profiles:
        return "<b>No users registered yet.</b>"
    lines = ["<b>Registered Users</b>\n"]
    for user_id, p in profiles.items():
        name = p.get("name", "Unknown")
        skills = p.get("skills", "")
        entry = f"  <b>{name}</b>"
        if skills:
            entry += f" — {skills}"
        lines.append(entry)
    return "\n".join(lines)


def format_profile(user_id: str) -> str:
    """Format a user's profile (HTML formatted)."""
    p = get_profile(user_id)
    if not any(p.values()):
        return (
            "<b>No profile set yet.</b>\n\n"
            "Set up with:\n"
            "  /set name Your Name\n"
            "  /set github https://github.com/you\n"
            "  /set portfolio https://yoursite.com\n"
            "  /set rate $20-30/hr\n"
            "  /set skills React, Node.js, Python"
        )

    lines = ["<b>Your Profile</b>\n"]
    lines.append(f"<b>Name:</b>  {p.get('name') or 'Not set'}")
    lines.append(f"<b>GitHub:</b>  {p.get('github') or 'Not set'}")
    lines.append(f"<b>Portfolio:</b>  {p.get('portfolio') or 'Not set'}")
    lines.append(f"<b>Rate:</b>  {p.get('rate') or 'Not set'}")
    lines.append(f"<b>Skills:</b>  {p.get('skills') or 'Not set'}")
    return "\n".join(lines)
