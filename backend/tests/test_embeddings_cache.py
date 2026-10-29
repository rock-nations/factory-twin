from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.core.config import Settings
from app.core.embeddings import OpenAIEmbedder


class FakeClient:
    """Stands in for the OpenAI client: one deterministic vector per text."""

    def __init__(self) -> None:
        self.calls = 0
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, model: str, input: list[str], dimensions: int) -> Any:
        self.calls += 1
        data = [SimpleNamespace(embedding=[float(len(t))] * 4) for t in input]
        return SimpleNamespace(data=data)


def make(tmp_path: Path) -> tuple[OpenAIEmbedder, FakeClient]:
    client = FakeClient()
    settings = Settings(_env_file=None, openai_embedding_model="m")
    return OpenAIEmbedder(settings, client=client, cache_path=tmp_path / "c.sqlite"), client  # type: ignore[arg-type]


def test_cache_hits_skip_the_api(tmp_path: Path) -> None:
    emb, client = make(tmp_path)
    emb.embed(["alpha", "beta"])
    emb.embed(["alpha", "beta", "gamma"])
    assert client.calls == 2 and emb.cache_hits == 2


def test_cache_is_usable_from_worker_threads(tmp_path: Path) -> None:
    # chat tools run in asyncio.to_thread workers; a thread-bound sqlite connection broke every call
    emb, _ = make(tmp_path)
    emb.embed(["warm"])  # connection created on the main thread
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: emb.embed([f"text-{i % 3}", "warm"]), range(24)))
    assert all(r[1] == [4.0] * 4 for r in results)
