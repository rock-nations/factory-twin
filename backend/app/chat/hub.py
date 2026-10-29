"""Connected /ws/chat clients, so the backend can push proactive messages (spec §8.6)."""

import asyncio
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

from app.models.chat import ChatEvent

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class ChatConnection:
    ws: WebSocket
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, event: ChatEvent) -> None:
        async with self.lock:  # streaming tokens and proactive pushes must not interleave mid-send
            await self.ws.send_json(event.model_dump(mode="json", exclude_none=True))


class ChatHub:
    def __init__(self) -> None:
        self._connections: set[ChatConnection] = set()

    def register(self, ws: WebSocket) -> ChatConnection:
        conn = ChatConnection(ws)
        self._connections.add(conn)
        return conn

    def unregister(self, conn: ChatConnection) -> None:
        self._connections.discard(conn)

    def __len__(self) -> int:
        return len(self._connections)

    async def broadcast(self, event: ChatEvent) -> int:
        """Send to every connected chat; returns how many received it."""
        delivered = 0
        for conn in list(self._connections):
            try:
                await conn.send(event)
                delivered += 1
            except Exception:
                logger.info("dropping dead chat connection")
                self._connections.discard(conn)
        return delivered
