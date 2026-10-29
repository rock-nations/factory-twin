import json
from typing import Any

import pytest

from app.chat.memory import SessionStore
from app.chat.orchestrator import MAX_TOOL_ROUNDS, Orchestrator, route_for
from app.core.config import Settings
from app.models.chat import DoneEvent, ErrorEvent
from tests.chat_fakes import FakeTools, ScriptedModel

Q = ("query_twin_data", json.dumps({"question": "open alarms on A2?"}))
KB = ("search_knowledge_base", json.dumps({"query": "replace spindle bearing"}))
CTX = ("get_machine_context", json.dumps({"machine_id": "M017"}))
SIM = ("find_similar_incidents", json.dumps({"description": "vibration"}))

SETTINGS = Settings(_env_file=None, price_input_per_m=1.0, price_output_per_m=2.0)


def orch(model: ScriptedModel, tools: FakeTools | None = None) -> Orchestrator:
    return Orchestrator(model, tools or FakeTools(), SessionStore(), SETTINGS)


async def collect(o: Orchestrator, msg: str = "hi", sid: str = "s1") -> list[Any]:
    return [e async for e in o.run(sid, msg)]


def types(events: list[Any]) -> list[str]:
    return [e.type for e in events]


def test_route_classification() -> None:
    assert route_for(set()) == "direct"
    assert route_for({"query_twin_data"}) == "cypher"
    assert route_for({"get_machine_context"}) == "cypher"
    assert route_for({"search_knowledge_base"}) == "rag"
    assert route_for({"find_similar_incidents"}) == "rag"
    assert route_for({"search_knowledge_base", "find_similar_incidents"}) == "rag"
    assert route_for({"get_machine_context", "find_similar_incidents"}) == "hybrid"
    assert route_for({"query_twin_data", "search_knowledge_base"}) == "hybrid"


async def test_direct_route_has_no_tool_events() -> None:
    events = await collect(orch(ScriptedModel([[]], answer="Hello there")))
    assert types(events)[0] == "route" and events[0].route == "direct"
    assert types(events)[-1] == "done" and "tool_call" not in types(events)
    assert events[-1].answer == "Hello there"


async def test_event_order_for_a_cypher_question() -> None:
    model = ScriptedModel([[Q], []], answer="M017 has an alarm [1].")
    events = await collect(orch(model), "Which machines on A2 have open alarms?")
    t = types(events)
    assert t[:3] == ["route", "tool_call", "tool_result"]
    assert t[3:-1] == ["token"] * (len(t) - 4) and t[-1] == "done"
    assert events[0].route == "cypher"
    result = events[2]
    assert result.cypher and result.row_count == 1 and result.sources[0].ref == 1
    done = events[-1]
    assert done.answer == "M017 has an alarm [1]."
    assert "".join(e.delta for e in events if e.type == "token").strip() == done.answer


async def test_hybrid_route_and_parallel_tools_in_one_round() -> None:
    tools = FakeTools()
    model = ScriptedModel([[CTX, SIM, KB], []])
    events = await collect(orch(model, tools), "Diagnose M017")
    assert [e.route for e in events if e.type == "route"] == ["hybrid"]
    assert {n for n, _ in tools.calls} == {
        "get_machine_context",
        "find_similar_incidents",
        "search_knowledge_base",
    }
    done = events[-1]
    assert done.trace.route == "hybrid" and done.trace.rounds == 1 and len(done.trace.tools) == 3


async def test_route_event_is_updated_when_a_later_round_adds_a_family() -> None:
    model = ScriptedModel([[Q], [KB], []])
    events = await collect(orch(model))
    assert [e.route for e in events if e.type == "route"] == ["cypher", "hybrid"]
    assert events[-1].trace.route == "hybrid" and events[-1].trace.rounds == 2


async def test_tool_rounds_are_capped_at_four() -> None:
    model = ScriptedModel([[Q]] * 10)
    tools = FakeTools()
    events = await collect(orch(model, tools))
    assert len(model.decide_inputs) == MAX_TOOL_ROUNDS == 4
    assert len(tools.calls) == 4 and events[-1].type == "done"
    assert events[-1].trace.rounds == 4


async def test_follow_up_rounds_send_only_function_outputs_with_previous_response_id() -> None:
    model = ScriptedModel([[Q], []])
    await collect(orch(model))
    first, second = model.decide_inputs
    assert first["previous"] is None
    assert second["previous"] == "resp_1"
    (item,) = second["input"]
    assert item["type"] == "function_call_output" and item["call_id"] == "call_1_0"
    assert json.loads(item["output"])["rows"] == [{"machine": "M017"}]


async def test_trace_tokens_cost_and_llm_calls() -> None:
    model = ScriptedModel([[Q], []])
    done = (await collect(orch(model)))[-1]
    assert isinstance(done, DoneEvent)
    steps = [c.step for c in done.trace.llm_calls]
    assert steps == ["orchestrate", "text2cypher", "orchestrate", "answer"]  # chronological
    # 100+100 orchestrate, 50 text2cypher, 300 answer in; 20+20+10+50 out
    assert (done.trace.input_tokens, done.trace.output_tokens) == (550, 100)
    assert done.trace.cost_usd == pytest.approx((550 * 1.0 + 100 * 2.0) / 1e6)
    assert done.trace.llm_calls[-1].cached_tokens == 100
    assert done.trace.total_latency_ms >= 0 and set(done.trace.step_latency_ms) == {
        "orchestrate",
        "tools",
        "answer",
    }
    assert done.trace.tools[0].latency_ms == 7


async def test_memory_resolves_follow_ups() -> None:
    o = orch(ScriptedModel([[CTX], [], [], []], answer="M017 feeds M018."))
    await collect(o, "Diagnose M017", sid="a")
    model = o.model
    assert isinstance(model, ScriptedModel)
    await collect(o, "and what's downstream of it?", sid="a")
    last_input = model.decide_inputs[-1]["input"]
    assert last_input[0] == {"role": "user", "content": "Diagnose M017"}
    assert last_input[1]["role"] == "assistant" and "M018" in last_input[1]["content"]
    assert (
        "Recently referenced (most recent first): machines M018, M017" in last_input[-1]["content"]
    )
    # a different session sees none of it
    await collect(o, "hello", sid="b")
    assert len(model.decide_inputs[-1]["input"]) == 1


async def test_only_last_eight_turns_are_kept() -> None:
    o = orch(ScriptedModel([[]] * 12))
    for i in range(12):
        await collect(o, f"q{i}", sid="x")
    model = o.model
    assert isinstance(model, ScriptedModel)
    msgs = model.decide_inputs[-1]["input"]
    assert len(msgs) == 8 * 2 + 1 and msgs[0]["content"] == "q3"


async def test_answer_prompt_gets_numbered_sources_and_results() -> None:
    model = ScriptedModel([[Q, KB], []])
    await collect(orch(model))
    content = model.answer_inputs[0][-1]["content"]
    assert "Tool results:" in content and "Sources:" in content
    assert "[1] query_twin_data result (cypher)" in content or "[1] " in content
    assert '"machine": "M017"' in content


async def test_no_tools_means_the_answer_prompt_says_so() -> None:
    model = ScriptedModel([[]])
    await collect(orch(model), "thanks!")
    assert "No tools were used" in model.answer_inputs[0][-1]["content"]


async def test_failed_tool_is_reported_to_the_model_and_conversation_continues() -> None:
    model = ScriptedModel([[Q], []])
    events = await collect(orch(model, FakeTools(fail={"query_twin_data"})))
    assert events[-1].type == "done"
    assert events[-1].trace.tools[0].error == "boom"
    assert json.loads(model.decide_inputs[1]["input"][0]["output"]) == {"error": "boom"}


async def test_invalid_tool_arguments_do_not_crash() -> None:
    model = ScriptedModel([[("query_twin_data", "{not json")], []])
    tools = FakeTools()
    events = await collect(orch(model, tools))
    assert events[-1].type == "done" and tools.calls == []
    assert "valid JSON" in (events[-1].trace.tools[0].error or "")


async def test_model_failure_becomes_an_error_event() -> None:
    events = await collect(orch(ScriptedModel([], fail_on_decide=True)))
    assert types(events) == ["error"] and isinstance(events[0], ErrorEvent)
    assert "model unavailable" in events[0].message


async def test_run_to_completion_returns_answer_and_trace() -> None:
    res = await orch(ScriptedModel([[Q], []], answer="Yes [1].")).run_to_completion("s", "q")
    assert res.answer == "Yes [1]." and res.trace.route == "cypher"
