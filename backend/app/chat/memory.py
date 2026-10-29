"""In-memory conversation memory per session: last 8 turns + recently referenced entities."""

import re
from collections import OrderedDict, deque
from dataclasses import dataclass, field

MAX_TURNS = 8
MAX_SESSIONS = 500
MAX_ENTITIES = 6
ASSISTANT_SNIPPET_CHARS = 500

MACHINE_RE = re.compile(r"\bM\d{3}\b")
LINE_RE = re.compile(r"\b[AB][1-4]\b")


@dataclass
class Turn:
    user: str
    assistant: str


@dataclass
class Session:
    turns: deque[Turn] = field(default_factory=lambda: deque(maxlen=MAX_TURNS))
    machines: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    def remember_entities(self, *texts: str) -> None:
        for text in texts:
            for found, store in (
                (MACHINE_RE.findall(text), self.machines),
                (LINE_RE.findall(text), self.lines),
            ):
                for item in found:
                    if item in store:
                        store.remove(item)
                    store.append(item)  # most recent last
                del store[:-MAX_ENTITIES]

    def entity_hint(self) -> str:
        parts = []
        if self.machines:
            parts.append("machines " + ", ".join(reversed(self.machines)))
        if self.lines:
            parts.append("lines " + ", ".join(reversed(self.lines)))
        return "Recently referenced (most recent first): " + "; ".join(parts) if parts else ""

    def history_messages(self) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = []
        for t in self.turns:
            msgs.append({"role": "user", "content": t.user})
            snippet = t.assistant[:ASSISTANT_SNIPPET_CHARS]
            msgs.append({"role": "assistant", "content": snippet})
        return msgs


class SessionStore:
    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._max = max_sessions

    def get(self, session_id: str) -> Session:
        session = self._sessions.pop(session_id, None) or Session()
        self._sessions[session_id] = session  # most recently used last
        while len(self._sessions) > self._max:
            self._sessions.popitem(last=False)
        return session

    def __len__(self) -> int:
        return len(self._sessions)
