import hashlib
import math
import re
from collections.abc import Sequence

from app.core.llm import Usage

DIMS = 1536


class HashingEmbedder:
    """Offline bag-of-words embedder: lexical overlap -> cosine similarity. Test-only."""

    def __init__(self) -> None:
        self.calls = 0
        self.texts_embedded = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.texts_embedded += len(texts)
        out = []
        for t in texts:
            v = [0.0] * DIMS
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIMS] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | list[dict[str, str]]]] = []

    def complete(
        self,
        model: str,
        system: str,
        user: str | list[dict[str, str]],
        max_output_tokens: int = 2500,
    ) -> tuple[str, Usage]:
        self.calls.append((model, system, user))
        return "## Overview\n\nGenerated body.", Usage(100, 50)
