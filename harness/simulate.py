import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from langgraph.store.memory import InMemoryStore

from .graph import build_graph, initial_state
from .nodes.common import make_approve, make_discover, make_pick_tasks, make_prioritize, make_reflect, make_report
from .nodes.fake import Script, fake_explore, fake_nodes, fake_pr_info, fake_rank, fake_reflect
from .projects import Config, Project
from .runner import run_config


@dataclass
class Simulation:
    app: object
    config: dict
    run_id: str
    inputs: dict
    script: Script
    backlog_path: Path
    cfg: Config | None = None
    store: object = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def load_scenario(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _render_backlog(tasks: list[dict]) -> str:
    out: dict[str, list[str]] = {}
    for t in tasks:
        note = f" — {t['note']}" if t.get("note") else ""
        out.setdefault(t["project"], []).append(
            f"- [{t.get('status', ' ')}] P{str(t['priority'])[-1]} {t['id']} {t['title']}{note}")
    return "".join(f"## {p}\n" + "\n".join(lines) + "\n\n" for p, lines in out.items())


def prepare(scenario_path: Path, workdir: Path, checkpointer, step: bool = False, delay: float | None = None,
            store=None) -> Simulation:
    """`store` defaults to an in-memory one: simulated lessons must never reach the real harness.db."""
    sc = load_scenario(scenario_path)
    workdir.mkdir(parents=True, exist_ok=True)
    backlog_path = workdir / "BACKLOG.md"
    backlog_path.write_bytes(_render_backlog(sc["tasks"]).encode("utf-8"))
    discover = sc.get("discover") or {}
    names = [*dict.fromkeys([t["project"] for t in sc["tasks"]] + list(discover))]
    projects = {n: Project(n, workdir, "main", (), ("true",), (), sc.get("max_attempts", 3), 800) for n in names}
    cfg = Config("subscription", sc.get("max_tasks", 3), 0.7, projects, sc.get("max_parallel", 2))
    per_task = {t["id"]: t["nodes"] for t in sc["tasks"] if t.get("nodes")}
    per_task |= {name: {"discover": v} for name, v in discover.items()}
    script = Script(sc.get("nodes") or {}, sc.get("delay", 0) if delay is None else delay, per_task)
    nodes = fake_nodes(script) | {"pick_tasks": make_pick_tasks(backlog_path, cfg), "report": make_report(backlog_path),
                                  "discover": make_discover(backlog_path, fake_explore(script)),
                                  "prioritize": make_prioritize(backlog_path, cfg, fake_rank(script)),
                                  "approve": make_approve(backlog_path),
                                  "reflect": make_reflect(backlog_path, fake_reflect(script), fake_pr_info(sc.get("prs") or {}))}
    run_id = f"sim-{Path(scenario_path).stem}-{int(time.time() * 1000)}"
    store = InMemoryStore() if store is None else store
    app = build_graph(nodes, checkpointer, step, store=store)
    return Simulation(app, run_config(run_id), run_id, initial_state(run_id, cfg, True, discover=bool(discover)),
                      script, backlog_path, cfg, store)
