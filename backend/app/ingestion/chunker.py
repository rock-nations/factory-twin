"""Markdown chunker: split on headings first, then pack/split to ~500 tokens with overlap."""

import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

TARGET_TOKENS = 500
OVERLAP_TOKENS = 60
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)


@dataclass(frozen=True)
class TextChunk:
    seq: int
    text: str
    token_count: int


@lru_cache
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def split_sections(body: str) -> list[str]:
    """Split before every markdown heading, keeping each heading with its content."""
    starts = [m.start() for m in _HEADING.finditer(body)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    bounds = starts + [len(body)]
    sections = (body[a:b].strip() for a, b in zip(bounds, bounds[1:], strict=False))
    return [s for s in sections if s]


def _split_long(text: str, target: int, overlap: int) -> list[str]:
    enc = _encoding()
    tokens = enc.encode(text)
    step = target - overlap
    pieces = []
    for start in range(0, len(tokens), step):
        pieces.append(enc.decode(tokens[start : start + target]).strip())
        if start + target >= len(tokens):
            break
    return [p for p in pieces if p]


def chunk_markdown(
    body: str, target: int = TARGET_TOKENS, overlap: int = OVERLAP_TOKENS
) -> list[TextChunk]:
    """Greedily pack whole sections up to `target` tokens; oversized sections are windowed."""
    if overlap >= target:
        raise ValueError("overlap must be smaller than target")
    texts: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            texts.append("\n\n".join(current))
            current, current_tokens = [], 0

    for section in split_sections(body):
        n = count_tokens(section)
        if n > target:
            flush()
            texts.extend(_split_long(section, target, overlap))
        elif current_tokens + n > target:
            flush()
            current, current_tokens = [section], n
        else:
            current.append(section)
            current_tokens += n
    flush()
    return [TextChunk(i, t, count_tokens(t)) for i, t in enumerate(texts)]
