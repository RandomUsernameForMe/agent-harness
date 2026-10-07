import argparse
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from . import backlog, events, serve
from .graph import build_graph, initial_state
from .nodes.common import edit_backlog, make_approve, make_discover, make_pick_tasks, make_prioritize, make_report
from .nodes.real import git, real_nodes, worktree_path
from .projects import load_config
from .priority import config_focus, rank
from .discover import format_plan, parse_approval
from .runner import approvals, execute, pending, questions, run_config
from .scan import proposal_lines, scan_all
from .sdk import check_billing
from .simulate import prepare
from .telegram import Telegram


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


def ask_approval(app, config: dict) -> dict:
    out = {}
    for a in approvals(app, config):
        print(format_plan(a["plan"]))
        while True:
            try:
                out[a["id"]] = parse_approval(input("Enter = vše, čísla = vyškrtnout (2,5), q = konec > "),
                                              len(a["plan"]))
                break
            except ValueError as e:
                print(e)
    return out


def answer_in_terminal(app, config: dict, status: str) -> dict:
    return ask_approval(app, config) if status == "approving" else ask_in_terminal(app, config)


def cmd_simulate(args) -> int:
    path = _scenario_path(args.home, args.scenario)
    if path is None:
        print(f"scenario not found: {args.scenario}", file=sys.stderr)
        return 2
    conn, saver = open_db(args.home)
    sim = prepare(path, Path(tempfile.mkdtemp(prefix="harness-sim-")), saver, step=args.step, delay=args.delay)
    print(f"run {sim.run_id}")
    status = execute(sim.app, sim.inputs, sim.config, conn, simulated=True)
    while status in ("interrupted", "waiting", "approving"):
        if status in ("waiting", "approving"):
            status = execute(sim.app, None, sim.config, conn, simulated=True,
                             answers=answer_in_terminal(sim.app, sim.config, status))
            continue
        nxt = ", ".join(pending(sim.app, sim.config))
        if input(f"další uzel: {nxt} — Enter = krok, q = konec > ").strip().lower() == "q":
            break
        status = execute(sim.app, None, sim.config, conn, simulated=True)
    print(status)
    print(sim.backlog_path.read_text(encoding="utf-8"))
    return 0


def _real_app(home: Path, cfg):
    backlog_path = home / "BACKLOG.md"
    conn, saver = open_db(home)
    nodes = real_nodes(cfg) | {"pick_tasks": make_pick_tasks(backlog_path, cfg), "report": make_report(backlog_path),
                               "discover": make_discover(backlog_path, lambda name, known: ("[]", [])),
                               "prioritize": make_prioritize(backlog_path, cfg, lambda c, n, f, w: ("[]", [])),
                               "approve": make_approve(backlog_path)}
    return build_graph(nodes, saver), backlog_path, conn


def _run(args, discover: bool) -> int:
    cfg = load_config(args.home / "projects.yaml")
    # preflight checks this per task, but --resume jumps straight back into implement
    if err := check_billing(cfg.billing, os.environ):
        print(err, file=sys.stderr)
        return 2
    app, backlog_path, conn = _real_app(args.home, cfg)
    resume = getattr(args, "resume", None)
    run_id = resume or f"{'go' if discover else 'run'}-{time.strftime('%Y%m%d-%H%M%S')}"
    inputs = None if resume else initial_state(run_id, cfg, simulated=False, discover=discover)
    config = run_config(run_id)
    status = execute(app, inputs, config, conn, simulated=False)
    while status in ("waiting", "approving"):
        status = execute(app, None, config, conn, simulated=False, answers=answer_in_terminal(app, config, status))
    for err in app.get_state(config).values.get("go_errors") or []:
        print(f"go: {err}", file=sys.stderr)
    print(f"{run_id} {status}")
    if status == "paused":
        print(f"resume: harness run --resume {run_id}")
    return 0


def cmd_run(args) -> int:
    return _run(args, discover=False)


def cmd_go(args) -> int:
    return _run(args, discover=True)


def _scan_into(cfg, backlog_path: Path) -> int:
    """Scan outside the backlog lock (git fetch is slow), then append only proposals still unknown."""
    _, props = scan_all(cfg, backlog_path.read_bytes().decode("utf-8"))

    def add(text: str) -> str:
        known = {t.id for t in backlog.parse(text)}
        for p in props:
            if p.id not in known:
                text = backlog.add_tasks(text, p.project, proposal_lines(p))
        return text
    edit_backlog(backlog_path, add)
    return len(props)


def cmd_serve(args) -> int:
    token, chat = os.environ.get("HARNESS_TELEGRAM_TOKEN"), os.environ.get("HARNESS_TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("set HARNESS_TELEGRAM_TOKEN and HARNESS_TELEGRAM_CHAT_ID (see README → serve)", file=sys.stderr)
        return 2
    cfg = load_config(args.home / "projects.yaml")
    if err := check_billing(cfg.billing, os.environ):
        print(err, file=sys.stderr)
        return 2
    app, backlog_path, conn = _real_app(args.home, cfg)
    state_path = args.home / "serve_state.json"
    ctx = serve.Ctx(cfg=cfg, bot=Telegram(token, int(chat)), app=app, backlog_path=backlog_path, conn=conn,
                    new_inputs=lambda rid: initial_state(rid, cfg, simulated=False),
                    state=serve.load_state(state_path, datetime.now()),
                    spawn=lambda job: threading.Thread(target=job, daemon=True).start(),
                    now=datetime.now, scan=lambda: _scan_into(cfg, backlog_path), state_path=state_path)
    print(f"harness serve: window {serve.fmt_window(cfg.serve.window)}, Ctrl+C to stop")
    serve.say(ctx, "🟢 harness serve běží. /help")
    try:
        serve.loop(ctx)
    except KeyboardInterrupt:  # a running run continues from its checkpoint on the next start
        serve.save_state(state_path, ctx.state)
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
    new_text, props = scan_all(cfg, text, names, args.limit)
    for name in names:
        mine = [p for p in props if p.project == name]
        for p in mine:
            print(f"{name}: {p.id} {p.title} ({p.path}:{p.line})")
        if not mine:
            print(f"{name}: no new proposals")
    if args.write and props:
        (args.home / "BACKLOG.md").write_bytes(new_text.encode("utf-8"))
        print(f"{len(props)} proposal(s) written as [~]; change to [ ] to approve")
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
    sub.add_parser("go")
    sub.add_parser("serve")
    sub.add_parser("prune")
    sub.add_parser("queue")
    sc = sub.add_parser("scan")
    sc.add_argument("--write", action="store_true")
    sc.add_argument("--project")
    sc.add_argument("--limit", type=int, default=5)
    args = p.parse_args(argv)
    handlers = {"simulate": cmd_simulate, "run": cmd_run, "dashboard": cmd_dashboard, "prune": cmd_prune,
                "queue": cmd_queue, "scan": cmd_scan, "serve": cmd_serve, "go": cmd_go}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
