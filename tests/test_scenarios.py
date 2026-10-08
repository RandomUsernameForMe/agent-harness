from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from harness import events
from harness.backlog import parse
from harness.runner import execute, pending, questions
from harness.simulate import prepare

SCENARIOS = Path(__file__).parent.parent / "scenarios"


def run(name, tmp_path, delay=0):
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(SCENARIOS / f"{name}.yaml", tmp_path, InMemorySaver(), delay=delay)
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
    assert started(conn, sim) == ["pick_tasks", "preflight", "make_worktree", "plan", "setup",
                                  "implement", "verify", "open_pr", "report", "pick_tasks"]
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
    sim, conn, status = run("unclear-task", tmp_path)
    assert status == "waiting"
    [q] = questions(sim.app, sim.config)
    assert (q["task"], q["question"]) == ("sim-01", "Co přesně měřit?")
    assert [e["kind"] for e in events.events_after(conn, 0, sim.run_id)][-1] == "waiting"
    assert execute(sim.app, None, sim.config, conn, simulated=True, answers={q["id"]: ""}) == "done"
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
    picks = [e for e in events.events_after(conn, 0, sim.run_id) if e["node"] == "pick_tasks" and e["kind"] == "end"]
    assert [b["task"]["id"] for b in picks[0]["delta"]["batch"]] == ["sim-02"]  # same project → one at a time


def test_three_tasks_flaky_respects_max_tasks(tmp_path):
    sim, conn, status = run("three-tasks-flaky", tmp_path)
    assert status == "done"
    assert statuses(sim) == {"sim-01": "x", "sim-02": "x", "sim-03": " "}


def test_step_mode(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(SCENARIOS / "happy-path.yaml", tmp_path, InMemorySaver(), step=True, delay=0)
    assert execute(sim.app, sim.inputs, sim.config, conn, simulated=True) == "interrupted"
    assert pending(sim.app, sim.config) == ["pick_tasks"]
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "interrupted"
    assert pending(sim.app, sim.config) == ["preflight"]


def test_unknown_node_value_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: x\ntasks: [{project: demo, id: a-1, title: t, priority: P1}]\nnodes: {verify: [banana]}\n", encoding="utf-8")
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(p, tmp_path, InMemorySaver(), delay=0)
    execute(sim.app, sim.inputs, sim.config, conn, simulated=True)
    assert "unknown scenario value" in sim.backlog_path.read_text(encoding="utf-8")


def worker_events(conn, sim, worker):
    return [e for e in events.events_after(conn, 0, sim.run_id) if e["worker"] == worker]


def test_parallel_projects_run_concurrently(tmp_path):
    sim, conn, status = run("parallel-projects", tmp_path, delay=0.05)
    assert status == "done"
    assert statuses(sim) == {"sim-a1": "x", "sim-b1": "x", "sim-a2": "x"}
    a1, b1, a2 = (worker_events(conn, sim, w) for w in ("sim-a1", "sim-b1", "sim-a2"))
    # both workers of the first batch started before either finished
    assert max(a1[0]["id"], b1[0]["id"]) < min(a1[-1]["id"], b1[-1]["id"])
    # the second alpha task waits for the next batch
    assert a2[0]["id"] > max(a1[-1]["id"], b1[-1]["id"])
    assert [e["node"] for e in a1 if e["kind"] == "start"].count("implement") == 2
    assert all(e["worker"] for e in events.events_after(conn, 0, sim.run_id) if e["node"] not in (None, "pick_tasks"))


def test_parallel_usage_reaches_parent_state(tmp_path):
    sim, conn, _ = run("parallel-projects", tmp_path)
    usage = sim.app.get_state(sim.config).values["usage"]
    assert sorted(u["node"] for u in usage) == ["implement"] * 4 + ["plan"] * 3
    assert sim.app.get_state(sim.config).values["tasks_done"] == 3


def test_parallel_rate_limit_resumes_only_interrupted_worker(tmp_path):
    sim, conn, status = run("parallel-rate-limit", tmp_path, delay=0.02)

    def starts(worker):
        return [e["node"] for e in worker_events(conn, sim, worker) if e["kind"] == "start"]
    assert status == "paused"
    assert statuses(sim) == {"sim-a1": " ", "sim-b1": "x"}
    # the sibling finished inside the paused step, fully logged
    assert starts("sim-b1") == ["preflight", "make_worktree", "plan", "setup", "implement", "verify", "open_pr", "report"]
    assert pending(sim.app, sim.config) == ["implement"]
    paused = events.events_after(conn, 0, sim.run_id)[-1]
    assert paused["kind"] == "paused" and paused["rate_limit"]["resets_at"] > 0
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "done"
    assert statuses(sim) == {"sim-a1": "x", "sim-b1": "x"}
    assert len(starts("sim-b1")) == 8
    assert starts("sim-a1").count("make_worktree") == 1
    assert starts("sim-a1").count("implement") == 2


def test_parallel_step_mode_moves_both_workers(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(SCENARIOS / "parallel-rate-limit.yaml", tmp_path, InMemorySaver(), step=True, delay=0)
    sim.script.per_task.clear()
    assert execute(sim.app, sim.inputs, sim.config, conn, simulated=True) == "interrupted"
    assert pending(sim.app, sim.config) == ["pick_tasks"]
    execute(sim.app, None, sim.config, conn, simulated=True)
    assert pending(sim.app, sim.config) == ["preflight", "preflight"]
    execute(sim.app, None, sim.config, conn, simulated=True)
    assert pending(sim.app, sim.config) == ["make_worktree", "make_worktree"]
    status = "interrupted"
    for _ in range(40):
        if status != "interrupted":
            break
        status = execute(sim.app, None, sim.config, conn, simulated=True)
    assert status == "done"
    assert statuses(sim) == {"sim-a1": "x", "sim-b1": "x"}


def test_rate_limit_still_active_after_resume_pauses_again(tmp_path):
    p = tmp_path / "rl.yaml"
    p.write_text("name: x\ntasks: [{project: demo, id: a-1, title: t, priority: P1}]\n"
                 "nodes: {plan: [{rate_limit: 5}, {rate_limit: 5}, ok]}\n", encoding="utf-8")
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(p, tmp_path, InMemorySaver(), delay=0)
    assert execute(sim.app, sim.inputs, sim.config, conn, simulated=True) == "paused"
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "paused"
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "done"
    assert statuses(sim) == {"a-1": "x"}
    assert sim.script.calls["plan"] == 3
    assert sim.script.calls["preflight"] == 1


def answer_all(sim, conn, text, **kw):
    return execute(sim.app, None, sim.config, conn, simulated=True,
                   answers={q["id"]: text for q in questions(sim.app, sim.config)}, **kw)


def test_clarify_answered(tmp_path):
    sim, conn, status = run("clarify-answered", tmp_path)
    assert status == "waiting" and statuses(sim) == {"sim-01": " "}
    assert answer_all(sim, conn, "měř LCP na /gallery") == "done"
    assert statuses(sim) == {"sim-01": "x"}
    assert sim.script.calls["plan"] == 2
    plans = [e["delta"]["plan"] for e in events.events_after(conn, 0, sim.run_id)
             if e["node"] == "plan" and e["kind"] == "end" and "plan" in (e["delta"] or {})]
    assert plans == ["1. Simulovaný krok\nUpřesnění: měř LCP na /gallery"]


def test_clarify_max_rounds(tmp_path):
    sim, conn, status = run("clarify-max-rounds", tmp_path)
    assert answer_all(sim, conn, "první") == "waiting"
    assert answer_all(sim, conn, "druhá") == "done"
    assert statuses(sim) == {"sim-01": "?"}
    assert sim.script.calls["plan"] == 3 and sim.script.calls["setup"] == 0


def test_parallel_clarify_sibling_finishes(tmp_path):
    sim, conn, status = run("parallel-clarify", tmp_path)
    assert status == "waiting"
    assert statuses(sim) == {"sim-a1": " ", "sim-b1": "x"}
    assert [q["task"] for q in questions(sim.app, sim.config)] == ["sim-a1"]
    assert answer_all(sim, conn, "ano") == "done"
    assert statuses(sim) == {"sim-a1": "x", "sim-b1": "x"}
    assert sim.script.calls["open_pr"] == 2  # b1 not repeated


def test_pause_wins_over_question_and_one_resume_answers_both(tmp_path):
    sim, conn, status = run("clarify-rate-limit", tmp_path)
    assert status == "paused"  # the question is still listed, the caller holds the answer until the reset
    assert [q["task"] for q in questions(sim.app, sim.config)] == ["sim-a1"]
    assert answer_all(sim, conn, "ano") == "done"
    assert statuses(sim) == {"sim-a1": "x", "sim-b1": "x"}
    assert sim.script.calls["implement"] == 3  # b1 limited + retried, a1 once


def test_go_happy_runs_approved_plan(tmp_path):
    from harness.discover import proposal_id
    from harness.runner import approvals
    sim, conn, status = run("go-happy", tmp_path)
    assert status == "approving"
    asked = approvals(sim.app, sim.config)[0]
    assert [p["title"] for p in asked["plan"]] == ["Testy pro parser", "Opravit odkaz v patičce",
                                                   "Ruční úkol z BACKLOGu"]
    status = execute(sim.app, None, sim.config, conn, simulated=True, answers={asked["id"]: [0, 1, 2]})
    assert status == "done"
    assert statuses(sim) == {"sim-01": "x", proposal_id("demo", "Testy pro parser"): "x",
                             proposal_id("web", "Opravit odkaz v patičce"): "x",
                             proposal_id("web", "Přepsat celý frontend"): "~"}
    assert started(conn, sim)[:4] == ["discover", "discover", "prioritize", "approve"]


def test_go_struck_task_is_rejected_and_not_run(tmp_path):
    from harness.discover import proposal_id
    from harness.runner import approvals
    sim, conn, _ = run("go-happy", tmp_path)
    asked = approvals(sim.app, sim.config)[0]
    execute(sim.app, None, sim.config, conn, simulated=True, answers={asked["id"]: [0, 2]})
    assert statuses(sim)[proposal_id("web", "Opravit odkaz v patičce")] == "-"
    assert sim.script.calls["open_pr"] == 2


def test_go_reject_all_parks_proposals(tmp_path):
    from harness.discover import proposal_id
    from harness.runner import approvals
    sim, conn, status = run("go-reject-all", tmp_path)
    asked = approvals(sim.app, sim.config)[0]
    assert execute(sim.app, None, sim.config, conn, simulated=True, answers={asked["id"]: "q"}) == "done"
    assert statuses(sim) == {proposal_id("demo", "Testy pro parser"): "~"}
    assert "preflight" not in started(conn, sim)


def test_go_discover_rate_limit_resumes_only_limited_project(tmp_path):
    from harness.runner import approvals
    sim, conn, status = run("go-discover-rate-limit", tmp_path)
    assert status == "paused"
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "approving"
    assert sim.script.calls["discover"] == 3  # demo once, web twice
    assert len(approvals(sim.app, sim.config)[0]["plan"]) == 2


def test_go_discover_events_carry_project(tmp_path):
    sim, conn, _ = run("go-happy", tmp_path)
    evs = [(e["kind"], e["worker"]) for e in events.events_after(conn, 0, sim.run_id) if e["node"] == "discover"]
    assert sorted(evs) == [("end", "demo"), ("end", "web"), ("start", "demo"), ("start", "web")]
