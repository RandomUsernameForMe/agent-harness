import pytest
from claude_agent_sdk import (AssistantMessage, RateLimitEvent, RateLimitInfo, ResultMessage, SystemMessage, TextBlock,
                              ThinkingBlock, ToolResultBlock, ToolUseBlock, UserMessage)

from harness import sdk, trace
from harness.graph import RateLimited, _pausing


def result(is_error=False, **kw):
    return ResultMessage("success", 1200, 1000, is_error, 4, "sess-1", total_cost_usd=0.25, result="hotovo",
                         usage={"input_tokens": 12, "output_tokens": 300, "cache_read_input_tokens": 9000,
                                "cache_creation_input_tokens": 700}, **kw)


def stream(*msgs):
    async def query(prompt, options):
        for m in msgs:
            if isinstance(m, BaseException):
                raise m
            yield m
    return query


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(trace, "_sink", lambda call, steps: calls.append((call, steps)))
    return calls


def run(monkeypatch, *msgs):
    monkeypatch.setattr(sdk, "query", stream(*msgs))
    return sdk.run_agent("p", cwd=".", model="opus", tools=[])


TOOL_MSGS = (
    AssistantMessage([ThinkingBlock("hmm", "sig"), TextBlock("Projdu kód."),
                      ToolUseBlock("t1", "Read", {"file_path": "lib/x.ts" + "a" * 400})], "opus"),
    UserMessage([ToolResultBlock("t1", "obsah souboru", False)]),
    AssistantMessage([ToolUseBlock("t2", "Grep", {"pattern": "TODO"})], "opus"),
    UserMessage([ToolResultBlock("t2", [{"type": "text", "text": "nic"}], True)]),
)


def test_steps_and_cache_tokens_recorded(monkeypatch, recorded):
    with trace.context(trace.Trace("run-1", "plan", "gp-01")):
        r = run(monkeypatch, *TOOL_MSGS, result())
    assert r.usage["cache_read_tokens"] == 9000 and r.usage["cache_write_tokens"] == 700
    call, steps = recorded[0]
    assert call | {"started": 0} == {
        "run_id": "run-1", "node": "plan", "worker": "gp-01", "model": "opus", "started": 0, "duration_ms": 1200,
        "num_turns": 4, "input_tokens": 12, "output_tokens": 300, "cache_read_tokens": 9000,
        "cache_write_tokens": 700, "cost_usd": 0.25, "status": "ok", "error": None, "session_id": "sess-1",
        "billing": None}
    assert [(s["kind"], s["tool"], s["is_error"]) for s in steps] == [
        ("text", None, False), ("tool_use", "Read", False), ("tool_result", "Read", False),
        ("tool_use", "Grep", False), ("tool_result", "Grep", True)]
    assert len(steps[1]["summary"]) == trace.TOOL_CHARS and steps[1]["summary"].startswith('{"file_path": "lib/x.ts')
    assert steps[2]["summary"] == "obsah souboru" and steps[4]["summary"] == "nic"


def test_failed_call_recorded_and_raised(monkeypatch, recorded):
    with pytest.raises(RuntimeError, match="agent error"):
        run(monkeypatch, *TOOL_MSGS[:2], result(is_error=True, errors=["max turns"]))
    call, steps = recorded[0]
    assert call["status"] == "error" and "agent error" in call["error"] and call["cost_usd"] == 0.25
    assert len(steps) == 3 and call["run_id"] is None  # no trace context → still recorded


def test_rate_limited_call_recorded_with_partial_steps(monkeypatch, recorded):
    limit = RateLimitEvent(RateLimitInfo("rejected", resets_at=99, rate_limit_type="five_hour", utilization=1.0),
                           "u", "sess-2")
    with pytest.raises(RateLimited):
        run(monkeypatch, TOOL_MSGS[0], limit)
    call, steps = recorded[0]
    assert call["status"] == "rate_limited" and call["input_tokens"] == 0 and len(steps) == 2


def test_stream_crash_recorded(monkeypatch, recorded):
    with pytest.raises(ConnectionError):
        run(monkeypatch, TOOL_MSGS[0], ConnectionError("cli died"))
    assert recorded[0][0]["status"] == "error" and "cli died" in recorded[0][0]["error"]


def test_no_sink_records_nothing(monkeypatch):
    monkeypatch.setattr(trace, "_sink", None)
    assert run(monkeypatch, result()).text == "hotovo"


def test_pausing_sets_context_from_langgraph_config():
    seen = []
    node = _pausing(lambda s: seen.append(trace.current()) or {})
    config = {"configurable": {"thread_id": "go-1"}, "metadata": {"langgraph_node": "discover"}}
    node({"project": "akano3"}, config)
    node({"task": {"id": "ak-01"}}, {"configurable": {"thread_id": "go-1"}, "metadata": {"langgraph_node": "plan"}})
    node({}, None)
    assert seen == [trace.Trace("go-1", "discover", "akano3"), trace.Trace("go-1", "plan", "ak-01"),
                    trace.Trace(None, None, None)]
    assert trace.current() == trace.Trace(None, None, None)


def test_graph_passes_config_into_trace_context(tmp_path):
    import json
    from tests.test_go_nodes import go_graph, write
    from harness.graph import initial_state
    seen = []

    def explore(name, known):
        seen.append(trace.current())
        return json.dumps([]), {}
    app, bp, c = go_graph(tmp_path, explore, lambda *a: ("[]", {}), [])
    write(bp, "")
    app.invoke(initial_state("go-7", c, True, discover=True), {"configurable": {"thread_id": "go-7"}})
    assert sorted(seen, key=lambda t: t.worker) == [trace.Trace("go-7", "discover", "demo"),
                                                    trace.Trace("go-7", "discover", "web")]


def test_sink_failure_never_changes_the_agent_outcome(monkeypatch, capsys):
    def broken(call, steps):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(trace, "_sink", broken)
    assert run(monkeypatch, result()).text == "hotovo"
    limit = RateLimitEvent(RateLimitInfo("rejected", resets_at=99), "u", "s")
    with pytest.raises(RateLimited):
        run(monkeypatch, limit)
    assert "database is locked" in capsys.readouterr().err


def test_interrupt_is_not_recorded_as_ok(monkeypatch, recorded):
    with pytest.raises(KeyboardInterrupt):
        run(monkeypatch, TOOL_MSGS[0], KeyboardInterrupt())
    assert recorded[0][0]["status"] == "error"


@pytest.mark.parametrize("source, billing", [("none", "subscription"), ("ANTHROPIC_API_KEY", "api")])
def test_billing_from_init_api_key_source(monkeypatch, recorded, source, billing):
    r = run(monkeypatch, SystemMessage("init", {"apiKeySource": source}), result())
    assert r.usage["billing"] == billing and recorded[0][0]["billing"] == billing
