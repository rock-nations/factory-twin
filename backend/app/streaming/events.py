"""Fan-out of live events to /ws/events clients."""

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventsHub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, asyncio.Lock] = {}

    def register(self, ws: WebSocket) -> None:
        self._clients[ws] = asyncio.Lock()

    def unregister(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    def __len__(self) -> int:
        return len(self._clients)

    async def send(self, ws: WebSocket, event: dict[str, Any]) -> None:
        lock = self._clients.get(ws)
        if lock is None:
            return
        async with lock:
            await ws.send_json(event)

    async def broadcast(self, event: dict[str, Any]) -> None:
        for ws in list(self._clients):
            try:
                await self.send(ws, event)
            except Exception:
                logger.info("dropping dead events connection")
                self.unregister(ws)
