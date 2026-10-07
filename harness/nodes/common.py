import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from langgraph.types import interrupt

from .. import backlog
from ..backlog import Task
from ..discover import apply_approval, candidates, fallback_plan, json_array, parse_proposals, parse_ranking
from ..graph import RateLimited
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
        planned = state.get("planned")
        if planned is None:
            ranked = rank(tasks, set(cfg.projects), config_focus(cfg), weights)
        else:
            order = {tid: i for i, tid in enumerate(planned)}
            ranked = sorted((t for t in tasks if t.status == " " and t.id in order), key=lambda t: order[t.id])
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


def _error(where: str, e: Exception) -> str:
    return f"{where}: {type(e).__name__}: {e}"


def _spent(agent: dict) -> dict:
    """Usage and quota of an agent call; quota feeds pick_tasks' reserve check before workers start."""
    return {"usage": agent.get("usage") or [], "quota": agent.get("quota")}


def make_discover(backlog_path: Path, explore: Callable[[str, list[Task]], tuple[str, dict]]):
    """Proposals of one project (Send payload {"project"}). A failure costs only this project's proposals.
    `explore` returns the model's answer and {"usage", "quota"} of the call."""
    def discover(state: dict) -> dict:
        name = state["project"]
        with _backlog_lock:
            known = [t for t in backlog.parse(_read(backlog_path)) if t.project == name]
        try:
            text, agent = explore(name, known)
        except RateLimited:
            raise
        except Exception as e:
            return {"proposals": [], "go_errors": [_error(name, e)]}
        errors = [] if json_array(text) is not None else [f"{name}: odpověď bez JSON pole"]
        return {"proposals": parse_proposals(text, name, known), "go_errors": errors} | _spent(agent)
    return discover


def make_prioritize(backlog_path: Path, cfg: Config,
                    rank_fn: Callable[[list[dict], int, dict, dict], tuple[str, dict]]):
    weights = {name: p.weight for name, p in cfg.projects.items()}

    def prioritize(state: dict) -> dict:
        with _backlog_lock:
            tasks = backlog.parse(_read(backlog_path))
        cands = candidates(state.get("proposals") or [], tasks, set(cfg.projects))
        if not cands:
            return {"plan": []}
        n, focus = state.get("max_tasks", cfg.max_tasks_per_run), config_focus(cfg)
        try:
            text, agent = rank_fn(cands, n, focus, weights)
            errors = []
        except RateLimited:
            raise
        except Exception as e:
            text, agent, errors = "", {}, [_error("prioritize", e)]
        plan = parse_ranking(text, cands, n) or fallback_plan(cands, focus, weights, n)
        return {"plan": plan, "go_errors": errors} | _spent(agent)
    return prioritize


def make_approve(backlog_path: Path):
    """Owner approves the plan once (interrupt → list of kept indices or "q"); proposals land in BACKLOG."""
    def approve(state: dict) -> dict:
        plan, proposals = state.get("plan") or [], state.get("proposals") or []
        answer = interrupt({"plan": plan}) if plan else "q"
        kept = None if answer == "q" else [plan[i]["id"] for i in answer]
        edit_backlog(backlog_path, lambda text: apply_approval(text, proposals, plan, kept))
        return {"planned": kept or []}
    return approve
