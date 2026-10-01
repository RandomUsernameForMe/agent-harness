import re
from dataclasses import dataclass

HEADER_RE = re.compile(r"^## (?P<project>\S+)\s*$")
# id becomes a git branch name and CLI argument → restrict to safe characters
TASK_RE = re.compile(r"^- \[(?P<status>[ x!?])\] P(?P<prio>[1-3]) (?P<id>[A-Za-z0-9._-]+) (?P<rest>.+)$")
SUFFIX_RE = re.compile(r" (?:→|—) ")


@dataclass(frozen=True)
class Task:
    id: str
    project: str
    priority: int
    title: str
    status: str
    description: str


def _title(rest: str, status: str) -> str:
    # only finished tasks carry a " → url" / " — note" suffix; an open title may contain "—" itself
    if status == " ":
        return rest.strip()
    return SUFFIX_RE.split(rest, maxsplit=1)[0].strip()


def parse(text: str) -> list[Task]:
    raw: list[dict] = []
    project, current = None, None
    for line in text.splitlines():
        if m := HEADER_RE.match(line):
            project, current = m["project"], None
        elif (m := TASK_RE.match(line)) and project:
            current = {"id": m["id"], "project": project, "priority": int(m["prio"]),
                       "title": _title(m["rest"], m["status"]), "status": m["status"], "description": []}
            raw.append(current)
        elif current is not None and line.startswith("  ") and line.strip():
            current["description"].append(line.strip())
        elif line.strip():
            current = None
    seen: set[str] = set()
    for t in raw:
        if t["id"] in seen:
            raise ValueError(f"duplicate task id: {t['id']}")
        seen.add(t["id"])
    return [Task(**{**t, "description": "\n".join(t["description"])}) for t in raw]


def next_task(tasks: list[Task], projects: set[str]) -> Task | None:
    open_tasks = [t for t in tasks if t.status == " " and t.project in projects]
    return min(open_tasks, key=lambda t: t.priority, default=None)


def set_status(text: str, task_id: str, status: str, note: str = "") -> str:
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        m = TASK_RE.match(body)
        if m and m["id"] == task_id:
            eol = line[len(body):]
            lines[i] = f"- [{status}] P{m['prio']} {task_id} {_title(m['rest'], m['status'])}{note}{eol}"
            return "".join(lines)
    raise KeyError(task_id)
