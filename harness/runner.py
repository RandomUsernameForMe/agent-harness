from langgraph.types import Command

from . import events


def run_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 200}


def pending(app, config: dict) -> list[str]:
    """Nodes the run waits before; for a paused worker its inner node, not the parent `worker`."""
    out: list[str] = []
    for t in app.get_state(config, subgraphs=True).tasks:
        if t.result is not None:  # a worker that already finished this step
            continue
        out += list(t.state.next) if t.state is not None and t.state.next else [t.name]
    return out


class _Logger:
    def __init__(self, conn, run_id: str, simulated: bool):
        self.conn, self.run_id, self.simulated = conn, run_id, simulated
        self.workers: dict[str, str] = {}  # parent task id → backlog task id

    def log(self, kind: str, **kw) -> None:
        events.log(self.conn, self.run_id, kind, simulated=self.simulated, **kw)

    def debug(self, ns: tuple, ev: dict) -> None:
        if ev["type"] not in ("task", "task_result"):
            return
        payload = ev["payload"]
        if not ns and payload["name"] == "worker":
            # parent-level wrapper: remember which task it carries; its inner nodes are logged instead
            if ev["type"] == "task":
                self.workers[payload["id"]] = (payload.get("input") or {}).get("task", {}).get("id")
            return
        worker = None
        if ns:
            parent_id = ns[0].partition(":")[2]
            worker = self.workers.get(parent_id, parent_id)
        name = payload["name"]
        if ev["type"] == "task":
            self.log("start", node=name, worker=worker)
        elif ev["type"] == "task_result":
            if payload.get("error"):
                self.log("error", node=name, worker=worker, delta={"error": str(payload["error"])})
                return
            result = payload.get("result") or {}
            if not isinstance(result, dict):
                result = {}
            self.log("end", node=name, worker=worker,
                     delta={k: v for k, v in result.items() if k not in ("usage", "rate_limit")},
                     usage=result.get("usage") or None, rate_limit=result.get("rate_limit"))


def open_interrupts(app, config: dict) -> list:
    """Interrupts still waiting. `get_state().interrupts` also keeps the answered interrupt of a worker that
    finished while a sibling still waits, so only unfinished tasks count."""
    return [i for t in app.get_state(config).tasks if t.result is None for i in t.interrupts]


def _rate_limit_pauses(app, config: dict) -> list:
    return [i for i in open_interrupts(app, config) if isinstance(i.value, dict) and "rate_limited" in i.value]


def resets_at(app, config: dict) -> int | None:
    """When the latest rate limit of a paused run resets (None = unknown or not paused)."""
    return max((i.value["rate_limited"] or 0 for i in _rate_limit_pauses(app, config)), default=0) or None


def questions(app, config: dict) -> list[dict]:
    return [{"id": i.id, **i.value} for i in open_interrupts(app, config)
            if isinstance(i.value, dict) and "question" in i.value]


def execute(app, inputs, config: dict, conn, simulated: bool, answers: dict[str, str] | None = None) -> str:
    """Run or resume. `answers` maps question interrupt ids to the owner's answer ("" = skip); an unanswered
    question asks again. A resume re-runs every interrupted node, so rate-limited ones retry on any resume —
    callers holding answers during a pause should wait for the reset (the quota is shared anyway)."""
    run_id = config["configurable"]["thread_id"]
    logger = _Logger(conn, run_id, simulated)
    if inputs is not None:
        logger.log("run_start")
    else:
        if resume := {i.id: True for i in _rate_limit_pauses(app, config)} | (answers or {}):
            inputs = Command(resume=resume)
    for ns, _, ev in app.stream(inputs, config, stream_mode=["debug"], subgraphs=True):
        logger.debug(ns, ev)
    if _rate_limit_pauses(app, config):
        logger.log("paused", rate_limit={"resets_at": resets_at(app, config)})
        return "paused"
    if asked := questions(app, config):
        logger.log("waiting", delta={"questions": asked})
        return "waiting"
    if app.get_state(config).next:
        return "interrupted"
    logger.log("run_end")
    return "done"
