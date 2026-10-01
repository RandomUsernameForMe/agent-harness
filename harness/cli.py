import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from . import events
from .runner import execute
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
    handlers = {"simulate": cmd_simulate, "run": _todo, "dashboard": _todo, "prune": _todo}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
