import re
import sys
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import yaml

REQUIRED = ("path", "base", "verify")
TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    base: str
    setup: tuple[str, ...]
    verify: tuple[str, ...]
    allowed: tuple[str, ...]
    max_attempts: int
    max_diff_lines: int
    weight: int = 0
    discover: bool = True  # false: `go` discover and the marker scan skip it (tasks only from reflection / by hand)


@dataclass(frozen=True)
class Serve:
    digest_at: time = time(7)  # daily Telegram digest of the last 24 h
    question_timeout_hours: float = 24
    scan: bool = True


@dataclass(frozen=True)
class Config:
    billing: str
    max_tasks_per_run: int
    quota_threshold: float
    projects: dict[str, Project]
    max_parallel: int = 2
    secondbrain: tuple[Path, int] | None = None  # (vault path, look-back days)
    serve: Serve = field(default_factory=Serve)
    plan_model: str = "opus"


def load_config(path: Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    billing = raw.get("billing", "subscription")
    if billing not in ("subscription", "api"):
        raise ValueError(f"billing must be subscription|api, got {billing!r}")
    defaults = raw.get("defaults", {})
    projects = {
        name: _project(name, spec, defaults, path.parent)
        for name, spec in (raw.get("projects") or {}).items()
    }
    max_parallel = raw.get("max_parallel", 2)
    if not isinstance(max_parallel, int) or max_parallel < 1:
        raise ValueError(f"max_parallel must be a positive int, got {max_parallel!r}")
    brain = raw.get("secondbrain")
    secondbrain = ((path.parent / brain["path"]).resolve(), brain.get("days", 14)) if brain else None
    return Config(billing, raw.get("max_tasks_per_run", 3), raw.get("quota_threshold", 0.7), projects,
                  max_parallel, secondbrain, _serve(raw.get("serve") or {}), raw.get("plan_model", "opus"))


def _time(text: str) -> time:
    m = TIME_RE.match(str(text))
    try:
        return time(int(m[1]), int(m[2]))
    except (TypeError, ValueError):  # no match / hour 25
        raise ValueError(f"serve.digest_at must look like 07:00, got {text!r}") from None


def _serve(raw: dict) -> Serve:
    if "window" in raw:
        print("projects.yaml: serve.window is ignored, autorun runs any time (limits in serve.py)", file=sys.stderr)
    return Serve(_time(raw.get("digest_at", "07:00")), raw.get("question_timeout_hours", 24), raw.get("scan", True))


def _project(name: str, spec: dict, defaults: dict, root: Path) -> Project:
    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        raise ValueError(f"project {name}: missing {', '.join(missing)}")
    return Project(
        name=name,
        path=(root / spec["path"]).resolve(),
        base=spec["base"],
        setup=tuple(spec.get("setup", [])),
        verify=tuple(spec["verify"]),
        allowed=tuple(spec.get("allowed", [])),
        max_attempts=spec.get("max_attempts", defaults.get("max_attempts", 3)),
        max_diff_lines=spec.get("max_diff_lines", defaults.get("max_diff_lines", 800)),
        weight=spec.get("weight", 0),
        discover=spec.get("discover", True),
    )
