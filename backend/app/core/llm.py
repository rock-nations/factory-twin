"""Thin OpenAI wrappers: model names come from settings, usage/cost is always returned."""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


def estimate_cost(usage: Usage, settings: Settings | None = None) -> float:
    """USD, from PRICE_INPUT_PER_M / PRICE_OUTPUT_PER_M (0 if prices are not configured)."""
    s = settings or get_settings()
    return (
        usage.input_tokens * s.price_input_per_m + usage.output_tokens * s.price_output_per_m
    ) / 1_000_000


def require_setting(value: str, env_name: str) -> str:
    if not value:
        raise ConfigError(f"{env_name} is not set; add it to .env")
    return value


def reasoning_kwargs(settings: Settings) -> dict[str, Any]:
    """Responses-API `reasoning` argument, only when OPENAI_REASONING_EFFORT is set."""
    effort = settings.openai_reasoning_effort.strip()
    return {"reasoning": {"effort": effort}} if effort else {}


def make_client(settings: Settings | None = None) -> OpenAI:
    s = settings or get_settings()
    return OpenAI(api_key=require_setting(s.openai_api_key, "OPENAI_API_KEY"))


class TextCompleter(Protocol):
    def complete(
        self,
        model: str,
        system: str,
        user: str | list[dict[str, str]],
        max_output_tokens: int = ...,
    ) -> tuple[str, Usage]: ...


class LLM:
    """Single-shot text generation through the Responses API."""

    def __init__(self, settings: Settings | None = None, client: OpenAI | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client or make_client(self.settings)

    def complete(
        self,
        model: str,
        system: str,
        user: str | list[dict[str, str]],
        max_output_tokens: int = 2500,
    ) -> tuple[str, Usage]:
        """`user` is a plain string or a list of {"role", "content"} messages (multi-turn)."""
        resp = self.client.responses.create(
            model=model,
            instructions=system,
            input=user,  # type: ignore[arg-type]
            max_output_tokens=max_output_tokens,
            **reasoning_kwargs(self.settings),
        )
        if resp.status == "incomplete" and not resp.output_text.strip():
            # reasoning models can spend the whole budget thinking and return nothing
            raise RuntimeError(
                f"{model} hit max_output_tokens={max_output_tokens} before producing text; "
                "raise the limit or set OPENAI_REASONING_EFFORT=low"
            )
        u = resp.usage
        usage = Usage(u.input_tokens, u.output_tokens) if u else Usage()
        return resp.output_text, usage
