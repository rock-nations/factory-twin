"""Proactive chat messages on new alarms (spec §8.6): cheap model, with a template fallback."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.llm import ConfigError, TextCompleter, Usage, require_setting
from app.models.chat import ProactiveAction, ProactiveEvent
from app.streaming.alarm_engine import AlarmRaised

logger = logging.getLogger(__name__)

PROMPT = (
    (Path(__file__).resolve().parents[1] / "prompts" / "proactive_system.md").read_text().strip()
)


def template_message(alarm: AlarmRaised, similar: dict[str, Any] | None) -> str:
    sigma = abs(alarm.sigma)
    direction = "above" if alarm.sigma >= 0 else "below"
    text = (
        f"⚠️ {alarm.machine_id} {alarm.sensor} {sigma:.1f}σ {direction} normal ({alarm.severity})."
    )
    if similar:
        text += f" Similar to failure {similar['failure']} ({similar['component'].lower()})."
    return f"{text} Ask me 'diagnose {alarm.machine_id}' for details."


def _facts(alarm: AlarmRaised, similar: dict[str, Any] | None) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "machine_id": alarm.machine_id,
        "sensor": alarm.sensor,
        "severity": alarm.severity,
        "value": round(alarm.value, 1),
        "sigma_from_normal": round(alarm.sigma, 1),
    }
    if similar:
        facts["most_similar_failure"] = {
            "failure_id": similar["failure"],
            "component": similar["component"],
            "similarity": round(similar["score"], 2),
        }
    return facts


async def build_proactive_event(
    alarm: AlarmRaised,
    similar: dict[str, Any] | None,
    llm: TextCompleter | None,
    settings: Settings | None = None,
) -> tuple[ProactiveEvent, Usage | None]:
    s = settings or get_settings()
    text, usage = template_message(alarm, similar), None
    if llm is not None:
        try:
            model = require_setting(s.openai_cheap_model, "OPENAI_CHEAP_MODEL")
            generated, usage = await asyncio.to_thread(
                llm.complete, model, PROMPT, json.dumps(_facts(alarm, similar)), 1500
            )
            if generated.strip():
                text = generated.strip()
        except ConfigError:
            logger.info("proactive message: no cheap model configured, using template")
        except Exception:
            logger.exception("proactive message generation failed, using template")
    event = ProactiveEvent(
        message=text,
        machine_id=alarm.machine_id,
        actions=[
            ProactiveAction(
                label=f"Diagnose {alarm.machine_id}",
                message=f"Diagnose {alarm.machine_id}: {alarm.sensor} alarm",
            )
        ],
    )
    return event, usage
