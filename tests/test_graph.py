import pytest
from langgraph.graph import END
from langgraph.types import Send

from harness.graph import NODES, WORKER_NODES, _max, after_verify, build_graph, fan_out, graph_mermaid, worker_mermaid


def test_fan_out_sends_one_worker_per_batch_item():
    sends = fan_out({"run_id": "r", "simulated": True, "batch": [{"task": {"id": "a"}, "max_attempts": 2},
                                                                  {"task": {"id": "b"}, "max_attempts": 3}]})
    assert sends == [Send("worker", {"run_id": "r", "simulated": True, "usage": [], "task": {"id": "a"}, "max_attempts": 2}),
                     Send("worker", {"run_id": "r", "simulated": True, "usage": [], "task": {"id": "b"}, "max_attempts": 3})]
    assert fan_out({"run_id": "r", "batch": []}) == END


def test_after_verify():
    assert after_verify({"verify_ok": True, "attempts": 1, "max_attempts": 3}) == "open_pr"
    assert after_verify({"verify_ok": False, "attempts": 1, "max_attempts": 3}) == "implement"
    assert after_verify({"verify_ok": False, "attempts": 3, "max_attempts": 3}) == "report"
    assert after_verify({"outcome": "failed", "verify_ok": True}) == "report"


def test_quota_reducer_keeps_highest_known():
    assert _max(None, 0.4) == 0.4
    assert _max(0.4, None) == 0.4
    assert _max(0.4, 0.2) == 0.4


def test_missing_node_rejected():
    with pytest.raises(ValueError, match="missing nodes"):
        build_graph({"pick_tasks": lambda s: {}})


def test_exception_in_safe_node_becomes_failed_outcome():
    seen = []

    def pick(state):
        return {"batch": [] if state.get("tasks_done") else [{"task": {"id": "t"}, "max_attempts": 1}]}

    def boom(state):
        raise RuntimeError("kaboom")

    nodes = {n: (lambda s: {}) for n in NODES} | {"pick_tasks": pick, "plan": boom,
                                                    "report": lambda s: seen.append(dict(s)) or {}}
    out = build_graph(nodes).invoke({"run_id": "r", "tasks_done": 0, "usage": []}, {"recursion_limit": 50})
    assert out["tasks_done"] == 1
    assert seen[0]["outcome"] == "failed"
    assert seen[0]["last_failure"] == "RuntimeError: kaboom"


def test_mermaid_lists_all_nodes():
    parent, worker = graph_mermaid(), worker_mermaid()
    assert "pick_tasks" in parent and "worker" in parent
    for n in WORKER_NODES:
        assert n in worker


def test_start_routes_go_and_run():
    from harness.graph import start
    assert start({"discover": False}) == "pick_tasks"
    assert start({}) == "pick_tasks"
    assert start({"discover": True, "projects": ["a", "b"]}) == [Send("discover", {"project": "a"}),
                                                                Send("discover", {"project": "b"})]
    assert start({"discover": True, "projects": []}) == "prioritize"


def test_after_approve():
    from harness.graph import after_approve
    assert after_approve({"planned": ["x"]}) == "pick_tasks"
    assert after_approve({"planned": []}) == END
