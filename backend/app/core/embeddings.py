"""Embedding client with an on-disk cache keyed by content hash (no re-spend on re-runs)."""

import hashlib
import logging
import sqlite3
import struct
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from openai import OpenAI

from app.core.config import REPO_ROOT, Settings, get_settings
from app.core.llm import make_client, require_setting

logger = logging.getLogger(__name__)

CACHE_PATH = REPO_ROOT / ".cache" / "embeddings.sqlite"
API_BATCH = 100


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(
        self,
        settings: Settings | None = None,
        client: OpenAI | None = None,
        cache_path: Path | None = CACHE_PATH,
    ) -> None:
        self.settings = settings or get_settings()
        self.model = require_setting(self.settings.openai_embedding_model, "OPENAI_EMBEDDING_MODEL")
        self.dims = self.settings.openai_embedding_dimensions
        self._client = client
        self.api_calls = 0
        self.cache_hits = 0
        self._db: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            # chat tools call this from worker threads, so share one connection behind a lock
            self._db = sqlite3.connect(cache_path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS emb (key TEXT PRIMARY KEY, vec BLOB)")

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = make_client(self.settings)
        return self._client

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}|{self.dims}|{text}".encode()).hexdigest()

    def _get(self, key: str) -> list[float] | None:
        if self._db is None:
            return None
        with self._lock:
            row = self._db.execute("SELECT vec FROM emb WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        blob: bytes = row[0]
        return list(struct.unpack(f"{len(blob) // 4}f", blob))

    def _put(self, key: str, vec: list[float]) -> None:
        if self._db is not None:
            with self._lock:
                self._db.execute(
                    "INSERT OR REPLACE INTO emb VALUES (?, ?)",
                    (key, struct.pack(f"{len(vec)}f", *vec)),
                )
                self._db.commit()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        keys = [self._key(t) for t in texts]
        out: dict[int, list[float]] = {}
        missing: list[int] = []
        for i, k in enumerate(keys):
            cached = self._get(k)
            if cached is not None:
                out[i] = cached
                self.cache_hits += 1
            else:
                missing.append(i)
        for start in range(0, len(missing), API_BATCH):
            idxs = missing[start : start + API_BATCH]
            resp = self.client.embeddings.create(
                model=self.model, input=[texts[i] for i in idxs], dimensions=self.dims
            )
            self.api_calls += 1
            for i, item in zip(idxs, resp.data, strict=True):
                out[i] = item.embedding
                self._put(keys[i], item.embedding)
        return [out[i] for i in range(len(texts))]
