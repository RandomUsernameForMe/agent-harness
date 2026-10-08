import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .. import backlog
from ..priority import config_focus, pick_batch, rank
from ..projects import Config

# parallel workers report into the same BACKLOG.md (read-modify-write)
_backlog_lock = threading.Lock()


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def edit_backlog(path: Path, fn: Callable[[str], str]) -> None:
    with _backlog_lock:
        path.write_bytes(fn(_read(path)).encode("utf-8"))


def make_pick_tasks(backlog_path: Path, cfg: Config):
    weights = {name: p.weight for name, p in cfg.projects.items()}

    def pick_tasks(state: dict) -> dict:
        left = state.get("max_tasks", cfg.max_tasks_per_run) - state.get("tasks_done", 0)
        if left <= 0 or (state.get("quota") or 0) >= state.get("quota_threshold", cfg.quota_threshold):
            return {"batch": []}
        with _backlog_lock:
            tasks = backlog.parse(_read(backlog_path))
        ranked = rank(tasks, set(cfg.projects), config_focus(cfg), weights)
        batch = pick_batch(ranked, min(left, state.get("max_parallel", cfg.max_parallel)))
        return {"batch": [{"task": asdict(t), "max_attempts": cfg.projects[t.project].max_attempts} for t in batch]}
    return pick_tasks


def _note(state: dict) -> tuple[str, str]:
    outcome = state.get("outcome") or "failed"
    detail = (state.get("last_failure") or "").replace("\r", " ").replace("\n", " ")
    if outcome == "pr_opened":
        return "x", f" → {state['pr_url']}"
    if outcome == "needs_clarification":
        return "?", f" — UNCLEAR: {detail}"[:200]
    return "!", f" — {outcome}: {detail}"[:200]


def make_report(backlog_path: Path):
    def report(state: dict) -> dict:
        status, note = _note(state)
        edit_backlog(backlog_path, lambda text: backlog.set_status(text, state["task"]["id"], status, note))
        return {}
    return report
