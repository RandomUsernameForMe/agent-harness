from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from harness import events
from harness.backlog import parse
from harness.runner import execute
from harness.simulate import prepare

SCENARIOS = Path(__file__).parent.parent / "scenarios"


def run(name, tmp_path):
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(SCENARIOS / f"{name}.yaml", tmp_path, InMemorySaver(), delay=0)
    status = execute(sim.app, sim.inputs, sim.config, conn, simulated=True)
    return sim, conn, status


def statuses(sim):
    return {t.id: t.status for t in parse(sim.backlog_path.read_bytes().decode())}


def started(conn, sim):
    return [e["node"] for e in events.events_after(conn, 0, sim.run_id) if e["kind"] == "start"]


def test_happy_path(tmp_path):
    sim, conn, status = run("happy-path", tmp_path)
    assert status == "done"
    assert statuses(sim) == {"sim-01": "x"}
    assert started(conn, sim) == ["pick_task", "preflight", "make_worktree", "plan", "setup",
                                  "implement", "verify", "open_pr", "report", "pick_task"]
    assert "https://example.invalid/sim/sim-01" in sim.backlog_path.read_text(encoding="utf-8")


def test_flaky_tests(tmp_path):
    sim, conn, status = run("flaky-tests", tmp_path)
    assert statuses(sim) == {"sim-01": "x"}
    assert sim.script.calls["implement"] == 3 and sim.script.calls["verify"] == 3


def test_retries_exhausted(tmp_path):
    sim, conn, _ = run("retries-exhausted", tmp_path)
    assert statuses(sim) == {"sim-01": "!"}
    assert sim.script.calls["open_pr"] == 0
    assert "tsc: 3 type errors" in sim.backlog_path.read_text(encoding="utf-8")


def test_unclear_task(tmp_path):
    sim, conn, _ = run("unclear-task", tmp_path)
    assert statuses(sim) == {"sim-01": "?"}
    assert sim.script.calls["setup"] == 0
    assert "UNCLEAR: Co přesně měřit?" in sim.backlog_path.read_text(encoding="utf-8")


def test_forbidden_path(tmp_path):
    sim, conn, _ = run("forbidden-path", tmp_path)
    assert statuses(sim) == {"sim-01": "!"}
    assert "forbidden paths" in sim.backlog_path.read_text(encoding="utf-8")


def test_rate_limit_pause_and_resume(tmp_path):
    sim, conn, status = run("rate-limit-pause-resume", tmp_path)
    assert status == "paused"
    assert statuses(sim) == {"sim-01": " "}
    assert [e["kind"] for e in events.events_after(conn, 0, sim.run_id)][-1] == "paused"
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "done"
    assert statuses(sim) == {"sim-01": "x"}
    assert sim.script.calls["make_worktree"] == 1
    assert sim.script.calls["implement"] == 2


def test_two_tasks_priority(tmp_path):
    sim, conn, _ = run("two-tasks-priority", tmp_path)
    assert statuses(sim) == {"sim-01": "x", "sim-02": "x"}
    reports = [e for e in events.events_after(conn, 0, sim.run_id) if e["node"] == "pick_task" and e["kind"] == "end"]
    assert reports[0]["delta"]["task"]["id"] == "sim-02"


def test_three_tasks_flaky_respects_max_tasks(tmp_path):
    sim, conn, status = run("three-tasks-flaky", tmp_path)
    assert status == "done"
    assert statuses(sim) == {"sim-01": "x", "sim-02": "x", "sim-03": " "}


def test_step_mode(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(SCENARIOS / "happy-path.yaml", tmp_path, InMemorySaver(), step=True, delay=0)
    assert execute(sim.app, sim.inputs, sim.config, conn, simulated=True) == "interrupted"
    assert sim.app.get_state(sim.config).next == ("pick_task",)
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "interrupted"
    assert sim.app.get_state(sim.config).next == ("preflight",)


def test_unknown_node_value_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: x\ntasks: [{project: demo, id: a-1, title: t, priority: P1}]\nnodes: {verify: [banana]}\n", encoding="utf-8")
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(p, tmp_path, InMemorySaver(), delay=0)
    execute(sim.app, sim.inputs, sim.config, conn, simulated=True)
    assert "unknown scenario value" in sim.backlog_path.read_text(encoding="utf-8")
