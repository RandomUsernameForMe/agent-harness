import operator
from subprocess import CalledProcessError
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from .projects import Config

NODES = ("pick_task", "preflight", "make_worktree", "plan", "setup", "implement", "verify", "open_pr", "report")
LINEAR = {"preflight": "make_worktree", "make_worktree": "plan", "plan": "setup",
          "setup": "implement", "implement": "verify", "open_pr": "report"}
RETRYING = {"make_worktree", "open_pr"}


class State(TypedDict, total=False):
    run_id: str
    simulated: bool
    max_tasks: int
    quota_threshold: float
    tasks_done: int
    task: dict | None
    max_attempts: int
    branch: str
    worktree: str
    plan: str
    baseline_failures: list[str]
    attempts: int
    verify_ok: bool
    last_failure: str
    outcome: str | None
    pr_url: str
    quota: float | None
    rate_limit: dict | None
    usage: Annotated[list, operator.add]


class RateLimited(Exception):
    def __init__(self, resets_at: int | None):
        super().__init__(f"rate limited until {resets_at}")
        self.resets_at = resets_at


def after_pick(state: State) -> str:
    return "preflight" if state.get("task") else END


def after_verify(state: State) -> str:
    if state.get("outcome"):
        return "report"
    if state.get("verify_ok"):
        return "open_pr"
    return "implement" if state.get("attempts", 0) < state.get("max_attempts", 3) else "report"


def _linear(nxt: str) -> Callable[[State], str]:
    return lambda state: "report" if state.get("outcome") else nxt


def _safe(fn: Callable) -> Callable:
    def wrapped(state: State) -> dict:
        try:
            return fn(state)
        except RateLimited:
            raise
        except Exception as e:
            return {"outcome": "failed", "last_failure": f"{type(e).__name__}: {e}"}
    return wrapped


def build_graph(nodes: dict[str, Callable], checkpointer=None, step: bool = False):
    missing = set(NODES) - set(nodes)
    if missing:
        raise ValueError(f"missing nodes: {sorted(missing)}")
    g = StateGraph(State)
    for name in NODES:
        if name in RETRYING:
            g.add_node(name, nodes[name], retry_policy=RetryPolicy(max_attempts=3, retry_on=CalledProcessError))
        else:
            g.add_node(name, _safe(nodes[name]))
    g.add_edge(START, "pick_task")
    g.add_conditional_edges("pick_task", after_pick, ["preflight", END])
    for src, nxt in LINEAR.items():
        g.add_conditional_edges(src, _linear(nxt), sorted({nxt, "report"}))
    g.add_conditional_edges("verify", after_verify, ["open_pr", "implement", "report"])
    g.add_edge("report", "pick_task")
    return g.compile(checkpointer=checkpointer, interrupt_before=list(NODES) if step else None)


def initial_state(run_id: str, cfg: Config, simulated: bool) -> dict:
    return {"run_id": run_id, "simulated": simulated, "max_tasks": cfg.max_tasks_per_run,
            "quota_threshold": cfg.quota_threshold, "tasks_done": 0, "usage": []}


def graph_mermaid() -> str:
    return build_graph({n: (lambda s: {}) for n in NODES}).get_graph().draw_mermaid()
