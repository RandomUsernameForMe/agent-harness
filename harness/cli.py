import argparse
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from . import backlog, events
from .graph import build_graph, initial_state
from .nodes.common import make_pick_tasks, make_report
from .nodes.real import git, real_nodes, worktree_path
from .projects import load_config
from .priority import config_focus, rank
from .runner import execute, pending, questions, run_config
from .scan import new_proposals, proposal_lines, scan_project
from .sdk import check_billing
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


def ask_in_terminal(app, config: dict) -> dict[str, str]:
    return {q["id"]: input(f"{q['task']}: {q['question']} (Enter = přeskočit) > ").strip()
            for q in questions(app, config)}


def cmd_simulate(args) -> int:
    path = _scenario_path(args.home, args.scenario)
    if path is None:
        print(f"scenario not found: {args.scenario}", file=sys.stderr)
        return 2
    conn, saver = open_db(args.home)
    sim = prepare(path, Path(tempfile.mkdtemp(prefix="harness-sim-")), saver, step=args.step, delay=args.delay)
    print(f"run {sim.run_id}")
    status = execute(sim.app, sim.inputs, sim.config, conn, simulated=True)
    while status in ("interrupted", "waiting"):
        if status == "waiting":
            status = execute(sim.app, None, sim.config, conn, simulated=True, answers=ask_in_terminal(sim.app, sim.config))
            continue
        nxt = ", ".join(pending(sim.app, sim.config))
        if input(f"další uzel: {nxt} — Enter = krok, q = konec > ").strip().lower() == "q":
            break
        status = execute(sim.app, None, sim.config, conn, simulated=True)
    print(status)
    print(sim.backlog_path.read_text(encoding="utf-8"))
    return 0


def cmd_run(args) -> int:
    cfg = load_config(args.home / "projects.yaml")
    # preflight checks this per task, but --resume jumps straight back into implement
    if err := check_billing(cfg.billing, os.environ):
        print(err, file=sys.stderr)
        return 2
    backlog_path = args.home / "BACKLOG.md"
    conn, saver = open_db(args.home)
    nodes = real_nodes(cfg) | {"pick_tasks": make_pick_tasks(backlog_path, cfg), "report": make_report(backlog_path)}
    app = build_graph(nodes, saver)
    run_id = args.resume or f"run-{time.strftime('%Y%m%d-%H%M%S')}"
    inputs = None if args.resume else initial_state(run_id, cfg, simulated=False)
    config = run_config(run_id)
    status = execute(app, inputs, config, conn, simulated=False)
    while status == "waiting":
        status = execute(app, None, config, conn, simulated=False, answers=ask_in_terminal(app, config))
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


def _read_backlog(home: Path) -> str:
    path = home / "BACKLOG.md"
    return path.read_bytes().decode("utf-8") if path.exists() else ""


def cmd_queue(args) -> int:
    cfg = load_config(args.home / "projects.yaml")
    focus = config_focus(cfg)
    ranked = rank(backlog.parse(_read_backlog(args.home)), set(cfg.projects), focus,
                  {n: p.weight for n, p in cfg.projects.items()})
    print("focus (secondbrain): " + (", ".join(f"{n}={c}" for n, c in focus.items()) if focus else "off"))
    for i, t in enumerate(ranked, 1):
        print(f"{i:>2}. P{t.priority} {t.id} [{t.project}] {t.title}")
    if not ranked:
        print("no open tasks")
    return 0


def cmd_scan(args) -> int:
    cfg = load_config(args.home / "projects.yaml")
    text = _read_backlog(args.home)
    names = [args.project] if args.project else list(cfg.projects)
    if unknown := [n for n in names if n not in cfg.projects]:
        print(f"unknown project: {', '.join(unknown)}", file=sys.stderr)
        return 2
    added = 0
    for name in names:
        try:
            props = new_proposals(scan_project(cfg.projects[name], args.limit), backlog.parse(text))
        except Exception as e:  # one unreachable repo must not hide the others
            print(f"{name}: scan failed: {e}", file=sys.stderr)
            continue
        for p in props:
            print(f"{name}: {p.id} {p.title} ({p.path}:{p.line})")
        if not props:
            print(f"{name}: no new proposals")
        if props and args.write:
            text = backlog.add_tasks(text, name, [line for p in props for line in proposal_lines(p)])
            added += len(props)
    if args.write and added:
        (args.home / "BACKLOG.md").write_bytes(text.encode("utf-8"))
        print(f"{added} proposal(s) written as [~]; change to [ ] to approve")
    return 0


def cmd_dashboard(args) -> int:
    import uvicorn

    from .dashboard.app import create_app
    uvicorn.run(create_app(args.home), host="127.0.0.1", port=args.port)
    return 0


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
    sub.add_parser("queue")
    sc = sub.add_parser("scan")
    sc.add_argument("--write", action="store_true")
    sc.add_argument("--project")
    sc.add_argument("--limit", type=int, default=5)
    args = p.parse_args(argv)
    handlers = {"simulate": cmd_simulate, "run": cmd_run, "dashboard": cmd_dashboard, "prune": cmd_prune,
                "queue": cmd_queue, "scan": cmd_scan}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
