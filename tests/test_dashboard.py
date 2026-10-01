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
    assert "verify" in client.get("/graph").json()["mermaid"]


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
    while client.get(f"/runs/{run_id}/state").json()["next"] != ["pick_task"]:
        assert time.time() < deadline
        time.sleep(0.05)
    assert client.post(f"/runs/{run_id}/step").json() == {"ok": True}
    deadline = time.time() + 5
    while client.get(f"/runs/{run_id}/state").json()["next"] != ["preflight"]:
        assert time.time() < deadline
        time.sleep(0.05)


def test_state_unknown_run_404(client):
    assert client.get("/runs/nope/state").status_code == 404
