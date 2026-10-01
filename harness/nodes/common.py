from dataclasses import asdict
from pathlib import Path

from .. import backlog
from ..projects import Config

RESET = {"task": None, "outcome": None, "attempts": 0, "verify_ok": False, "last_failure": "",
         "plan": "", "pr_url": "", "branch": "", "worktree": "", "baseline_failures": []}


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def make_pick_task(backlog_path: Path, cfg: Config):
    def pick_task(state: dict) -> dict:
        if state.get("tasks_done", 0) >= state.get("max_tasks", cfg.max_tasks_per_run):
            return RESET
        if (state.get("quota") or 0) >= state.get("quota_threshold", cfg.quota_threshold):
            return RESET
        task = backlog.next_task(backlog.parse(_read(backlog_path)), set(cfg.projects))
        if task is None:
            return RESET
        return RESET | {"task": asdict(task), "max_attempts": cfg.projects[task.project].max_attempts}
    return pick_task


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
        text = backlog.set_status(_read(backlog_path), state["task"]["id"], status, note)
        backlog_path.write_bytes(text.encode("utf-8"))
        return {"tasks_done": state.get("tasks_done", 0) + 1}
    return report
