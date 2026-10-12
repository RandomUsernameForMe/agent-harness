import re
from dataclasses import dataclass

HEADER_RE = re.compile(r"^## (?P<project>\S+)\s*$")
# id becomes a git branch name and CLI argument → restrict to safe characters
TASK_RE = re.compile(r"^- \[(?P<status>[ x!?~-])\] P(?P<prio>[1-3]) (?P<id>[A-Za-z0-9._-]+) (?P<rest>.+)$")
SUFFIX_RE = re.compile(r" (?:→|—) ")
# proposal kind as a title prefix: "- [~] P2 id [feature] Title"; set_status keeps it untouched
KIND_RE = re.compile(r"^\[(?P<kind>test|bug|debt|feature|docs|refactor)\] (?P<title>.+)$")


@dataclass(frozen=True)
class Task:
    id: str
    project: str
    priority: int
    title: str
    status: str
    description: str
    note: str = ""  # suffix of a finished task: PR url, failure, rejection reason
    kind: str = ""  # test|bug|debt|feature|docs|refactor; "" = written by hand or before kinds existed


def _split(rest: str, status: str) -> tuple[str, str]:
    # only finished tasks carry a " → url" / " — note" suffix; an open/proposed title may contain "—" itself
    if status in " ~":
        return rest.strip(), ""
    title, note = (SUFFIX_RE.split(rest, maxsplit=1) + [""])[:2]
    return title.strip(), note.strip()


def _title(rest: str, status: str) -> str:
    return _split(rest, status)[0]


def parse(text: str) -> list[Task]:
    raw: list[dict] = []
    project, current = None, None
    for line in text.splitlines():
        if m := HEADER_RE.match(line):
            project, current = m["project"], None
        elif (m := TASK_RE.match(line)) and project:
            title, note = _split(m["rest"], m["status"])
            k = KIND_RE.match(title)
            current = {"id": m["id"], "project": project, "priority": int(m["prio"]),
                       "title": k["title"] if k else title, "status": m["status"], "description": [], "note": note,
                       "kind": k["kind"] if k else ""}
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


def add_description(text: str, task_id: str, line: str) -> str:
    """Append an indented description line after the task's existing description lines."""
    lines = text.splitlines(keepends=True)
    for i, row in enumerate(lines):
        m = TASK_RE.match(row.rstrip("\r\n"))
        if m and m["id"] == task_id:
            eol = row[len(row.rstrip("\r\n")):] or "\n"
            end = i + 1
            while end < len(lines) and lines[end].startswith("  ") and lines[end].strip():
                end += 1
            if end > 0 and not lines[end - 1].endswith(("\n", "\r")):
                lines[end - 1] += eol
            lines.insert(end, f"  {line}{eol}")
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
