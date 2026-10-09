from harness import backlog, events, traces


def call(worker, node, cost, run_id="r1", status="ok", **kw):
    return {"run_id": run_id, "node": node, "worker": worker, "model": "opus", "started": 1.0, "duration_ms": 10,
            "num_turns": 2, "input_tokens": 10, "output_tokens": 100, "cache_read_tokens": 1000,
            "cache_write_tokens": 50, "cost_usd": cost, "status": status, "error": None, "session_id": "s"} | kw


def db(tmp_path):
    return events.connect(tmp_path / "h.db")


def test_record_and_read_back_calls_and_steps(tmp_path):
    conn = db(tmp_path)
    cid = traces.record(conn, call("ak-01", "plan", 0.2), [
        {"kind": "tool_use", "tool": "Read", "summary": "{}", "is_error": False},
        {"kind": "tool_result", "tool": "Read", "summary": "x", "is_error": True}])
    traces.record(conn, call("ak-01", "plan", 0.1, run_id="other"), [])
    rows = traces.calls(conn, "r1")
    assert [(r["id"], r["node"], r["cache_read_tokens"], r["steps"]) for r in rows] == [(cid, "plan", 1000, 2)]
    assert [(s["seq"], s["kind"], s["is_error"]) for s in traces.steps(conn, cid)] == [
        (0, "tool_use", False), (1, "tool_result", True)]


def test_costs_per_task_and_overhead(tmp_path):
    conn = db(tmp_path)
    traces.record(conn, call("akano3", "discover", 0.5), [])
    traces.record(conn, call(None, "prioritize", 0.1), [])
    traces.record(conn, call("ak-01", "plan", 0.25), [])
    traces.record(conn, call("ak-01", "implement", 0.5), [])
    traces.record(conn, call("ak-01", "implement", 0.25, status="error"), [])
    traces.sync_outcomes(conn, "## akano3\n- [x] P1 ak-01 Testy → https://gh/pr/1\n", lambda url: "merged")
    c = traces.costs(conn)
    assert c["tasks"] == [{"task_id": "ak-01", "project": "akano3", "state": "merged", "pr_url": "https://gh/pr/1",
                           "attempts": 2, "calls": 3, "cost_usd": 1.0, "tokens": 3 * 1160}]
    assert c["overhead"] == [{"run_id": "r1", "node": "discover", "calls": 1, "cost_usd": 0.5, "tokens": 1160},
                             {"run_id": "r1", "node": "prioritize", "calls": 1, "cost_usd": 0.1, "tokens": 1160}]


BACKLOG = """## demo
- [x] P1 d1 Hotový → https://gh/pr/7
- [x] P2 d2 Hotový bez odkazu
- [!] P1 d3 Spadl — failed: verify
- [?] P2 d4 Nejasný — UNCLEAR: co?
- [ ] P2 d5 Otevřený
- [~] P3 d6 Návrh
"""


def test_sync_outcomes_states(tmp_path):
    conn = db(tmp_path)
    asked = []
    traces.sync_outcomes(conn, BACKLOG, lambda url: asked.append(url) or "closed")
    assert asked == ["https://gh/pr/7"]
    assert traces.outcomes(conn) == {"d1": ("demo", "closed", "https://gh/pr/7"), "d3": ("demo", "failed", None),
                                     "d4": ("demo", "unclear", None)}


def test_sync_keeps_state_when_gh_fails(tmp_path):
    conn = db(tmp_path)
    traces.sync_outcomes(conn, BACKLOG, lambda url: "open")
    traces.sync_outcomes(conn, BACKLOG, lambda url: None)
    assert traces.outcomes(conn)["d1"] == ("demo", "open", "https://gh/pr/7")


def test_pr_urls_match_parsed_backlog():
    assert traces.pr_urls(BACKLOG) == {"d1": "https://gh/pr/7"}
    assert {t.id for t in backlog.parse(BACKLOG)} >= set(traces.pr_urls(BACKLOG))


def test_costs_lists_outcomes_without_calls(tmp_path):
    conn = db(tmp_path)
    traces.sync_outcomes(conn, "## demo\n- [x] P1 d1 Hotový → https://gh/pr/7\n", lambda url: "open")
    assert traces.costs(conn)["tasks"] == [{"task_id": "d1", "project": "demo", "state": "open",
                                           "pr_url": "https://gh/pr/7", "attempts": 0, "calls": 0,
                                           "cost_usd": 0, "tokens": 0}]


def test_backfill_from_event_usage_once(tmp_path):
    conn = db(tmp_path)
    u = lambda node, cost: [{"node": node, "model": "opus", "input_tokens": 5, "output_tokens": 50, "cost_usd": cost}]
    events.log(conn, "old", "end", node="plan", worker="d1", usage=u("plan", 0.3))
    events.log(conn, "old", "end", node="implement", worker="d1", usage=u("implement", 0.2))
    events.log(conn, "old", "end", node="discover", worker="demo", usage=u("discover", 0.5))
    events.log(conn, "sim", "end", node="plan", worker="s1", usage=u("plan", 0.0), simulated=True)
    traces.record(conn, call("d2", "plan", 1.0, run_id="new"), [])
    events.log(conn, "new", "end", node="plan", worker="d2", usage=u("plan", 1.0))
    traces.backfill(conn)
    traces.backfill(conn)
    c = traces.costs(conn)
    assert [(t["task_id"], t["calls"], t["cost_usd"]) for t in c["tasks"]] == [("d2", 1, 1.0), ("d1", 2, 0.5)]
    assert [(o["run_id"], o["cost_usd"]) for o in c["overhead"]] == [("old", 0.5)]


def test_sync_skips_final_states(tmp_path):
    conn = db(tmp_path)
    traces.sync_outcomes(conn, BACKLOG, lambda url: "merged")
    asked = []
    traces.sync_outcomes(conn, BACKLOG, lambda url: asked.append(url) or "open")
    assert asked == [] and traces.outcomes(conn)["d1"][1] == "merged"


def test_pr_state_survives_missing_or_hanging_gh(monkeypatch):
    import subprocess

    def missing(*a, **kw):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(subprocess, "run", missing)
    assert traces.pr_state("https://gh/pr/1") is None

    def hang(*a, **kw):
        assert kw.get("timeout")
        raise subprocess.TimeoutExpired("gh", kw["timeout"])
    monkeypatch.setattr(subprocess, "run", hang)
    assert traces.pr_state("https://gh/pr/1") is None


def test_backfill_keeps_pre_ledger_calls_of_resumed_run(tmp_path):
    conn = db(tmp_path)
    u = [{"node": "plan", "model": "opus", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.3}]
    events.log(conn, "r", "end", node="plan", worker="d1", usage=u)  # before phase 4
    traces.record(conn, call("d1", "implement", 0.5, run_id="r", started=9e9), [])  # resumed after
    traces.backfill(conn)
    traces.backfill(conn)
    assert [(t["calls"], t["cost_usd"]) for t in traces.costs(conn)["tasks"]] == [(2, 0.8)]


def test_attempts_ignore_rate_limited_calls(tmp_path):
    conn = db(tmp_path)
    traces.record(conn, call("d1", "implement", 0.1, status="rate_limited"), [])
    traces.record(conn, call("d1", "implement", 0.4), [])
    assert traces.costs(conn)["tasks"][0]["attempts"] == 1


def test_indexes_exist(tmp_path):
    conn = db(tmp_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"agent_steps_call", "agent_calls_run"} <= names


def test_ci_state():
    run = lambda status, conclusion="": {"__typename": "CheckRun", "status": status, "conclusion": conclusion}
    assert traces.ci_state([]) is None
    assert traces.ci_state([run("COMPLETED", "SUCCESS"), run("COMPLETED", "SKIPPED")]) == "ok"
    assert traces.ci_state([run("COMPLETED", "SUCCESS"), run("IN_PROGRESS")]) == "pending"
    assert traces.ci_state([run("IN_PROGRESS"), run("COMPLETED", "FAILURE")]) == "fail"
    assert traces.ci_state([{"__typename": "StatusContext", "state": "PENDING"}]) == "pending"
    assert traces.ci_state([{"__typename": "StatusContext", "state": "ERROR"}]) == "fail"
