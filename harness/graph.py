import operator
import re
from subprocess import CalledProcessError
from typing import Annotated, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send, interrupt

from . import trace
from .projects import Config

WORKER_NODES = ("preflight", "make_worktree", "plan", "setup", "implement", "verify", "open_pr", "report")
NODES = ("discover", "prioritize", "approve", "pick_tasks", *WORKER_NODES)
LINEAR = {"preflight": "make_worktree", "make_worktree": "plan",
          "setup": "implement", "implement": "verify", "open_pr": "report"}
MAX_CLARIFY = 2
RETRYING = {"make_worktree", "open_pr"}


def _max(a: float | None, b: float | None) -> float | None:
    # ponytail: highest utilization seen in the run, never goes back down after a quota reset — fine for one sitting
    return b if a is None else a if b is None else max(a, b)


class WorkerState(TypedDict, total=False):
    run_id: str
    simulated: bool
    task: dict
    max_attempts: int
    branch: str
    worktree: str
    plan: str
    accept: str  # acceptance command the planner derived from the task's "Hotovo ="; verify runs it too
    stuck: bool
    supervisor_stop: str  # why the supervisor stopped the last implement call; "" = it did not
    replanned: bool
    clarifications: list[str]
    baseline_failures: list[str]
    attempts: int
    verify_ok: bool
    last_failure: str
    outcome: str | None
    pr_url: str
    quota: float | None
    rate_limit: dict | None
    usage: Annotated[list, operator.add]


class State(TypedDict, total=False):
    run_id: str
    simulated: bool
    max_tasks: int
    max_parallel: int
    quota_threshold: float
    batch: list[dict]
    # written by parallel workers in the same step → every key needs a reducer
    tasks_done: Annotated[int, operator.add]
    quota: Annotated[float | None, _max]
    usage: Annotated[list, operator.add]
    discover: bool
    projects: list[str]
    only: list[str] | None  # project filter from the dashboard; None = every project
    lenses: list[str] | None  # discover lenses from the dashboard (fix, feature, cleanup); None = fix + feature
    auto: bool  # autorun discover: no plan, no approval interrupt, every proposal lands as [~]
    proposals: Annotated[list, operator.add]  # parallel discover writes
    plan: list[dict]
    planned: list[str] | None  # approved ids in order; None = `harness run`, rank the whole backlog
    go_errors: Annotated[list, operator.add]


class RateLimited(Exception):
    def __init__(self, resets_at: int | None):
        super().__init__(f"rate limited until {resets_at}")
        self.resets_at = resets_at


def fan_out(state: State) -> list[Send] | str:
    base = {"run_id": state["run_id"], "simulated": state.get("simulated", False), "usage": []}
    return [Send("worker", base | item) for item in state.get("batch") or []] or "reflect"


def start(state: State) -> list[Send] | str:
    if not state.get("discover"):
        return "pick_tasks"
    only = state.get("only")
    lenses = state.get("lenses")
    return [Send("discover", {"project": p, "lenses": lenses}) for p in state.get("projects") or [] if not only or p in only] \
        or "prioritize"


def after_approve(state: State) -> str:
    return "pick_tasks" if state.get("planned") else "reflect"


def after_plan(state: WorkerState) -> str:
    if state.get("outcome") == "needs_clarification" and len(state.get("clarifications") or []) < MAX_CLARIFY:
        return "clarify"
    return "report" if state.get("outcome") else "setup"


def after_clarify(state: WorkerState) -> str:
    return "report" if state.get("outcome") else "plan"


def clarify(state: WorkerState) -> dict:
    t = state["task"]
    answer = interrupt({"question": state.get("last_failure", ""), "task": t["id"], "title": t.get("title", ""),
                        "project": t.get("project", "")})
    if not (answer or "").strip():
        return {}  # skipped: outcome stays needs_clarification → report writes [?]
    return {"outcome": None, "last_failure": "", "clarifications": [*(state.get("clarifications") or []), answer.strip()]}


def after_verify(state: WorkerState) -> str:
    if state.get("outcome"):
        return "report"
    if state.get("verify_ok") and not state.get("supervisor_stop"):  # weakened tests pass verify too
        return "open_pr"
    if not state.get("stuck") and not state.get("supervisor_stop") \
            and state.get("attempts", 0) < state.get("max_attempts", 3):
        return "implement"
    return "report" if state.get("replanned") else "replan"


def replan(state: WorkerState) -> dict:
    # last_failure stays: plan and implement see why the previous plan failed
    out = {"replanned": True, "attempts": 0, "stuck": False, "supervisor_stop": ""}
    if stop := state.get("supervisor_stop"):
        out["last_failure"] = f"The supervisor stopped the implementer ({stop})\n\n{state.get('last_failure', '')}"
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"\d+", "#", text).split())


def same_failure(a: str, b: str) -> bool:
    # ponytail: digits masked (counts, timings, line numbers); "3 errors" → "2 errors" counts as stuck too
    return bool(a) and _norm(a) == _norm(b)


def _detect_stuck(fn: Callable) -> Callable:
    def verify(state: WorkerState) -> dict:
        out = fn(state)
        return out | {"stuck": not out.get("verify_ok") and same_failure(state.get("last_failure") or "",
                                                                         out.get("last_failure") or "")}
    return verify


def _linear(nxt: str) -> Callable[[WorkerState], str]:
    return lambda state: "report" if state.get("outcome") else nxt


def _pausing(fn: Callable) -> Callable:
    def wrapped(state: dict, config: RunnableConfig | None = None) -> dict:
        # LangGraph injects `config` (thread id, running node in metadata) → agent calls know whose they are
        try:
            with trace.context(trace.from_config(state, config)):
                return fn(state)
        except RateLimited as e:
            limited = e
        # interrupt, not an exception: an exception cancels the step and parallel siblings would finish
        # unobserved; an interrupt lets them complete. Each resume re-runs the node once; interrupts answered
        # by earlier resumes return immediately, the first unanswered one pauses the run again.
        while True:
            interrupt({"rate_limited": limited.resets_at})
    return wrapped


def _safe(fn: Callable) -> Callable:
    def guarded(state: WorkerState) -> dict:
        try:
            return fn(state)
        except RateLimited:
            raise
        except Exception as e:
            return {"outcome": "failed", "last_failure": f"{type(e).__name__}: {e}"}
    return _pausing(guarded)


def build_worker(nodes: dict[str, Callable], step: bool = False):
    g = StateGraph(WorkerState)
    for name in WORKER_NODES:
        if name in RETRYING:
            g.add_node(name, nodes[name], retry_policy=RetryPolicy(max_attempts=3, retry_on=CalledProcessError))
        else:
            g.add_node(name, _safe(_detect_stuck(nodes[name]) if name == "verify" else nodes[name]))
    g.add_node("clarify", clarify)  # not _safe: the interrupt must propagate
    g.add_node("replan", replan)
    g.add_edge(START, "preflight")
    for src, nxt in LINEAR.items():
        g.add_conditional_edges(src, _linear(nxt), sorted({nxt, "report"}))
    g.add_conditional_edges("plan", after_plan, ["clarify", "setup", "report"])
    g.add_conditional_edges("clarify", after_clarify, ["plan", "report"])
    g.add_conditional_edges("verify", after_verify, ["open_pr", "implement", "replan", "report"])
    g.add_edge("replan", "plan")
    g.add_edge("report", END)
    # no checkpointer: inherits the parent's, each Send gets its own namespace worker:<task id>
    return g.compile(interrupt_before=[*WORKER_NODES, "clarify"] if step else None)


def _worker_node(sub) -> Callable:
    def worker(state: WorkerState) -> dict:
        out = sub.invoke(state)
        return {"tasks_done": 1, "usage": out.get("usage") or [], "quota": out.get("quota")}
    return worker


def build_graph(nodes: dict[str, Callable], checkpointer=None, step: bool = False, store=None):
    missing = set(NODES) - set(nodes)
    if missing:
        raise ValueError(f"missing nodes: {sorted(missing)}")
    g = StateGraph(State)
    g.add_node("discover", _pausing(nodes["discover"]))
    g.add_node("prioritize", _pausing(nodes["prioritize"]))
    g.add_node("approve", nodes["approve"])  # not _pausing: its interrupt must propagate
    g.add_node("pick_tasks", nodes["pick_tasks"])
    g.add_node("worker", _worker_node(build_worker(nodes, step)))
    g.add_node("reflect", _pausing(nodes.get("reflect", lambda s: {})))  # optional: older callers have none
    g.add_conditional_edges(START, start, ["discover", "prioritize", "pick_tasks"])
    g.add_edge("discover", "prioritize")
    g.add_edge("prioritize", "approve")
    g.add_conditional_edges("approve", after_approve, ["pick_tasks", "reflect"])
    g.add_conditional_edges("pick_tasks", fan_out, ["worker", "reflect"])
    g.add_edge("worker", "pick_tasks")
    g.add_edge("reflect", END)
    return g.compile(checkpointer=checkpointer, store=store,
                     interrupt_before=["discover", "prioritize", "pick_tasks"] if step else None)


def initial_state(run_id: str, cfg: Config, simulated: bool, discover: bool = False) -> dict:
    return {"run_id": run_id, "simulated": simulated, "max_tasks": cfg.max_tasks_per_run,
            "max_parallel": cfg.max_parallel, "quota_threshold": cfg.quota_threshold, "tasks_done": 0, "usage": [],
            "discover": discover, "projects": [n for n, p in cfg.projects.items() if p.discover]}


def _noop_nodes() -> dict[str, Callable]:
    return {n: (lambda s: {}) for n in NODES}


def graph_mermaid() -> str:
    return build_graph(_noop_nodes()).get_graph().draw_mermaid()


def worker_mermaid() -> str:
    return build_worker(_noop_nodes()).get_graph().draw_mermaid()
