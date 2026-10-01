import argparse
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from . import backlog, events
from .graph import build_graph, initial_state
from .nodes.common import make_pick_task, make_report
from .nodes.real import git, real_nodes, worktree_path
from .projects import load_config
from .runner import execute, run_config
from .simulate import prepare


def open_db(home: Path):
    db = home / "harness.db"
    conn = events.connect(db)
    saver = SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
    return conn, saver


def _scenario_path(home: Path, name: str) -> Path | None:
    p = Path(name)
    if p.suffix == ".yaml" and p.exists():
        return p
    p = home / "scenarios" / f"{name}.yaml"
    return p if p.exists() else None


def cmd_simulate(args) -> int:
    path = _scenario_path(args.home, args.scenario)
    if path is None:
        print(f"scenario not found: {args.scenario}", file=sys.stderr)
        return 2
    conn, saver = open_db(args.home)
    sim = prepare(path, Path(tempfile.mkdtemp(prefix="harness-sim-")), saver, step=args.step, delay=args.delay)
    print(f"run {sim.run_id}")
    status = execute(sim.app, sim.inputs, sim.config, conn, simulated=True)
    while status == "interrupted":
        nxt = ", ".join(sim.app.get_state(sim.config).next)
        if input(f"další uzel: {nxt} — Enter = krok, q = konec > ").strip().lower() == "q":
            break
        status = execute(sim.app, None, sim.config, conn, simulated=True)
    print(status)
    print(sim.backlog_path.read_text(encoding="utf-8"))
    return 0


def cmd_run(args) -> int:
    cfg = load_config(args.home / "projects.yaml")
    backlog_path = args.home / "BACKLOG.md"
    conn, saver = open_db(args.home)
    nodes = real_nodes(cfg) | {"pick_task": make_pick_task(backlog_path, cfg), "report": make_report(backlog_path)}
    app = build_graph(nodes, saver)
    run_id = args.resume or f"run-{time.strftime('%Y%m%d-%H%M%S')}"
    inputs = None if args.resume else initial_state(run_id, cfg, simulated=False)
    status = execute(app, inputs, run_config(run_id), conn, simulated=False)
    print(f"{run_id} {status}")
    if status == "paused":
        print(f"resume: harness run --resume {run_id}")
    return 0


def cmd_prune(args) -> int:
    cfg = load_config(args.home / "projects.yaml")
    done = {t.id for t in backlog.parse((args.home / "BACKLOG.md").read_bytes().decode("utf-8")) if t.status == "x"}
    for project in cfg.projects.values():
        for task_id in done:
            path = worktree_path(project, task_id)
            if path.exists():
                git(["worktree", "remove", "--force", str(path)], project.path)
                print(f"removed {path}")
    return 0


def _todo(args) -> int:
    raise SystemExit("not implemented yet")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252; BACKLOG uses → and —
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(prog="harness")
    p.add_argument("--home", type=Path, default=Path.cwd())
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate")
    s.add_argument("scenario")
    s.add_argument("--step", action="store_true")
    s.add_argument("--delay", type=float)
    r = sub.add_parser("run")
    r.add_argument("--resume", metavar="RUN_ID")
    d = sub.add_parser("dashboard")
    d.add_argument("--port", type=int, default=8765)
    sub.add_parser("prune")
    args = p.parse_args(argv)
    handlers = {"simulate": cmd_simulate, "run": cmd_run, "dashboard": _todo, "prune": cmd_prune}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
