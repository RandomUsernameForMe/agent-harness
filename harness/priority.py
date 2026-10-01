import re
from datetime import date, timedelta
from pathlib import Path

from .backlog import Task
from .projects import Config

CAPTURE_DIRS = ("Thoughts", "Inbox")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def rank(tasks: list[Task], projects, focus: dict[str, int] | None = None,
         weights: dict[str, int] | None = None) -> list[Task]:
    """Open tasks of known projects: explicit priority first, then secondbrain focus, project weight, file order."""
    focus, weights = focus or {}, weights or {}
    open_tasks = [t for t in tasks if t.status == " " and t.project in projects]
    return sorted(open_tasks, key=lambda t: (t.priority, -focus.get(t.project, 0), -weights.get(t.project, 0)))


def pick_batch(ranked: list[Task], n: int) -> list[Task]:
    """Up to n tasks, at most one per project (parallel workers must not share a repo)."""
    batch: list[Task] = []
    for t in ranked:
        if len(batch) >= n:
            break
        if all(b.project != t.project for b in batch):
            batch.append(t)
    return batch


def _mention_re(name: str) -> re.Pattern:
    parts = [re.escape(p) for p in re.split(r"[-_\s]+", name)]
    return re.compile(r"(?<![\w-])" + r"[-_\s]?".join(parts) + r"(?![\w-])", re.IGNORECASE)


def _recent_captures(root: Path, since: date) -> list[Path]:
    files = []
    for d in CAPTURE_DIRS:
        for p in (root / d).glob("*.md"):
            m = DATE_RE.match(p.name)
            if m and date.fromisoformat(m[1]) >= since:
                files.append(p)
    return files


def focus(root: Path, projects, days: int, today: date | None = None) -> dict[str, int]:
    """How many recent secondbrain captures (Thoughts/, Inbox/) mention each project."""
    since = (today or date.today()) - timedelta(days=days)
    texts = [p.read_text(encoding="utf-8", errors="replace") for p in _recent_captures(Path(root), since)]
    return {name: sum(1 for t in texts if _mention_re(name).search(t)) for name in projects}


def config_focus(cfg: Config) -> dict[str, int]:
    if cfg.secondbrain is None:
        return {}
    path, days = cfg.secondbrain
    return focus(path, cfg.projects, days)
