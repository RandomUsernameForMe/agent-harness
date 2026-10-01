import re
from dataclasses import dataclass

HEADER_RE = re.compile(r"^## (?P<project>\S+)\s*$")
# id becomes a git branch name and CLI argument → restrict to safe characters
TASK_RE = re.compile(r"^- \[(?P<status>[ x!?~])\] P(?P<prio>[1-3]) (?P<id>[A-Za-z0-9._-]+) (?P<rest>.+)$")
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
    # only finished tasks carry a " → url" / " — note" suffix; an open/proposed title may contain "—" itself
    if status in " ~":
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


def add_tasks(text: str, project: str, lines: list[str]) -> str:
    """Append task lines at the end of the project's section (created at the end of the file if missing)."""
    eol = "\r\n" if "\r\n" in text else "\n"
    rows = text.splitlines()
    start = next((i for i, r in enumerate(rows) if (m := HEADER_RE.match(r)) and m["project"] == project), None)
    if start is None:
        head = rows + [""] if rows else []
        return eol.join(head + [f"## {project}", *lines]) + eol
    end = next((i for i in range(start + 1, len(rows)) if HEADER_RE.match(rows[i])), len(rows))
    while end > start + 1 and not rows[end - 1].strip():
        end -= 1
    return eol.join(rows[:end] + lines + rows[end:]) + eol
