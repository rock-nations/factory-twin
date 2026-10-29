import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.chat.service import get_hub, get_orchestrator
from app.core.llm import ConfigError
from app.models.chat import ChatRequest, ChatResponse, ErrorEvent

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Non-streaming fallback."""
    try:
        orchestrator = await asyncio.to_thread(get_orchestrator, request.app)
        return await orchestrator.run_to_completion(body.session_id, body.message)
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.websocket("/ws/chat")
async def chat_socket(ws: WebSocket) -> None:
    await ws.accept()
    hub = get_hub(ws.app)
    conn = hub.register(ws)
    try:
        while True:
            data = await ws.receive_json()
            try:
                if data.get("type") != "user_message":
                    raise ValueError("expected a message of type 'user_message'")
                request = ChatRequest(
                    session_id=data.get("session_id"), message=data.get("message")
                )
            except (ValueError, ValidationError, AttributeError) as exc:
                await conn.send(ErrorEvent(message=f"invalid message: {exc}"))
                continue
            try:
                orchestrator = await asyncio.to_thread(get_orchestrator, ws.app)
            except Exception as exc:
                logger.warning("chat unavailable: %s", exc)
                await conn.send(ErrorEvent(message=str(exc)))
                continue
            async for event in orchestrator.run(request.session_id, request.message):
                await conn.send(event)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(conn)
