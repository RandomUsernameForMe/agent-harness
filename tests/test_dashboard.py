import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.dashboard.app import create_app

ROOT = Path(__file__).parent.parent


@pytest.fixture
def client(tmp_path):
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    return TestClient(create_app(tmp_path))


def wait_for(client, run_id, kind, timeout=10):
    deadline = time.time() + timeout
    kinds = []
    while time.time() < deadline:
        kinds = [e["kind"] for e in client.get(f"/runs/{run_id}/events").json()]
        if kind in kinds:
            return kinds
        time.sleep(0.05)
    raise AssertionError(f"{kind} not reached: {kinds}")


def test_graph(client):
    g = client.get("/graph").json()
    assert "pick_tasks" in g["mermaid"] and "verify" in g["worker"]


def test_scenarios_listed(client):
    names = {s["name"] for s in client.get("/scenarios").json()}
    assert {"happy-path", "flaky-tests", "rate-limit-pause-resume"} <= names


def test_simulate_runs_to_end(client):
    run_id = client.post("/simulate", json={"scenario": "happy-path", "delay": 0}).json()["run_id"]
    wait_for(client, run_id, "run_end")
    assert any(r["run_id"] == run_id for r in client.get("/runs").json())
    assert client.get(f"/backlog?sim={run_id}").json()[0]["status"] == "x"
    assert client.get("/usage").json()["summary"]


def test_unknown_or_traversal_scenario_404(client):
    assert client.post("/simulate", json={"scenario": "../../etc/passwd"}).status_code == 404
    assert client.post("/simulate", json={"scenario": "nope"}).status_code == 404


def test_step_mode(client):
    run_id = client.post("/simulate", json={"scenario": "happy-path", "step": True, "delay": 0}).json()["run_id"]
    deadline = time.time() + 5
    while client.get(f"/runs/{run_id}/state").json()["next"] != ["pick_tasks"]:
        assert time.time() < deadline
        time.sleep(0.05)
    assert client.post(f"/runs/{run_id}/step").json() == {"ok": True}
    deadline = time.time() + 5
    while client.get(f"/runs/{run_id}/state").json()["next"] != ["preflight"]:
        assert time.time() < deadline
        time.sleep(0.05)


def test_state_reports_running_step(client):
    run_id = client.post("/simulate", json={"scenario": "happy-path", "step": True, "delay": 0.5}).json()["run_id"]
    deadline = time.time() + 5
    while client.get(f"/runs/{run_id}/state").json()["next"] != ["pick_tasks"]:
        assert time.time() < deadline
        time.sleep(0.05)
    client.post(f"/runs/{run_id}/step")  # pick_tasks is instant (not a fake node)
    while client.get(f"/runs/{run_id}/state").json()["next"] != ["preflight"]:
        assert time.time() < deadline
        time.sleep(0.05)
    client.post(f"/runs/{run_id}/step")  # preflight sleeps 0.5 s
    time.sleep(0.1)
    assert client.get(f"/runs/{run_id}/state").json()["running"] is True


def test_state_unknown_run_404(client):
    assert client.get("/runs/nope/state").status_code == 404


def wait_next(client, run_id, expected, timeout=5):
    deadline = time.time() + timeout
    while (st := client.get(f"/runs/{run_id}/state").json())["next"] != expected or st["running"]:
        assert time.time() < deadline, st
        time.sleep(0.05)


def test_step_mode_shows_parallel_workers(client):
    run_id = client.post("/simulate", json={"scenario": "parallel-projects", "step": True, "delay": 0}).json()["run_id"]
    wait_next(client, run_id, ["pick_tasks"])
    client.post(f"/runs/{run_id}/step")
    wait_next(client, run_id, ["preflight", "preflight"])
    client.post(f"/runs/{run_id}/step")
    wait_next(client, run_id, ["make_worktree", "make_worktree"])
    workers = {e["worker"] for e in client.get(f"/runs/{run_id}/events").json() if e["worker"]}
    assert workers == {"sim-a1", "sim-b1"}


def test_answer_unclear_question(client):
    run_id = client.post("/simulate", json={"scenario": "unclear-task", "delay": 0}).json()["run_id"]
    wait_for(client, run_id, "waiting")
    deadline = time.time() + 5
    while (st := client.get(f"/runs/{run_id}/state").json())["running"]:
        assert time.time() < deadline
        time.sleep(0.05)
    [q] = st["questions"]
    assert q["question"] == "Co přesně měřit?"
    assert client.post(f"/runs/{run_id}/answer", json={"id": "nope", "answer": "x"}).status_code == 404
    assert client.post(f"/runs/{run_id}/answer", json={"id": q["id"], "answer": ""}).json() == {"ok": True}
    wait_for(client, run_id, "run_end")
    assert client.get(f"/backlog?sim={run_id}").json()[0]["status"] == "?"


def test_second_answer_while_resuming_is_rejected(client, monkeypatch):
    import threading
    from harness.dashboard import app as app_module
    run_id = client.post("/simulate", json={"scenario": "unclear-task", "delay": 0}).json()["run_id"]
    wait_for(client, run_id, "waiting")
    deadline = time.time() + 5
    while (st := client.get(f"/runs/{run_id}/state").json())["running"]:
        assert time.time() < deadline
        time.sleep(0.05)
    started = []
    monkeypatch.setattr(app_module.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda self: started.append(1)})())
    q = st["questions"][0]
    assert client.post(f"/runs/{run_id}/answer", json={"id": q["id"], "answer": "a"}).status_code == 200
    assert client.post(f"/runs/{run_id}/answer", json={"id": q["id"], "answer": "b"}).status_code == 409
    assert started == [1]


def test_calls_steps_and_costs(tmp_path):
    from harness import events, traces
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    conn = events.connect(tmp_path / "harness.db")
    cid = traces.record(conn, {"run_id": "go-1", "worker": "ak-01", "node": "plan", "model": "opus",
                               "input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 3, "cache_write_tokens": 4,
                               "cost_usd": 0.5, "status": "ok"},
                        [{"kind": "tool_use", "tool": "Read", "summary": "{}", "is_error": False}])
    client = TestClient(create_app(tmp_path))
    calls = client.get("/runs/go-1/calls").json()
    assert [(c["id"], c["node"], c["steps"]) for c in calls] == [(cid, "plan", 1)]
    assert client.get(f"/calls/{cid}/steps").json()[0]["tool"] == "Read"
    costs = client.get("/costs").json()
    assert costs["tasks"][0]["task_id"] == "ak-01" and costs["tasks"][0]["cost_usd"] == 0.5


def test_overview_reads_projects_yaml_every_request(tmp_path):
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    (tmp_path / "a").mkdir()
    yaml_path = tmp_path / "projects.yaml"
    yaml_path.write_text("projects:\n  alpha: {path: a, base: main, verify: [x]}\n", encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text("# Backlog\n\n## alpha\n- [ ] P1 a-1 Úkol\n- [~] P2 a-2 Návrh\n  popis\n",
                                         encoding="utf-8")
    calls = []
    prs = lambda path: calls.append(path) or [{"number": 1, "title": "PR", "url": "u", "isDraft": True}]
    client = TestClient(create_app(tmp_path, prs=prs))
    body = client.get("/overview").json()
    [alpha] = body["projects"]
    assert alpha["name"] == "alpha" and alpha["prs"][0]["number"] == 1
    assert alpha["counts"][" "] == 1 and alpha["counts"]["~"] == 1
    assert [p["id"] for p in body["proposals"]] == ["a-2"] and body["serve"] is None
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8") + "  beta: {path: b, base: main, verify: [x]}\n",
                         encoding="utf-8")
    body = client.get("/overview").json()
    assert [p["name"] for p in body["projects"]] == ["alpha", "beta"]
    assert body["projects"][1]["prs"] is None  # missing checkout: no gh call
    assert len(calls) == 1  # alpha cached
    assert body["boot"] == client.get("/overview").json()["boot"]
    assert TestClient(create_app(tmp_path, prs=prs)).get("/overview").json()["boot"] != body["boot"]


def test_control_needs_serve_and_local_origin(client):
    assert client.post("/control/run", json={}).status_code == 503
    assert client.post("/control/run", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/desktop").status_code == 200
