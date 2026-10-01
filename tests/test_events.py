from harness import events


def test_log_and_read(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    events.log(conn, "r1", "run_start", simulated=True)
    events.log(conn, "r1", "end", node="plan", delta={"plan": "x"},
               usage=[{"node": "plan", "model": "opus", "input_tokens": 10, "output_tokens": 2, "cost_usd": 0.5}],
               rate_limit={"type": "five_hour", "utilization": 0.4, "status": "allowed", "resets_at": 1})
    events.log(conn, "r2", "run_start")
    rows = events.events_after(conn, 0, "r1")
    assert [r["kind"] for r in rows] == ["run_start", "end"]
    assert rows[1]["delta"] == {"plan": "x"} and rows[1]["simulated"] is False
    assert rows[0]["simulated"] is True
    assert [r["id"] for r in events.events_after(conn, rows[0]["id"])] == [rows[1]["id"], rows[1]["id"] + 1]


def test_runs_and_usage(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    events.log(conn, "r1", "run_start", simulated=True)
    events.log(conn, "r1", "end", node="implement", usage=[{"node": "implement", "model": "sonnet", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.1}], simulated=True)
    events.log(conn, "r1", "end", node="implement", usage=[{"node": "implement", "model": "sonnet", "input_tokens": 50, "output_tokens": 5, "cost_usd": None}], simulated=True)
    events.log(conn, "r1", "run_end", simulated=True)
    [run] = events.runs(conn)
    assert run["run_id"] == "r1" and run["last_kind"] == "run_end" and run["simulated"] is True
    [u] = events.usage_summary(conn)
    assert (u["node"], u["model"], u["input_tokens"], u["output_tokens"]) == ("implement", "sonnet", 150, 25)
    assert abs(u["cost_usd"] - 0.1) < 1e-9


def test_latest_rate_limit(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    assert events.latest_rate_limit(conn) is None
    events.log(conn, "r", "end", node="plan", rate_limit={"utilization": 0.2})
    events.log(conn, "r", "end", node="implement", rate_limit={"utilization": 0.5})
    assert events.latest_rate_limit(conn) == {"utilization": 0.5}


def test_worker_column_and_migration_of_phase1_db(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute(events.SCHEMA.replace("  worker TEXT,\n", ""))
    old.execute("INSERT INTO events (run_id, ts, kind) VALUES ('r0', 1, 'run_start')")
    old.commit()
    old.close()
    conn = events.connect(db)
    events.log(conn, "r1", "start", node="plan", worker="gp-01")
    rows = events.events_after(conn)
    assert [(r["run_id"], r["worker"]) for r in rows] == [("r0", None), ("r1", "gp-01")]
    events.connect(db)  # second migration is a no-op
