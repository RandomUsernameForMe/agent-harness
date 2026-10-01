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
