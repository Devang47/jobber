"""
Helper script to resolve Discord invite codes to server IDs.
Run this once to get the server IDs for servers where only invite codes are known.

Usage: python3 get_server_ids.py
"""

import asyncio
import ssl
import certifi
import aiohttp

INVITES = [
    ("Devs For Hire & Jobs", "awHZtSf89q"),
    ("NextJob", "ZEH6eJucrF"),
    ("Hire Developers", "hiredevelopers"),
]


async def resolve_invite(session: aiohttp.ClientSession, name: str, code: str):
    url = f"https://discord.com/api/v10/invites/{code}"
    async with session.get(url) as resp:
        if resp.status == 200:
            data = await resp.json()
            guild = data.get("guild", {})
            guild_id = guild.get("id", "unknown")
            guild_name = guild.get("name", name)
            members = data.get("approximate_member_count", "?")
            print(f"{guild_name}: {guild_id}  ({members} members)")
            return guild_id
        else:
            print(f"{name}: Failed to resolve (HTTP {resp.status})")
            return None


async def main():
    print("Resolving Discord invite codes to server IDs...\n")
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=conn) as session:
        for name, code in INVITES:
            await resolve_invite(session, name, code)
    print("\nAdd these IDs to DISCORD_SERVER_IDS in your .env file")


if __name__ == "__main__":
    asyncio.run(main())
