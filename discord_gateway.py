import asyncio
import json
import logging
import random
import ssl
from typing import AsyncGenerator

import aiohttp
import certifi

logger = logging.getLogger(__name__)


class DiscordGateway:
    GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

    def __init__(self, token: str, server_ids: list[int]):
        self._token = token
        self._server_ids = set(server_ids)
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._heartbeat_interval: float = 41.25
        self._sequence: int | None = None
        self._session_id: str | None = None
        self._resume_gateway_url: str | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._heartbeat_acked: bool = True
        # Cache for guild/channel names
        self._guild_names: dict[int, str] = {}
        self._channel_names: dict[int, str] = {}

    @property
    def can_resume(self) -> bool:
        return self._session_id is not None and self._sequence is not None

    def _ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=certifi.where())

    async def connect(self) -> None:
        """Establish WebSocket connection and perform full handshake."""
        ssl_ctx = self._ssl_context()
        conn = aiohttp.TCPConnector(ssl=ssl_ctx)
        self._session = aiohttp.ClientSession(connector=conn)
        self._ws = await self._session.ws_connect(self.GATEWAY_URL, ssl=ssl_ctx)

        # Receive HELLO (opcode 10)
        hello = await self._receive()
        if hello["op"] != 10:
            raise ConnectionError(f"Expected HELLO (op 10), got op {hello['op']}")
        self._heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000.0
        logger.info(f"Heartbeat interval: {self._heartbeat_interval:.1f}s")

        # Start heartbeat early (Discord expects it right after HELLO)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Send IDENTIFY
        await self._identify()

        # Wait for READY — Discord may send other events first
        for _ in range(50):
            data = await self._receive()
            op = data.get("op")

            if op == 11:  # HEARTBEAT ACK
                self._heartbeat_acked = True
                continue

            if op == 0 and data.get("t") == "READY":
                self._session_id = data["d"]["session_id"]
                self._resume_gateway_url = data["d"].get("resume_gateway_url")
                user = data["d"].get("user", {})
                logger.info(
                    f"Connected as {user.get('username')}#{user.get('discriminator')} "
                    f"(session: {self._session_id[:8]}...)"
                )
                for guild in data["d"].get("guilds", []):
                    gid = int(guild["id"])
                    if gid in self._server_ids:
                        self._guild_names[gid] = guild.get("name", str(gid))
                break

            if op == 9:  # INVALID SESSION
                raise ConnectionError("IDENTIFY rejected — invalid token or session")

            logger.debug(f"Handshake: skipping op={op} t={data.get('t')}")
        else:
            logger.warning("Never received READY after 50 messages — continuing anyway")

    async def resume(self) -> None:
        """Resume a disconnected session."""
        url = self._resume_gateway_url or self.GATEWAY_URL
        ssl_ctx = self._ssl_context()
        conn = aiohttp.TCPConnector(ssl=ssl_ctx)
        self._session = aiohttp.ClientSession(connector=conn)
        self._ws = await self._session.ws_connect(url, ssl=ssl_ctx)

        hello = await self._receive()
        if hello["op"] != 10:
            raise ConnectionError(f"Expected HELLO on resume, got op {hello['op']}")
        self._heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000.0

        await self._ws.send_json(
            {
                "op": 6,
                "d": {
                    "token": self._token,
                    "session_id": self._session_id,
                    "seq": self._sequence,
                },
            }
        )
        logger.info("Sent RESUME, waiting for replay...")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _identify(self) -> None:
        payload = {
            "op": 2,
            "d": {
                "token": self._token,
                "properties": {
                    "os": "Mac OS X",
                    "browser": "Chrome",
                    "device": "",
                },
                "presence": {
                    "status": "online",
                    "afk": False,
                },
            },
        }
        await self._ws.send_json(payload)
        logger.debug("Sent IDENTIFY")

    async def _heartbeat_loop(self) -> None:
        # Initial jitter before first heartbeat
        await asyncio.sleep(self._heartbeat_interval * random.random())
        missed = 0
        while True:
            self._heartbeat_acked = False
            await self._ws.send_json({"op": 1, "d": self._sequence})
            logger.debug(f"Heartbeat sent (seq={self._sequence})")
            await asyncio.sleep(self._heartbeat_interval)
            if not self._heartbeat_acked:
                missed += 1
                logger.warning(f"Heartbeat not ACKed (missed={missed})")
                if missed >= 2:
                    logger.warning("2 heartbeats missed — zombie connection, closing")
                    await self._ws.close()
                    return
            else:
                missed = 0

    async def _receive(self) -> dict:
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(msg.data)
            if data.get("s") is not None:
                self._sequence = data["s"]
            return data
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            raise ConnectionError(f"WebSocket closed: {msg.type}")
        elif msg.type == aiohttp.WSMsgType.CLOSING:
            raise ConnectionError("WebSocket is closing")
        return {}

    async def listen(self) -> AsyncGenerator[dict, None]:
        """Yield MESSAGE_CREATE events from monitored servers."""
        while True:
            try:
                data = await self._receive()
            except ConnectionError:
                raise
            except Exception as e:
                logger.error(f"Error receiving message: {e}")
                raise ConnectionError(str(e))

            op = data.get("op")

            if op == 0:  # DISPATCH
                event_type = data.get("t")

                # Cache channel names from GUILD_CREATE events
                if event_type == "GUILD_CREATE":
                    guild_data = data["d"]
                    gid = int(guild_data["id"])
                    self._guild_names[gid] = guild_data.get("name", str(gid))
                    for ch in guild_data.get("channels", []):
                        self._channel_names[int(ch["id"])] = ch.get("name", str(ch["id"]))

                if event_type == "MESSAGE_CREATE":
                    msg_data = data["d"]
                    guild_id = int(msg_data.get("guild_id", 0))
                    if guild_id in self._server_ids:
                        # Enrich with cached names
                        msg_data["_server_name"] = self._guild_names.get(guild_id, str(guild_id))
                        channel_id = int(msg_data.get("channel_id", 0))
                        msg_data["_channel_name"] = self._channel_names.get(
                            channel_id, str(channel_id)
                        )
                        yield msg_data

            elif op == 1:  # Server requests heartbeat
                await self._ws.send_json({"op": 1, "d": self._sequence})

            elif op == 7:  # RECONNECT
                logger.info("Server requested RECONNECT")
                raise ConnectionError("Server requested reconnect")

            elif op == 9:  # INVALID SESSION
                resumable = data.get("d", False)
                if not resumable:
                    self._session_id = None
                    self._sequence = None
                logger.warning(f"Invalid session (resumable={resumable})")
                raise ConnectionError("Invalid session")

            elif op == 11:  # HEARTBEAT ACK
                self._heartbeat_acked = True
                logger.debug("Heartbeat ACKed")

    async def close(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("Gateway connection closed")
