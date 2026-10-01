import pytest
from langgraph.graph import END

from harness.graph import NODES, after_pick, after_verify, build_graph, graph_mermaid


def test_after_pick():
    assert after_pick({"task": {"id": "a"}}) == "preflight"
    assert after_pick({"task": None}) == END


def test_after_verify():
    assert after_verify({"verify_ok": True, "attempts": 1, "max_attempts": 3}) == "open_pr"
    assert after_verify({"verify_ok": False, "attempts": 1, "max_attempts": 3}) == "implement"
    assert after_verify({"verify_ok": False, "attempts": 3, "max_attempts": 3}) == "report"
    assert after_verify({"outcome": "failed", "verify_ok": True}) == "report"


def test_missing_node_rejected():
    with pytest.raises(ValueError, match="missing nodes"):
        build_graph({"pick_task": lambda s: {}})


def test_exception_in_safe_node_becomes_failed_outcome():
    calls = []

    def boom(state):
        raise RuntimeError("kaboom")

    def pick(state):
        if calls:
            return {"task": None}
        calls.append(1)
        return {"task": {"id": "t"}, "outcome": None}

    nodes = {n: (lambda s: {}) for n in NODES} | {"pick_task": pick, "plan": boom,
                                                    "report": lambda s: {"tasks_done": 1}}
    out = build_graph(nodes).invoke({"tasks_done": 0, "usage": []}, {"recursion_limit": 50})
    assert out["outcome"] == "failed"
    assert out["last_failure"] == "RuntimeError: kaboom"


def test_mermaid_lists_all_nodes():
    text = graph_mermaid()
    for n in NODES:
        assert n in text
