import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from harness import backlog
from harness.discover import proposal_id
from harness.graph import NODES, RateLimited, build_graph, initial_state
from harness.nodes.common import make_approve, make_discover, make_pick_tasks, make_prioritize, make_report
from harness.projects import Config, Project


def cfg(*names):
    return Config("subscription", 3, 0.7, {n: Project(n, ".", "main", (), ("true",), (), 1, 800) for n in names})


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


def read(path):
    return {t.id: t.status for t in backlog.parse(path.read_bytes().decode("utf-8"))}


def test_discover_passes_known_tasks_and_parses(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "## demo\n- [-] P2 old Odmítnuté\n## web\n- [ ] P1 w1 Jiné\n")
    seen = []

    def explore(name, known, lens):
        seen.append((name, lens, [t.title for t in known]))
        return '[{"title": "Odmítnuté"}, {"title": "Nové", "kind": "bug"}]', {"usage": [{"node": lens}], "quota": 0.4}
    out = make_discover(bp, explore)({"project": "demo"})
    # the feature lens sees the fix lens' proposals as known, so it cannot repeat them
    assert seen == [("demo", "fix", ["Odmítnuté"]), ("demo", "feature", ["Odmítnuté", "Nové"])]
    assert [(p["title"], p["kind"]) for p in out["proposals"]] == [("Nové", "bug")]
    assert out["usage"] == [{"node": "fix"}, {"node": "feature"}] and out["quota"] == 0.4 and out["go_errors"] == []


def test_discover_merges_both_lenses(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")
    answers = {"fix": '[{"title": "Oprava", "kind": "bug"}]', "feature": '[{"title": "Export do CSV"}]'}
    out = make_discover(bp, lambda n, k, lens: (answers[lens], {}))({"project": "demo"})
    assert [(p["title"], p["kind"]) for p in out["proposals"]] == [("Oprava", "bug"), ("Export do CSV", "feature")]


def test_discover_runs_only_chosen_lenses(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")
    calls = []
    explore = lambda n, k, lens: (calls.append(lens) or '[{"title": "Export do CSV"}]', {})
    out = make_discover(bp, explore)({"project": "demo", "lenses": ["feature"]})
    assert calls == ["feature"] and [p["kind"] for p in out["proposals"]] == ["feature"]


def test_discover_failure_costs_only_this_project(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")
    def half(n, k, lens):
        if lens == "fix":
            raise RuntimeError("agent died")
        return '[{"title": "Export"}]', {}
    out = make_discover(bp, half)({"project": "demo"})
    assert out["go_errors"] == ["demo/fix: RuntimeError: agent died"]
    assert [p["title"] for p in out["proposals"]] == ["Export"]
    prose = make_discover(bp, lambda n, k, lens: ("Nic jsem nenašel.", {}))
    assert prose({"project": "demo"})["go_errors"] == ["demo/fix: odpověď bez JSON pole",
                                                       "demo/feature: odpověď bez JSON pole"]
    empty = make_discover(bp, lambda n, k, lens: ("[]", {}))
    assert empty({"project": "demo"})["go_errors"] == []


def test_discover_lets_rate_limit_through(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")

    def limited(name, known, lens):
        raise RateLimited(123)
    with pytest.raises(RateLimited):
        make_discover(bp, limited)({"project": "demo"})


def proposal(title, project="demo"):
    return {"id": proposal_id(project, title), "project": project, "title": title, "kind": "test",
            "description": "", "why": "", "done": "", "source": "new"}


def test_prioritize_uses_model_order_or_falls_back(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "## demo\n- [ ] P1 t1 Ruční\n")
    state = {"proposals": [proposal("A"), proposal("B")], "max_tasks": 2}
    a, b = state["proposals"][0]["id"], state["proposals"][1]["id"]
    ok = make_prioritize(bp, cfg("demo"), lambda c, n, f, w: (json.dumps([{"id": b}, {"id": "t1"}]), {"usage": [{"u": 1}], "quota": 0.5}))
    out = ok(state)
    assert [p["id"] for p in out["plan"]] == [b, "t1"] and out["usage"] == [{"u": 1}] and out["quota"] == 0.5
    bad = make_prioritize(bp, cfg("demo"), lambda c, n, f, w: (_ for _ in ()).throw(RuntimeError("x")))
    out = bad(state)
    assert [p["id"] for p in out["plan"]] == ["t1", a]
    assert out["go_errors"] == ["prioritize: RuntimeError: x"]


def test_prioritize_without_candidates_skips_model(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")
    called = []
    node = make_prioritize(bp, cfg("demo"), lambda *a: called.append(a) or ("[]", {}))
    assert node({"proposals": []}) == {"plan": []} and not called


def go_graph(tmp_path, explore, rank_fn, workers):
    bp = tmp_path / "BACKLOG.md"
    c = cfg("demo", "web")
    nodes = {n: (lambda s: {}) for n in NODES} | {
        "discover": make_discover(bp, explore), "prioritize": make_prioritize(bp, c, rank_fn),
        "approve": make_approve(bp), "pick_tasks": make_pick_tasks(bp, c), "report": make_report(bp),
        "verify": lambda s: {"verify_ok": True},  # noop verify would loop implement ↔ verify forever
        "open_pr": lambda s: workers.append(s["task"]["id"]) or {"outcome": "pr_opened", "pr_url": "u"}}
    return build_graph(nodes, InMemorySaver()), bp, c


def test_go_end_to_end_with_approval(tmp_path):
    workers = []
    explore = lambda name, known, lens: (json.dumps([{"title": f"Úkol {name}"}] if lens == "fix" else []), {})
    rank_all = lambda cands, n, f, w: (json.dumps([{"id": c["id"]} for c in cands]), {})
    app, bp, c = go_graph(tmp_path, explore, rank_all, workers)
    write(bp, "## demo\n- [ ] P3 t1 Ruční\n")
    config = {"configurable": {"thread_id": "go"}, "recursion_limit": 100}
    app.invoke(initial_state("go", c, True, discover=True), config)
    asked = app.get_state(config).tasks[0].interrupts[0]
    assert [p["title"] for p in asked.value["plan"]] == ["Úkol demo", "Úkol web", "Ruční"]
    app.invoke(Command(resume={asked.id: [0, 2]}), config)
    demo, web = proposal_id("demo", "Úkol demo"), proposal_id("web", "Úkol web")
    assert read(bp) == {"t1": "x", demo: "x", web: "-"}
    assert sorted(workers) == sorted(["t1", demo])


def test_go_with_empty_plan_ends_without_question(tmp_path):
    workers = []
    app, bp, c = go_graph(tmp_path, lambda n, k: ("[]", {}), lambda *a: ("[]", {}), workers)
    write(bp, "")
    config = {"configurable": {"thread_id": "go"}, "recursion_limit": 100}
    app.invoke(initial_state("go", c, True, discover=True), config)
    assert not app.get_state(config).next and not workers


def test_pick_tasks_follows_planned_order(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "## demo\n- [ ] P1 a A\n- [ ] P3 b B\n## web\n- [ ] P1 c C\n- [ ] P1 d D\n")
    pick = make_pick_tasks(bp, cfg("demo", "web"))
    batch = pick({"planned": ["b", "a", "d"], "max_tasks": 3, "max_parallel": 2, "tasks_done": 0})["batch"]
    assert [i["task"]["id"] for i in batch] == ["b", "d"]
    assert [i["task"]["id"] for i in pick({"max_tasks": 3, "max_parallel": 2})["batch"]] == ["a", "c"]


def test_go_quota_spent_by_discover_stops_workers(tmp_path):
    workers = []
    explore = lambda name, known, lens: (json.dumps([{"title": f"Úkol {name}"}] if lens == "fix" else []), {"usage": [], "quota": 0.95})
    rank_all = lambda cands, n, f, w: (json.dumps([{"id": c["id"]} for c in cands]), {})
    app, bp, c = go_graph(tmp_path, explore, rank_all, workers)
    write(bp, "")
    config = {"configurable": {"thread_id": "go"}, "recursion_limit": 100}
    app.invoke(initial_state("go", c, True, discover=True), config)
    asked = app.get_state(config).tasks[0].interrupts[0]
    app.invoke(Command(resume={asked.id: [0, 1]}), config)
    assert not workers
    assert set(read(bp).values()) == {" "}  # approved, left for a run after the quota resets


def test_discover_default_skips_cleanup_lens(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")
    calls = []
    make_discover(bp, lambda n, k, lens: (calls.append(lens) or "[]", {}))({"project": "demo"})
    assert calls == ["fix", "feature"]
