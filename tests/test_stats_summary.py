from harness import events, traces
from harness.cli import main

BACKLOG = """## akano3
- [x] P1 t1 Sonnet merged → https://gh/pr/1
- [!] P1 t2 Sonnet failed
- [x] P1 t3 Opus merged → https://gh/pr/3
- [x] P1 t4 Bez plánu → https://gh/pr/4
"""
STATES = {"https://gh/pr/1": "merged", "https://gh/pr/3": "merged", "https://gh/pr/4": "open"}


def call(worker, node, cost, **kw):
    return {"run_id": "r1", "node": node, "worker": worker, "model": "opus", "started": 1.0, "duration_ms": 10,
            "num_turns": 2, "input_tokens": 10, "output_tokens": 100, "cache_read_tokens": 1000,
            "cache_write_tokens": 50, "cost_usd": cost, "status": "ok", "error": None, "session_id": "s"} | kw


def seed(conn):
    traces.record(conn, call("t1", "plan", 0.25, model="sonnet"), [])
    traces.record(conn, call("t1", "plan", 0.25, model="opus"), [])  # only the first plan call counts
    traces.record(conn, call("t1", "implement", 0.5), [])
    traces.record(conn, call("t2", "plan", 0.25, model="sonnet"), [])
    traces.record(conn, call("t3", "plan", 0.5, model="opus"), [])
    traces.record(conn, call("t3", "implement", 1.0), [])
    traces.record(conn, call("t4", "implement", 0.125), [])
    traces.sync_outcomes(conn, BACKLOG, STATES.get)


def test_summary_groups_by_project_and_plan_model(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    seed(conn)
    base = {"project": "akano3", "closed": 0, "unclear": 0}
    assert traces.summary(conn) == [
        base | {"plan_model": "-", "tasks": 1, "merged": 0, "failed": 0, "open": 1, "cost_usd": 0.125,
                "cost_per_merged": None},
        base | {"plan_model": "opus", "tasks": 1, "merged": 1, "failed": 0, "open": 0, "cost_usd": 1.5,
                "cost_per_merged": 1.5},
        base | {"plan_model": "sonnet", "tasks": 2, "merged": 1, "failed": 1, "open": 0, "cost_usd": 1.25,
                "cost_per_merged": 1.25},
    ]


def test_stats_prints_summary(tmp_path, capsys, monkeypatch):
    (tmp_path / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text(BACKLOG, encoding="utf-8")
    seed(events.connect(tmp_path / "harness.db"))
    monkeypatch.setattr(traces, "pr_state", STATES.get)
    assert main(["--home", str(tmp_path), "stats"]) == 0
    lines = capsys.readouterr().out.splitlines()
    i = lines.index("souhrn (projekt × model plánovače)")
    row = next(l for l in lines[i:] if "sonnet" in l)
    assert row.split()[:2] == ["akano3", "sonnet"] and row.endswith("~$/merged 1.25")
    assert lines[-1].startswith("celkem")
