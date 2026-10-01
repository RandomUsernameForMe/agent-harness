import asyncio
import json
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import backlog, events
from ..graph import graph_mermaid
from ..runner import execute
from ..simulate import Simulation, prepare

STATIC = Path(__file__).parent / "static"


class SimulateRequest(BaseModel):
    scenario: str
    step: bool = False
    delay: float | None = None


def create_app(home: Path) -> FastAPI:
    from ..cli import open_db

    app = FastAPI(title="harness")
    conn, saver = open_db(home)
    sims: dict[str, Simulation] = {}
    mermaid = graph_mermaid()

    def scenarios() -> dict[str, Path]:
        return {p.stem: p for p in sorted((home / "scenarios").glob("*.yaml"))}

    def run_sim(sim: Simulation, inputs) -> None:
        with sim.lock:
            execute(sim.app, inputs, sim.config, conn, simulated=True)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/graph")
    def graph():
        return {"mermaid": mermaid}

    @app.get("/runs")
    def runs():
        return events.runs(conn)

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, after: int = 0):
        return events.events_after(conn, after, run_id)

    @app.get("/runs/{run_id}/state")
    def run_state(run_id: str):
        sim = sims.get(run_id)
        if sim is None:
            raise HTTPException(404, "only simulations started from this dashboard have live state")
        return {"next": list(sim.app.get_state(sim.config).next), "simulated": True}

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

    @app.get("/usage")
    def usage():
        return {"summary": events.usage_summary(conn), "rate_limit": events.latest_rate_limit(conn)}

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

    return app
