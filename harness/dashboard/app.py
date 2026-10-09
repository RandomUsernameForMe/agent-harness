import asyncio
import json
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import backlog, events, quota, serve, traces
from ..graph import graph_mermaid, worker_mermaid
from ..projects import load_config
from ..runner import execute, pending, questions
from ..simulate import Simulation, prepare

STATIC = Path(__file__).parent / "static"
PR_CACHE_SECONDS = 300
LOCAL_ORIGINS = ("http://127.0.0.1:", "http://localhost:")


class AnswerRequest(BaseModel):
    id: str
    answer: str


class MergeRequest(BaseModel):
    project: str
    number: int


class SimulateRequest(BaseModel):
    scenario: str
    step: bool = False
    delay: float | None = None


def progress(evs: list[dict]) -> dict:
    """Fold a run's events into node states (active/done/error): run-level stages, discover per project,
    and per worker its nodes in order of first visit, title, outcome and attempts."""
    stages: dict[str, str] = {}
    discover: dict[str, str] = {}
    workers: dict[str, dict] = {}
    titles: dict[str, str] = {}
    for e in evs:
        node, kind, delta = e["node"], e["kind"], e["delta"] or {}
        if kind not in ("start", "end", "error") or not node:
            continue
        state = {"start": "active", "end": "done", "error": "error"}[kind]
        if node == "pick_tasks" and kind == "end":
            titles |= {b["task"]["id"]: b["task"].get("title", "") for b in delta.get("batch") or []}
        if node == "discover":
            discover[e["worker"] or "?"] = state
        elif e["worker"] is None:
            stages[node] = state
        else:
            w = workers.setdefault(e["worker"], {"id": e["worker"], "nodes": {}, "outcome": None, "attempts": 0})
            w["nodes"][node] = state
            w["last"] = node
            w["outcome"] = delta.get("outcome", w["outcome"])
            w["attempts"] = delta.get("attempts", w["attempts"])
            w["pr_url"] = delta.get("pr_url", w.get("pr_url"))
    if discover:
        stages["discover"] = "active" if "active" in discover.values() else "done"
    if workers:
        stages["worker"] = "active" if any(w["nodes"].get("report") != "done" for w in workers.values()) else "done"
    return {"stages": stages, "discover": discover,
            "workers": [w | {"title": titles.get(w["id"], "")} for w in workers.values()]}


def local_origin(request: Request) -> bool:
    """Control endpoints spend quota: refuse cross-site requests (a page on the internet posting to localhost)."""
    origin = request.headers.get("origin")
    return origin is None or origin.startswith(LOCAL_ORIGINS)


def create_app(home: Path, control: serve.Ctx | None = None, prs=traces.open_prs, merge=traces.merge_pr,
               live_quota=quota.fetch) -> FastAPI:
    from ..cli import open_db

    app = FastAPI(title="harness")
    boot = uuid.uuid4().hex  # changes on every serve restart → open dashboards reload their page
    conn, saver = open_db(home)
    sims: dict[str, Simulation] = {}
    mermaid = {"mermaid": graph_mermaid(), "worker": worker_mermaid()}

    def scenarios() -> dict[str, Path]:
        return {p.stem: p for p in sorted((home / "scenarios").glob("*.yaml"))}

    pr_cache: dict[Path, tuple[float, list | None]] = {}

    def cached_prs(path: Path) -> list | None:
        hit = pr_cache.get(path)
        if hit is None or time.time() - hit[0] > PR_CACHE_SECONDS:
            hit = pr_cache[path] = (time.time(), prs(path) if path.exists() else None)
        return hit[1]

    def run_sim(sim: Simulation, inputs) -> None:
        with sim.lock:
            execute(sim.app, inputs, sim.config, conn, simulated=True)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/desktop")
    def desktop():
        return FileResponse(STATIC / "desktop.html")

    @app.get("/overview")
    def overview():
        cfg = load_config(home / "projects.yaml")  # every request: new projects show up without a restart
        path = home / "BACKLOG.md"
        tasks = backlog.parse(path.read_bytes().decode("utf-8")) if path.exists() else []
        with ThreadPoolExecutor() as pool:
            pulls = dict(zip(cfg.projects, pool.map(cached_prs, [p.path for p in cfg.projects.values()])))
        projects = [{"name": name, "prs": pulls[name],
                     "counts": {s: sum(t.project == name and t.status == s for t in tasks) for s in " ~x?-"}}
                    for name in cfg.projects]
        proposals = [asdict(t) for t in tasks if t.status == "~"]
        return {"projects": projects, "proposals": proposals, "serve": control and serve.snapshot(control),
                "boot": boot}

    @app.post("/control/{action}")
    def control_action(action: str, request: Request, body: dict | None = None):
        if not local_origin(request):
            raise HTTPException(403, "cross-origin request")
        if control is None:
            raise HTTPException(503, "start `harness serve` to control runs")
        try:
            return {"message": serve.act(control, action, body or {})}
        except KeyError:
            raise HTTPException(404, f"unknown action {action!r}") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/prs/merge")
    def merge_pr(req: MergeRequest, request: Request):
        if not local_origin(request):
            raise HTTPException(403, "cross-origin request")
        project = load_config(home / "projects.yaml").projects.get(req.project)
        if project is None:
            raise HTTPException(404, f"unknown project {req.project!r}")
        try:
            message = merge(project.path, req.number)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            raise HTTPException(409, str(e)) from None
        finally:
            pr_cache.pop(project.path, None)
        return {"message": message}

    @app.get("/graph")
    def graph():
        return mermaid

    @app.get("/runs")
    def runs():
        return events.runs(conn)

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, after: int = 0):
        return events.events_after(conn, after, run_id)

    @app.get("/runs/{run_id}/progress")
    def run_progress(run_id: str):
        return progress(events.events_after(conn, 0, run_id))

    @app.get("/runs/{run_id}/state")
    def run_state(run_id: str):
        sim = sims.get(run_id)
        if sim is None:
            raise HTTPException(404, "only simulations started from this dashboard have live state")
        running = sim.lock.locked()
        return {"next": pending(sim.app, sim.config), "running": running, "simulated": True,
                "questions": [] if running else questions(sim.app, sim.config)}

    @app.get("/events/stream")
    async def stream(after: int = 0):
        async def gen():
            last = after
            while True:
                for e in events.events_after(conn, last):
                    last = e["id"]
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/backlog")
    def get_backlog(sim: str | None = None):
        path = sims[sim].backlog_path if sim in sims else home / "BACKLOG.md"
        if not path.exists():
            return []
        return [asdict(t) for t in backlog.parse(path.read_bytes().decode("utf-8"))]

    @app.get("/runs/{run_id}/calls")
    def run_calls(run_id: str):
        return traces.calls(conn, run_id)

    @app.get("/calls/{call_id}/steps")
    def call_steps(call_id: int):
        return traces.steps(conn, call_id)

    @app.get("/costs")
    def costs():
        return traces.costs(conn)

    @app.get("/usage")
    def usage():
        live = live_quota() or {}
        return {"summary": events.usage_summary(conn),
                "rate_limit": live.get("five_hour") or events.latest_rate_limit(conn), "seven_day": live.get("seven_day")}

    @app.get("/scenarios")
    def list_scenarios():
        return [{"name": name, "title": (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("name", name)}
                for name, p in scenarios().items()]

    @app.post("/simulate")
    def simulate(req: SimulateRequest):
        path = scenarios().get(req.scenario)
        if path is None:
            raise HTTPException(404, f"unknown scenario {req.scenario!r}")
        sim = prepare(path, Path(tempfile.mkdtemp(prefix="harness-sim-")), saver, step=req.step, delay=req.delay)
        sims[sim.run_id] = sim
        threading.Thread(target=run_sim, args=(sim, sim.inputs), daemon=True).start()
        return {"run_id": sim.run_id}

    @app.post("/runs/{run_id}/step")
    def step(run_id: str):
        sim = sims.get(run_id)
        if sim is None:
            raise HTTPException(404, "unknown simulation")
        if sim.lock.locked():
            raise HTTPException(409, "step already running")
        threading.Thread(target=run_sim, args=(sim, None), daemon=True).start()
        return {"ok": True}

    @app.post("/runs/{run_id}/answer")
    def answer(run_id: str, req: AnswerRequest):
        sim = sims.get(run_id)
        if sim is None:
            raise HTTPException(404, "unknown simulation")
        # take the lock here, not in the thread: two quick answers must not both resume
        if not sim.lock.acquire(blocking=False):
            raise HTTPException(409, "run is busy")
        if req.id not in {q["id"] for q in questions(sim.app, sim.config)}:
            sim.lock.release()
            raise HTTPException(404, "unknown question")

        def resume() -> None:
            try:
                execute(sim.app, None, sim.config, conn, simulated=True, answers={req.id: req.answer})
            finally:
                sim.lock.release()
        threading.Thread(target=resume, args=(), daemon=True).start()
        return {"ok": True}

    return app
