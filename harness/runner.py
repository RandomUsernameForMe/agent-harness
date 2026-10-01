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


def _rate_limit_pauses(app, config: dict) -> list:
    return [i for i in app.get_state(config).interrupts if isinstance(i.value, dict) and "rate_limited" in i.value]


def execute(app, inputs, config: dict, conn, simulated: bool) -> str:
    run_id = config["configurable"]["thread_id"]
    logger = _Logger(conn, run_id, simulated)
    if inputs is not None:
        logger.log("run_start")
    elif pauses := _rate_limit_pauses(app, config):
        inputs = Command(resume={i.id: True for i in pauses})
    for ns, _, ev in app.stream(inputs, config, stream_mode=["debug"], subgraphs=True):
        logger.debug(ns, ev)
    if pauses := _rate_limit_pauses(app, config):
        logger.log("paused", rate_limit={"resets_at": max((i.value["rate_limited"] or 0) for i in pauses) or None})
        return "paused"
    if app.get_state(config).next:
        return "interrupted"
    logger.log("run_end")
    return "done"
