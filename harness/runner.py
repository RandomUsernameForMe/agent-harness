from . import events
from .graph import RateLimited


def run_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 200}


def _log_debug(conn, run_id: str, ev: dict, simulated: bool) -> None:
    payload = ev["payload"]
    if ev["type"] == "task":
        events.log(conn, run_id, "start", node=payload["name"], simulated=simulated)
    elif ev["type"] == "task_result":
        if payload.get("error"):
            events.log(conn, run_id, "error", node=payload["name"], delta={"error": str(payload["error"])}, simulated=simulated)
            return
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        events.log(conn, run_id, "end", node=payload["name"],
                   delta={k: v for k, v in result.items() if k not in ("usage", "rate_limit")},
                   usage=result.get("usage") or None, rate_limit=result.get("rate_limit"), simulated=simulated)


def execute(app, inputs, config: dict, conn, simulated: bool) -> str:
    run_id = config["configurable"]["thread_id"]
    if inputs is not None:
        events.log(conn, run_id, "run_start", simulated=simulated)
    try:
        for _, ev in app.stream(inputs, config, stream_mode=["debug"]):
            _log_debug(conn, run_id, ev, simulated)
    except RateLimited as e:
        events.log(conn, run_id, "paused", rate_limit={"resets_at": e.resets_at}, simulated=simulated)
        return "paused"
    if app.get_state(config).next:
        return "interrupted"
    events.log(conn, run_id, "run_end", simulated=simulated)
    return "done"
