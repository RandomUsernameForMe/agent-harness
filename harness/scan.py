import hashlib
import re
import subprocess
from dataclasses import dataclass

from .backlog import Task
from .nodes.real import git
from .projects import Project

MARKER_RE = re.compile(r"\b(TODO|FIXME)\b[:\s-]*(.*)")


@dataclass(frozen=True)
class Proposal:
    id: str
    project: str
    path: str
    line: int
    title: str


def _proposal(project: str, path: str, line: int, text: str) -> Proposal | None:
    m = MARKER_RE.search(text)
    if not m:
        return None
    # id ignores the line number so a proposal survives code moving around it
    digest = hashlib.sha1(f"{path}\0{text.strip()}".encode()).hexdigest()[:6]
    title = f"{m[1]}: {m[2].strip()}" if m[2].strip() else f"{m[1]} v {path}"
    return Proposal(f"{re.sub(r'[^A-Za-z0-9._-]', '-', project)}-todo-{digest}", project, path, line, title[:100])


def scan_project(project: Project, limit: int = 5) -> list[Proposal]:
    """TODO/FIXME markers on origin/<base> (never the user's working copy), first `limit` by path."""
    ref = f"origin/{project.base}"
    git(["fetch", "-q", "origin", project.base], project.path)
    # markdown excluded: plans/specs/notes quote TODOs as prose, not as work left in code
    out = subprocess.run(["git", "grep", "-n", "-I", "-w", "-E", "TODO|FIXME", ref, "--", ".", ":!*.md"], cwd=project.path,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode not in (0, 1):  # 1 = no match
        raise RuntimeError(f"git grep failed in {project.name}: {out.stderr.strip()}")
    props = []
    for row in out.stdout.splitlines():
        # ponytail: a path containing ":" is mis-split and skipped; use -z if that ever matters
        path, line, text = row.removeprefix(f"{ref}:").split(":", 2)
        if line.isdigit() and (p := _proposal(project.name, path, int(line), text)):
            props.append(p)
    props.sort(key=lambda p: (p.path, p.line))
    return props[:limit]


def new_proposals(props: list[Proposal], tasks: list[Task]) -> list[Proposal]:
    known = {t.id for t in tasks}
    return [p for p in props if p.id not in known]


def proposal_lines(p: Proposal) -> list[str]:
    return [f"- [~] P3 {p.id} {p.title}",
            f"  Návrh ze skenu: {p.path}:{p.line}. Vyřeš poznámku a odstraň ji; ověř existujícími testy."]
