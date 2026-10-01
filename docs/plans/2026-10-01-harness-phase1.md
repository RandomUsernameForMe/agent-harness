# Harness fáze 1 — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LangGraph harness, který vezme úkol z `BACKLOG.md`, nechá ho naplánovat (Opus) a implementovat (Sonnet) ve worktree přes Claude Agent SDK na subscription, ověří ho a otevře draft PR. K tomu dashboard a simulace scénářů s falešnými uzly.

**Architecture:** „Tenký graf, tlustý worker“. `graph.py` definuje `StateGraph` a dostane implementace uzlů zvenku: skutečné (`nodes/real.py`) nebo falešné řízené scénářem (`nodes/fake.py`). `pick_task` a `report` jsou sdílené (`nodes/common.py`). `runner.execute` streamuje graf a zapisuje eventy do SQLite. Ze stejného souboru čte dashboard (FastAPI + statické HTML s Mermaid).

**Tech Stack:** Python 3.11, langgraph 1.2.x, langgraph-checkpoint-sqlite 3.1.x, claude-agent-sdk ≥0.2.163, FastAPI, uvicorn, PyYAML, pytest. Verze a API ověřené 2026-10-01 v dočasném venv.

**Spec:** `docs/specs/2026-10-01-harness-phase1-design.md`

## Global Constraints

- Python ≥ 3.11; všechny příkazy se spouštějí z `C:\Users\TGJ\Projects\harness` v Git Bash, venv `.venv` (`.venv/Scripts/python`).
- `claude_agent_sdk` importuje **jen** `harness/sdk.py`.
- Workers: `setting_sources=["project"]`, `system_prompt` preset `claude_code` + `append`. Globální `~/.claude` se nenačítá.
- `billing: subscription` + nastavený `ANTHROPIC_API_KEY` → preflight selže.
- Harness nikdy nepushuje do base větve, nemerguje a otevírá jen `--draft` PR.
- Worktree: `<Projects>/.harness-worktrees/<projekt>/<task-id>`, větev `harness/<task-id>`, vždy z `origin/<base>`.
- Výchozí hodnoty: `max_attempts=3`, `max_diff_lines=800`, `max_tasks_per_run=3`, `quota_threshold=0.7`, `last_failure` ≤ 4000 znaků.
- Dashboard poslouchá jen na `127.0.0.1:8765`; `POST /simulate` nikdy nespouští skutečný běh.
- **Každý commit musí obsahovat řádek v `CHANGELOG.md`** ve formátu `## [2026-10-01] - popis` (vynucuje globální hook). V každém kroku commitu je proto `git add CHANGELOG.md`.
- `BACKLOG.md` se čte a zapisuje binárně (`read_bytes`/`write_bytes`), aby se zachovaly konce řádků (CRLF).
- `shell=True` (`sh()`) jen pro příkazy z `projects.yaml`, které píše vlastník. Hodnoty z BACKLOGu (ID, název) a od modelu se předávají výhradně jako argumenty v seznamu. ID úkolu je omezené na `[A-Za-z0-9._-]`.
- Konfigurace grafu: `recursion_limit=200` (výchozích 25 nestačí pro 3 úkoly s retry).

### Odchylky od specu (vědomé, schválit při review plánu)

1. **Pořadí uzlů:** `make_worktree` je **před** `plan`. Pracovní kopie uživatele bývá na jiné větvi (guth-pictures je na `codex/editorial-redesign`), takže plán musí číst kód z `origin/<base>` ve worktree. Cena je worktree i pro úkoly, které skončí jako nejasné (`harness prune` je uklidí).
2. **Pauza na rate limit:** `RateLimited` výjimka **propadne z grafu**, místo aby nastavila `outcome=paused`. Checkpoint zůstane na přerušeném uzlu a `--resume` ho spustí znovu (ověřeno spikem: `stream(None, config)` po výjimce zopakuje jen spadlý uzel). `runner` zapíše event `paused`. Chování je stejné jako ve specu, jen nativní cestou LangGraphu.
3. **Baseline** je na úrovni **příkazů**, ne jednotlivých testů. Verify příkaz, který padá už na base větvi, se do gate nepočítá. Strop: regrese uvnitř takového příkazu se nepozná (označeno `ponytail:` komentářem).
4. **Uzly `make_worktree` a `open_pr`** mají `RetryPolicy` a nejsou obalené v `_safe`. Když selžou i po 3 pokusech, běh se zastaví (resume možné), nepokračuje se do `report`.

## Review Focus

1. **CRLF v `BACKLOG.md`** (Windows/Obsidian): zápis stavu nesmí přepsat konce řádků ani jiné úkoly. Test v Task 2.
2. **Více úkolů v jednom běhu:** pořadí podle priority, zastavení na `max_tasks`, žádný `GraphRecursionError` při 3 úkolech s retry. Scénáře `two-tasks-priority` a `three-tasks-flaky` v Task 6.
3. **`ANTHROPIC_API_KEY` nastavený při `billing: subscription`:** preflight musí selhat dřív, než se zavolá model. Test `check_billing` v Task 8.
4. **Opakovaný `open_pr` po částečném úspěchu** (commit prošel, push spadl): retry nesmí spadnout na „nothing to commit“ ani vytvořit druhý PR. Test `commit_if_dirty` a `make_worktree` reuse na lokálním git repu v Task 9.
5. **Název scénáře z dashboardu** (`../../etc`): jen scénáře ze seznamu, jinak 404. Test v Task 10.

---

## File Structure

```
harness/
  pyproject.toml
  CHANGELOG.md                 # existuje
  .gitignore                   # existuje, doplnit .sim/
  projects.yaml                # Task 12
  BACKLOG.md                   # Task 12
  harness/
    __init__.py
    projects.py                # Config, Project, load_config
    backlog.py                 # Task, parse, next_task, set_status
    guard.py                   # check_diff, new_failures
    events.py                  # SQLite event log + dotazy pro dashboard
    graph.py                   # State, NODES, RateLimited, build_graph, initial_state, graph_mermaid
    runner.py                  # execute(app, inputs, config, conn, simulated)
    simulate.py                # load_scenario, prepare, Simulation
    sdk.py                     # run_agent, WorkerResult, check_billing, READ_TOOLS...
    cli.py                     # harness simulate|run|dashboard|prune
    nodes/__init__.py
    nodes/common.py            # make_pick_task, make_report
    nodes/fake.py              # Script, fake_nodes
    nodes/real.py              # real_nodes, sh, git, worktree_path, commit_if_dirty
    prompts/plan.md
    prompts/implement.md
    dashboard/__init__.py
    dashboard/app.py           # create_app(home)
    dashboard/static/index.html
  scenarios/*.yaml             # 8 scénářů
  scripts/check_isolation.py   # spike: worker nevidí globální pluginy
  tests/
    test_projects.py test_backlog.py test_guard.py test_events.py
    test_graph.py test_scenarios.py test_cli.py test_sdk.py
    test_real_git.py test_dashboard.py
```

---

### Task 1: Scaffold + konfigurace projektů

**Files:**
- Create: `pyproject.toml`, `harness/__init__.py`, `harness/nodes/__init__.py`, `harness/dashboard/__init__.py`, `harness/projects.py`
- Modify: `.gitignore`
- Test: `tests/test_projects.py`

**Interfaces:**
- Produces: `Project(name, path: Path, base, setup: tuple[str,...], verify: tuple[str,...], allowed: tuple[str,...], max_attempts: int, max_diff_lines: int)`, `Config(billing: str, max_tasks_per_run: int, quota_threshold: float, projects: dict[str, Project])`, `load_config(path: Path) -> Config`

- [ ] **Step 1: pyproject + venv**

`pyproject.toml`:
```toml
[project]
name = "harness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "langgraph>=1.2,<2",
  "langgraph-checkpoint-sqlite>=3.1,<4",
  "claude-agent-sdk>=0.2.163",
  "fastapi>=0.142",
  "uvicorn>=0.54",
  "pyyaml>=6",
]

[project.optional-dependencies]
dev = ["pytest>=9", "httpx>=0.28"]

[project.scripts]
harness = "harness.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["harness*"]

[tool.setuptools.package-data]
harness = ["prompts/*.md", "dashboard/static/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Prázdné soubory `harness/__init__.py`, `harness/nodes/__init__.py`, `harness/dashboard/__init__.py`. Do `.gitignore` přidat řádek `.sim/` a `*.egg-info/`.

Run: `python -m venv .venv && .venv/Scripts/python -m pip install -q -e ".[dev]"`
Expected: bez chyby; `.venv/Scripts/harness.exe` existuje (zatím spadne na chybějícím `cli`, to je OK).

- [ ] **Step 2: Failing test**

`tests/test_projects.py`:
```python
from pathlib import Path

import pytest

from harness.projects import load_config


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "projects.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_and_relative_path(tmp_path):
    cfg = load_config(write(tmp_path, """
defaults: {max_attempts: 5}
projects:
  demo:
    path: ../demo
    base: main
    verify: ["npm test"]
"""))
    p = cfg.projects["demo"]
    assert p.path == (tmp_path / ".." / "demo").resolve()
    assert p.max_attempts == 5
    assert p.max_diff_lines == 800
    assert p.setup == () and p.allowed == ()
    assert cfg.billing == "subscription"
    assert cfg.max_tasks_per_run == 3
    assert cfg.quota_threshold == 0.7


def test_project_overrides_defaults(tmp_path):
    cfg = load_config(write(tmp_path, """
defaults: {max_attempts: 5}
projects:
  demo: {path: ., base: dev, verify: [x], max_attempts: 1, allowed: ["bot/**"]}
"""))
    assert cfg.projects["demo"].max_attempts == 1
    assert cfg.projects["demo"].allowed == ("bot/**",)


def test_missing_verify_raises(tmp_path):
    with pytest.raises(ValueError, match="demo: missing verify"):
        load_config(write(tmp_path, "projects:\n  demo: {path: ., base: main}\n"))


def test_bad_billing_raises(tmp_path):
    with pytest.raises(ValueError, match="billing"):
        load_config(write(tmp_path, "billing: free\nprojects: {}\n"))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_projects.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'harness.projects'`

- [ ] **Step 4: Implementation**

`harness/projects.py`:
```python
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED = ("path", "base", "verify")


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


@dataclass(frozen=True)
class Config:
    billing: str
    max_tasks_per_run: int
    quota_threshold: float
    projects: dict[str, Project]


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
    return Config(billing, raw.get("max_tasks_per_run", 3), raw.get("quota_threshold", 0.7), projects)


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
    )
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_projects.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

Do `CHANGELOG.md` přidat `## [2026-10-01] - Scaffold harness balíčku a načítání projects.yaml`.
```bash
git add pyproject.toml .gitignore harness tests CHANGELOG.md
git commit -m "feat: scaffold package and project config loader"
```

---

### Task 2: BACKLOG parser a zápis

**Files:**
- Create: `harness/backlog.py`
- Test: `tests/test_backlog.py`

**Interfaces:**
- Produces: `Task(id, project, priority: int, title, status: str, description: str)` (frozen dataclass), `parse(text: str) -> list[Task]`, `next_task(tasks: list[Task], projects: set[str]) -> Task | None`, `set_status(text: str, task_id: str, status: str, note: str = "") -> str`. `status` ∈ `" "`, `"x"`, `"!"`, `"?"`. `note` začíná `" → "` nebo `" — "`.

- [ ] **Step 1: Failing test**

`tests/test_backlog.py`:
```python
import pytest

from harness.backlog import next_task, parse, set_status

SAMPLE = """# Backlog

## guth-pictures
- [ ] P2 gp-01 Lazy loading obrázků
  Popis řádek 1.
  Popis řádek 2.
- [x] P1 gp-02 Oprava řazení → https://example.com/pull/42
- [!] P1 gp-03 Migrace — failed: verify
- [?] P3 gp-04 Zlepšit výkon — UNCLEAR: co měřit?

## akano3
- [ ] P1 ak-01 Přidat test
"""


def test_parse_fields():
    tasks = parse(SAMPLE)
    assert [t.id for t in tasks] == ["gp-01", "gp-02", "gp-03", "gp-04", "ak-01"]
    gp1 = tasks[0]
    assert (gp1.project, gp1.priority, gp1.status, gp1.title) == ("guth-pictures", 2, " ", "Lazy loading obrázků")
    assert gp1.description == "Popis řádek 1.\nPopis řádek 2."
    assert tasks[1].title == "Oprava řazení"
    assert tasks[2].status == "!"
    assert tasks[4].project == "akano3"


def test_task_before_header_ignored():
    assert parse("- [ ] P1 x-1 bez projektu\n") == []


def test_duplicate_id_raises():
    with pytest.raises(ValueError, match="duplicate task id: a-1"):
        parse("## p\n- [ ] P1 a-1 one\n- [ ] P2 a-1 two\n")


def test_next_task_priority_then_file_order():
    tasks = parse("## p\n- [ ] P2 a-1 A\n- [ ] P1 b-1 B\n- [ ] P1 c-1 C\n- [x] P1 d-1 D\n")
    assert next_task(tasks, {"p"}).id == "b-1"


def test_unsafe_id_is_not_a_task():
    assert parse("## p\n- [ ] P1 a;rm-rf Bad\n") == []


def test_next_task_filters_projects_and_status():
    tasks = parse(SAMPLE)
    assert next_task(tasks, {"guth-pictures"}).id == "gp-01"
    assert next_task(tasks, {"other"}) is None


def test_set_status_replaces_suffix_and_keeps_rest():
    out = set_status(SAMPLE, "gp-03", "x", " → https://example.com/pull/7")
    assert "- [x] P1 gp-03 Migrace → https://example.com/pull/7\n" in out
    assert out.replace("- [x] P1 gp-03 Migrace → https://example.com/pull/7", "- [!] P1 gp-03 Migrace — failed: verify") == SAMPLE


def test_set_status_preserves_crlf():
    crlf = SAMPLE.replace("\n", "\r\n")
    out = set_status(crlf, "gp-01", "?", " — UNCLEAR: proč?")
    assert "- [?] P2 gp-01 Lazy loading obrázků — UNCLEAR: proč?\r\n  Popis řádek 1.\r\n" in out
    assert out.count("\r\n") == crlf.count("\r\n")
    assert "\n" not in out.replace("\r\n", "")


def test_set_status_unknown_id():
    with pytest.raises(KeyError):
        set_status(SAMPLE, "nope", "x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_backlog.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'harness.backlog'`

- [ ] **Step 3: Implementation**

`harness/backlog.py`:
```python
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


def _title(rest: str) -> str:
    return SUFFIX_RE.split(rest, maxsplit=1)[0].strip()


def parse(text: str) -> list[Task]:
    raw: list[dict] = []
    project, current = None, None
    for line in text.splitlines():
        if m := HEADER_RE.match(line):
            project, current = m["project"], None
        elif (m := TASK_RE.match(line)) and project:
            current = {"id": m["id"], "project": project, "priority": int(m["prio"]),
                       "title": _title(m["rest"]), "status": m["status"], "description": []}
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
            lines[i] = f"- [{status}] P{m['prio']} {task_id} {_title(m['rest'])}{note}{eol}"
            return "".join(lines)
    raise KeyError(task_id)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_backlog.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

CHANGELOG: `## [2026-10-01] - BACKLOG.md parser a zápis stavu`.
```bash
git add harness/backlog.py tests/test_backlog.py CHANGELOG.md
git commit -m "feat: backlog parser with status writes"
```

---

### Task 3: Guard (diff + baseline)

**Files:**
- Create: `harness/guard.py`
- Test: `tests/test_guard.py`

**Interfaces:**
- Produces: `check_diff(changed: list[str], diff_lines: int, allowed: tuple[str, ...], max_lines: int) -> str | None` (vrátí popis porušení, nebo `None`), `new_failures(baseline: list[str], failed: list[str]) -> list[str]`

- [ ] **Step 1: Failing test**

`tests/test_guard.py`:
```python
from harness.guard import check_diff, new_failures


def test_empty_diff():
    assert check_diff([], 0, (), 800) == "empty diff: worker changed nothing"


def test_no_allowed_means_any_path():
    assert check_diff(["Thoughts/x.md"], 10, (), 800) is None


def test_allowed_nested_ok():
    assert check_diff(["bot/a/b.py", "web/app/page.tsx"], 10, ("bot/**", "web/**"), 800) is None


def test_forbidden_path():
    msg = check_diff(["bot/x.py", "Thoughts/2026-10-01.md"], 10, ("bot/**",), 800)
    assert msg == "forbidden paths: Thoughts/2026-10-01.md"


def test_prefix_lookalike_is_forbidden():
    assert check_diff(["botx/a.py"], 1, ("bot/**",), 800) == "forbidden paths: botx/a.py"


def test_too_large():
    assert check_diff(["a.py"], 801, (), 800) == "diff too large: 801 > 800 lines"


def test_new_failures():
    assert new_failures(["npm test"], ["npm test", "npx tsc --noEmit"]) == ["npx tsc --noEmit"]
    assert new_failures([], []) == []
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python -m pytest tests/test_guard.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implementation**

`harness/guard.py`:
```python
from fnmatch import fnmatchcase


def check_diff(changed: list[str], diff_lines: int, allowed: tuple[str, ...], max_lines: int) -> str | None:
    if not changed:
        return "empty diff: worker changed nothing"
    if allowed:
        bad = [p for p in changed if not any(fnmatchcase(p, pat) for pat in allowed)]
        if bad:
            return f"forbidden paths: {', '.join(bad[:10])}"
    if diff_lines > max_lines:
        return f"diff too large: {diff_lines} > {max_lines} lines"
    return None


# ponytail: baseline per command, not per test; a regression inside an already-failing command goes unnoticed — parse test reporters if that bites
def new_failures(baseline: list[str], failed: list[str]) -> list[str]:
    return [c for c in failed if c not in baseline]
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_guard.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

CHANGELOG: `## [2026-10-01] - Guard pro diff a baseline selhání`.
```bash
git add harness/guard.py tests/test_guard.py CHANGELOG.md
git commit -m "feat: diff guard and baseline comparison"
```

---

### Task 4: Event log

**Files:**
- Create: `harness/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Produces: `connect(path: Path) -> sqlite3.Connection`, `log(conn, run_id: str, kind: str, node: str | None = None, delta=None, usage=None, rate_limit=None, simulated: bool = False) -> None`, `events_after(conn, after_id: int = 0, run_id: str | None = None) -> list[dict]`, `runs(conn) -> list[dict]` (klíče `run_id, started, last, simulated, last_kind`), `usage_summary(conn) -> list[dict]` (klíče `day, node, model, input_tokens, output_tokens, cost_usd, simulated`), `latest_rate_limit(conn) -> dict | None`. Řádek eventu: `id, run_id, ts, node, kind, delta, usage, rate_limit, simulated` (JSON pole už dekódovaná, `simulated: bool`).

- [ ] **Step 1: Failing test**

`tests/test_events.py`:
```python
from harness import events


def test_log_and_read(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    events.log(conn, "r1", "run_start", simulated=True)
    events.log(conn, "r1", "end", node="plan", delta={"plan": "x"},
               usage=[{"node": "plan", "model": "opus", "input_tokens": 10, "output_tokens": 2, "cost_usd": 0.5}],
               rate_limit={"type": "five_hour", "utilization": 0.4, "status": "allowed", "resets_at": 1})
    events.log(conn, "r2", "run_start")
    rows = events.events_after(conn, 0, "r1")
    assert [r["kind"] for r in rows] == ["run_start", "end"]
    assert rows[1]["delta"] == {"plan": "x"} and rows[1]["simulated"] is False
    assert rows[0]["simulated"] is True
    assert [r["id"] for r in events.events_after(conn, rows[0]["id"])] == [rows[1]["id"], rows[1]["id"] + 1]


def test_runs_and_usage(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    events.log(conn, "r1", "run_start", simulated=True)
    events.log(conn, "r1", "end", node="implement", usage=[{"node": "implement", "model": "sonnet", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.1}], simulated=True)
    events.log(conn, "r1", "end", node="implement", usage=[{"node": "implement", "model": "sonnet", "input_tokens": 50, "output_tokens": 5, "cost_usd": None}], simulated=True)
    events.log(conn, "r1", "run_end", simulated=True)
    [run] = events.runs(conn)
    assert run["run_id"] == "r1" and run["last_kind"] == "run_end" and run["simulated"] is True
    [u] = events.usage_summary(conn)
    assert (u["node"], u["model"], u["input_tokens"], u["output_tokens"]) == ("implement", "sonnet", 150, 25)
    assert abs(u["cost_usd"] - 0.1) < 1e-9


def test_latest_rate_limit(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    assert events.latest_rate_limit(conn) is None
    events.log(conn, "r", "end", node="plan", rate_limit={"utilization": 0.2})
    events.log(conn, "r", "end", node="implement", rate_limit={"utilization": 0.5})
    assert events.latest_rate_limit(conn) == {"utilization": 0.5}
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python -m pytest tests/test_events.py -v`
Expected: FAIL `ImportError`

- [ ] **Step 3: Implementation**

`harness/events.py`:
```python
import json
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

SCHEMA = """CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts REAL NOT NULL,
  node TEXT,
  kind TEXT NOT NULL,
  delta TEXT,
  usage TEXT,
  rate_limit TEXT,
  simulated INTEGER NOT NULL DEFAULT 0
)"""
JSON_FIELDS = ("delta", "usage", "rate_limit")
_lock = threading.Lock()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def _dump(value):
    return None if value is None else json.dumps(value, default=str, ensure_ascii=False)


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_FIELDS:
        d[k] = json.loads(d[k]) if d[k] else None
    d["simulated"] = bool(d["simulated"])
    return d


def log(conn, run_id, kind, node=None, delta=None, usage=None, rate_limit=None, simulated=False) -> None:
    with _lock:
        conn.execute(
            "INSERT INTO events (run_id, ts, node, kind, delta, usage, rate_limit, simulated) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, time.time(), node, kind, _dump(delta), _dump(usage), _dump(rate_limit), int(simulated)),
        )
        conn.commit()


def events_after(conn, after_id: int = 0, run_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM events WHERE id > ?", [after_id]
    if run_id:
        sql, args = sql + " AND run_id = ?", args + [run_id]
    with _lock:
        rows = conn.execute(sql + " ORDER BY id", args).fetchall()
    return [_row(r) for r in rows]


def runs(conn) -> list[dict]:
    with _lock:
        rows = conn.execute("""
          SELECT run_id, MIN(ts) AS started, MAX(ts) AS last, MAX(simulated) AS simulated,
                 (SELECT kind FROM events e2 WHERE e2.run_id = e.run_id ORDER BY id DESC LIMIT 1) AS last_kind
          FROM events e GROUP BY run_id ORDER BY started DESC""").fetchall()
    return [{**dict(r), "simulated": bool(r["simulated"])} for r in rows]


def usage_summary(conn) -> list[dict]:
    with _lock:
        rows = conn.execute("SELECT ts, usage, simulated FROM events WHERE usage IS NOT NULL").fetchall()
    totals: dict[tuple, dict] = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
        for u in json.loads(r["usage"]):
            t = totals[(day, u.get("node"), u.get("model"), bool(r["simulated"]))]
            t["input_tokens"] += u.get("input_tokens") or 0
            t["output_tokens"] += u.get("output_tokens") or 0
            t["cost_usd"] += u.get("cost_usd") or 0.0
    return [{"day": d, "node": n, "model": m, "simulated": s, **v} for (d, n, m, s), v in sorted(totals.items())]


def latest_rate_limit(conn) -> dict | None:
    with _lock:
        row = conn.execute("SELECT rate_limit FROM events WHERE rate_limit IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["rate_limit"]) if row else None
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_events.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

CHANGELOG: `## [2026-10-01] - SQLite event log`.
```bash
git add harness/events.py tests/test_events.py CHANGELOG.md
git commit -m "feat: sqlite event log"
```

---

### Task 5: Graf + sdílené uzly

**Files:**
- Create: `harness/graph.py`, `harness/nodes/common.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `backlog.parse/next_task/set_status`, `Config`
- Produces (`harness/graph.py`): `State` (TypedDict, viz kód), `NODES: tuple[str, ...]`, `RateLimited(resets_at: int | None)` (výjimka, atribut `resets_at`), `build_graph(nodes: dict[str, Callable[[State], dict]], checkpointer=None, step: bool = False)` → compiled graph, `initial_state(run_id: str, cfg: Config, simulated: bool) -> dict`, `graph_mermaid() -> str`, `after_pick`, `after_verify`.
- Produces (`harness/nodes/common.py`): `make_pick_task(backlog_path: Path, cfg: Config) -> Callable`, `make_report(backlog_path: Path) -> Callable`.

- [ ] **Step 1: Failing test**

`tests/test_graph.py`:
```python
from langgraph.graph import END

from harness.graph import NODES, after_pick, after_verify, build_graph, graph_mermaid


def test_after_pick():
    assert after_pick({"task": {"id": "a"}}) == "preflight"
    assert after_pick({"task": None}) == END


def test_after_verify():
    assert after_verify({"verify_ok": True, "attempts": 1, "max_attempts": 3}) == "open_pr"
    assert after_verify({"verify_ok": False, "attempts": 1, "max_attempts": 3}) == "implement"
    assert after_verify({"verify_ok": False, "attempts": 3, "max_attempts": 3}) == "report"
    assert after_verify({"outcome": "failed", "verify_ok": True}) == "report"


def test_missing_node_rejected():
    import pytest
    with pytest.raises(ValueError, match="missing nodes"):
        build_graph({"pick_task": lambda s: {}})


def test_exception_in_safe_node_becomes_failed_outcome():
    calls = []

    def boom(state):
        raise RuntimeError("kaboom")

    def pick(state):
        if calls:
            return {"task": None}
        calls.append(1)
        return {"task": {"id": "t"}, "outcome": None}

    nodes = {n: (lambda s: {}) for n in NODES} | {"pick_task": pick, "plan": boom,
                                                    "report": lambda s: {"tasks_done": 1}}
    out = build_graph(nodes).invoke({"tasks_done": 0, "usage": []}, {"recursion_limit": 50})
    assert out["outcome"] == "failed"
    assert out["last_failure"] == "RuntimeError: kaboom"


def test_mermaid_lists_all_nodes():
    text = graph_mermaid()
    for n in NODES:
        assert n in text
```

Pozn.: v `test_exception_in_safe_node_becomes_failed_outcome` druhé volání `pick` vrátí `task: None`, ale `outcome` zůstane `failed` (pick ho neresetuje, protože jde o testovací stub). Skutečný `make_pick_task` resetuje.

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python -m pytest tests/test_graph.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'harness.graph'`

- [ ] **Step 3: Implementation — graph.py**

`harness/graph.py`:
```python
import operator
from subprocess import CalledProcessError
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from .projects import Config

NODES = ("pick_task", "preflight", "make_worktree", "plan", "setup", "implement", "verify", "open_pr", "report")
LINEAR = {"preflight": "make_worktree", "make_worktree": "plan", "plan": "setup",
          "setup": "implement", "implement": "verify", "open_pr": "report"}
RETRYING = {"make_worktree", "open_pr"}


class State(TypedDict, total=False):
    run_id: str
    simulated: bool
    max_tasks: int
    quota_threshold: float
    tasks_done: int
    task: dict | None
    max_attempts: int
    branch: str
    worktree: str
    plan: str
    baseline_failures: list[str]
    attempts: int
    verify_ok: bool
    last_failure: str
    outcome: str | None
    pr_url: str
    quota: float | None
    rate_limit: dict | None
    usage: Annotated[list, operator.add]


class RateLimited(Exception):
    def __init__(self, resets_at: int | None):
        super().__init__(f"rate limited until {resets_at}")
        self.resets_at = resets_at


def after_pick(state: State) -> str:
    return "preflight" if state.get("task") else END


def after_verify(state: State) -> str:
    if state.get("outcome"):
        return "report"
    if state.get("verify_ok"):
        return "open_pr"
    return "implement" if state.get("attempts", 0) < state.get("max_attempts", 3) else "report"


def _linear(nxt: str) -> Callable[[State], str]:
    return lambda state: "report" if state.get("outcome") else nxt


def _safe(fn: Callable) -> Callable:
    def wrapped(state: State) -> dict:
        try:
            return fn(state)
        except RateLimited:
            raise
        except Exception as e:
            return {"outcome": "failed", "last_failure": f"{type(e).__name__}: {e}"}
    return wrapped


def build_graph(nodes: dict[str, Callable], checkpointer=None, step: bool = False):
    missing = set(NODES) - set(nodes)
    if missing:
        raise ValueError(f"missing nodes: {sorted(missing)}")
    g = StateGraph(State)
    for name in NODES:
        if name in RETRYING:
            g.add_node(name, nodes[name], retry_policy=RetryPolicy(max_attempts=3, retry_on=CalledProcessError))
        else:
            g.add_node(name, _safe(nodes[name]))
    g.add_edge(START, "pick_task")
    g.add_conditional_edges("pick_task", after_pick, ["preflight", END])
    for src, nxt in LINEAR.items():
        g.add_conditional_edges(src, _linear(nxt), sorted({nxt, "report"}))
    g.add_conditional_edges("verify", after_verify, ["open_pr", "implement", "report"])
    g.add_edge("report", "pick_task")
    return g.compile(checkpointer=checkpointer, interrupt_before=list(NODES) if step else None)


def initial_state(run_id: str, cfg: Config, simulated: bool) -> dict:
    return {"run_id": run_id, "simulated": simulated, "max_tasks": cfg.max_tasks_per_run,
            "quota_threshold": cfg.quota_threshold, "tasks_done": 0, "usage": []}


def graph_mermaid() -> str:
    return build_graph({n: (lambda s: {}) for n in NODES}).get_graph().draw_mermaid()
```

- [ ] **Step 4: Implementation — common.py**

`harness/nodes/common.py`:
```python
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
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_graph.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

CHANGELOG: `## [2026-10-01] - LangGraph graf a sdílené uzly pick_task/report`.
```bash
git add harness/graph.py harness/nodes/common.py tests/test_graph.py CHANGELOG.md
git commit -m "feat: state graph with routing and shared nodes"
```

---

### Task 6: Falešné uzly, scénáře, runner

**Files:**
- Create: `harness/nodes/fake.py`, `harness/simulate.py`, `harness/runner.py`, `scenarios/happy-path.yaml`, `scenarios/flaky-tests.yaml`, `scenarios/retries-exhausted.yaml`, `scenarios/unclear-task.yaml`, `scenarios/forbidden-path.yaml`, `scenarios/rate-limit-pause-resume.yaml`, `scenarios/two-tasks-priority.yaml`, `scenarios/three-tasks-flaky.yaml`
- Test: `tests/test_scenarios.py`

**Interfaces:**
- Consumes: `build_graph`, `initial_state`, `RateLimited`, `make_pick_task`, `make_report`, `events.log/events_after`, `Config`, `Project`
- Produces:
  - `harness/nodes/fake.py`: `Script(spec: dict, delay: float = 0.0)` s atributem `calls: Counter` a metodou `next(node: str) -> dict`; `fake_nodes(script: Script) -> dict[str, Callable]` (všechny uzly kromě `pick_task`/`report`).
  - `harness/simulate.py`: `Simulation` (dataclass: `app`, `config: dict`, `run_id: str`, `inputs: dict`, `script: Script`, `backlog_path: Path`, `lock: threading.Lock`), `load_scenario(path: Path) -> dict`, `prepare(scenario_path: Path, workdir: Path, checkpointer, step: bool = False, delay: float | None = None) -> Simulation`.
  - `harness/runner.py`: `execute(app, inputs: dict | None, config: dict, conn, simulated: bool) -> str` vrací `"done" | "paused" | "interrupted"`; `run_config(run_id: str) -> dict`.

- [ ] **Step 1: Scénáře**

`scenarios/happy-path.yaml`:
```yaml
name: Vše projde napoprvé
tasks:
  - {project: demo, id: sim-01, title: "Demo úkol", priority: P1}
nodes: {}
delay: 0.8
```

`scenarios/flaky-tests.yaml`:
```yaml
name: Testy dvakrát spadnou, potřetí projdou
tasks:
  - {project: demo, id: sim-01, title: "Demo úkol", priority: P1}
nodes:
  verify: [{fail: "2 tests failed"}, {fail: "1 test failed"}, pass]
delay: 0.8
```

`scenarios/retries-exhausted.yaml`:
```yaml
name: Verify selže ve všech pokusech
tasks:
  - {project: demo, id: sim-01, title: "Demo úkol", priority: P1}
nodes:
  verify: [{fail: "tsc: 3 type errors"}]
delay: 0.8
```

`scenarios/unclear-task.yaml`:
```yaml
name: Plánovač označí zadání za nejasné
tasks:
  - {project: demo, id: sim-01, title: "Zlepšit výkon", priority: P1}
nodes:
  plan: {unclear: "Co přesně měřit?"}
delay: 0.8
```

`scenarios/forbidden-path.yaml`:
```yaml
name: Worker sáhne na zakázanou cestu
tasks:
  - {project: demo, id: sim-01, title: "Úprava bota", priority: P1}
nodes:
  verify: [{guard: "forbidden paths: Thoughts/2026-10-01.md"}]
delay: 0.8
```

`scenarios/rate-limit-pause-resume.yaml`:
```yaml
name: Rate limit během implementace, pak resume
tasks:
  - {project: demo, id: sim-01, title: "Demo úkol", priority: P1}
nodes:
  implement: [{rate_limit: 60}, ok]
delay: 0.8
```

`scenarios/two-tasks-priority.yaml`:
```yaml
name: Dva úkoly, P1 jde první
tasks:
  - {project: demo, id: sim-01, title: "Méně důležitý", priority: P2}
  - {project: demo, id: sim-02, title: "Důležitý", priority: P1}
nodes: {}
delay: 0.5
```

`scenarios/three-tasks-flaky.yaml`:
```yaml
name: Tři úkoly s opakováním, limit dvou na běh
max_tasks: 2
tasks:
  - {project: demo, id: sim-01, title: "A", priority: P1}
  - {project: demo, id: sim-02, title: "B", priority: P1}
  - {project: demo, id: sim-03, title: "C", priority: P1}
nodes:
  verify: [{fail: "x"}, {fail: "y"}, pass, {fail: "z"}, {fail: "w"}, pass]
delay: 0.3
```

- [ ] **Step 2: Failing test**

`tests/test_scenarios.py`:
```python
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from harness import events
from harness.backlog import parse
from harness.runner import execute
from harness.simulate import prepare

SCENARIOS = Path(__file__).parent.parent / "scenarios"


def run(name, tmp_path):
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(SCENARIOS / f"{name}.yaml", tmp_path, InMemorySaver(), delay=0)
    status = execute(sim.app, sim.inputs, sim.config, conn, simulated=True)
    return sim, conn, status


def statuses(sim):
    return {t.id: t.status for t in parse(sim.backlog_path.read_bytes().decode())}


def started(conn, sim):
    return [e["node"] for e in events.events_after(conn, 0, sim.run_id) if e["kind"] == "start"]


def test_happy_path(tmp_path):
    sim, conn, status = run("happy-path", tmp_path)
    assert status == "done"
    assert statuses(sim) == {"sim-01": "x"}
    assert started(conn, sim) == ["pick_task", "preflight", "make_worktree", "plan", "setup",
                                  "implement", "verify", "open_pr", "report", "pick_task"]
    assert "https://example.invalid/sim/sim-01" in sim.backlog_path.read_text(encoding="utf-8")


def test_flaky_tests(tmp_path):
    sim, conn, status = run("flaky-tests", tmp_path)
    assert statuses(sim) == {"sim-01": "x"}
    assert sim.script.calls["implement"] == 3 and sim.script.calls["verify"] == 3


def test_retries_exhausted(tmp_path):
    sim, conn, _ = run("retries-exhausted", tmp_path)
    assert statuses(sim) == {"sim-01": "!"}
    assert sim.script.calls["open_pr"] == 0
    assert "tsc: 3 type errors" in sim.backlog_path.read_text(encoding="utf-8")


def test_unclear_task(tmp_path):
    sim, conn, _ = run("unclear-task", tmp_path)
    assert statuses(sim) == {"sim-01": "?"}
    assert sim.script.calls["setup"] == 0
    assert "UNCLEAR: Co přesně měřit?" in sim.backlog_path.read_text(encoding="utf-8")


def test_forbidden_path(tmp_path):
    sim, conn, _ = run("forbidden-path", tmp_path)
    assert statuses(sim) == {"sim-01": "!"}
    assert "forbidden paths" in sim.backlog_path.read_text(encoding="utf-8")


def test_rate_limit_pause_and_resume(tmp_path):
    sim, conn, status = run("rate-limit-pause-resume", tmp_path)
    assert status == "paused"
    assert statuses(sim) == {"sim-01": " "}
    assert [e["kind"] for e in events.events_after(conn, 0, sim.run_id)][-1] == "paused"
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "done"
    assert statuses(sim) == {"sim-01": "x"}
    assert sim.script.calls["make_worktree"] == 1
    assert sim.script.calls["implement"] == 2


def test_two_tasks_priority(tmp_path):
    sim, conn, _ = run("two-tasks-priority", tmp_path)
    assert statuses(sim) == {"sim-01": "x", "sim-02": "x"}
    reports = [e for e in events.events_after(conn, 0, sim.run_id) if e["node"] == "pick_task" and e["kind"] == "end"]
    assert reports[0]["delta"]["task"]["id"] == "sim-02"


def test_three_tasks_flaky_respects_max_tasks(tmp_path):
    sim, conn, status = run("three-tasks-flaky", tmp_path)
    assert status == "done"
    assert statuses(sim) == {"sim-01": "x", "sim-02": "x", "sim-03": " "}


def test_step_mode(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(SCENARIOS / "happy-path.yaml", tmp_path, InMemorySaver(), step=True, delay=0)
    assert execute(sim.app, sim.inputs, sim.config, conn, simulated=True) == "interrupted"
    assert sim.app.get_state(sim.config).next == ("pick_task",)
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "interrupted"
    assert sim.app.get_state(sim.config).next == ("preflight",)


def test_unknown_node_value_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: x\ntasks: [{project: demo, id: a-1, title: t, priority: P1}]\nnodes: {verify: [banana]}\n", encoding="utf-8")
    conn = events.connect(tmp_path / "h.db")
    sim = prepare(p, tmp_path, InMemorySaver(), delay=0)
    execute(sim.app, sim.inputs, sim.config, conn, simulated=True)
    assert "unknown scenario value" in sim.backlog_path.read_text(encoding="utf-8")
```

- [ ] **Step 3: Run to verify fail**

Run: `.venv/Scripts/python -m pytest tests/test_scenarios.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'harness.runner'`

- [ ] **Step 4: Implementation — fake.py**

`harness/nodes/fake.py`:
```python
import time
from collections import Counter

from ..graph import RateLimited


class Script:
    def __init__(self, spec: dict, delay: float = 0.0):
        self.spec, self.delay, self.calls = spec, delay, Counter()

    def next(self, node: str) -> dict:
        i = self.calls[node]
        self.calls[node] += 1
        if self.delay:
            time.sleep(self.delay)
        value = self.spec.get(node, "ok")
        if isinstance(value, list):
            value = value[min(i, len(value) - 1)]
        if value in ("ok", "pass"):
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"unknown scenario value for {node}: {value!r}")
        if "rate_limit" in value:
            raise RateLimited(int(time.time()) + int(value["rate_limit"]))
        if "error" in value:
            raise RuntimeError(value["error"])
        return value


def _usage(node: str, model: str) -> list[dict]:
    return [{"node": node, "model": model, "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.0}]


def _failed(value: dict) -> dict:
    return {"outcome": "failed", "last_failure": value.get("fail", str(value))}


def fake_nodes(script: Script) -> dict:
    def preflight(s):
        v = script.next("preflight")
        return _failed(v) if v else {"branch": f"harness/{s['task']['id']}"}

    def make_worktree(s):
        v = script.next("make_worktree")
        return _failed(v) if v else {"worktree": f"/sim/{s['task']['id']}"}

    def plan(s):
        v = script.next("plan")
        if "unclear" in v:
            return {"outcome": "needs_clarification", "last_failure": v["unclear"], "usage": _usage("plan", "opus")}
        return _failed(v) if v else {"plan": "1. Simulovaný krok", "usage": _usage("plan", "opus")}

    def setup(s):
        v = script.next("setup")
        return _failed(v) if v else {"baseline_failures": []}

    def implement(s):
        v = script.next("implement")
        return _failed(v) if v else {"usage": _usage("implement", "sonnet")}

    def verify(s):
        v = script.next("verify")
        attempts = s.get("attempts", 0) + 1
        if not v:
            return {"verify_ok": True, "last_failure": "", "attempts": attempts}
        msg = v.get("fail") or f"guard: {v.get('guard')}"
        return {"verify_ok": False, "last_failure": msg, "attempts": attempts}

    def open_pr(s):
        v = script.next("open_pr")
        return _failed(v) if v else {"outcome": "pr_opened", "pr_url": f"https://example.invalid/sim/{s['task']['id']}"}

    return {"preflight": preflight, "make_worktree": make_worktree, "plan": plan, "setup": setup,
            "implement": implement, "verify": verify, "open_pr": open_pr}
```

- [ ] **Step 5: Implementation — runner.py**

`harness/runner.py`:
```python
from . import events
from .graph import RateLimited


def run_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 200}


def _log_debug(conn, run_id: str, ev: dict, simulated: bool) -> None:
    payload = ev["payload"]
    if ev["type"] == "task":
        events.log(conn, run_id, "start", node=payload["name"], simulated=simulated)
    elif ev["type"] == "task_result":
        if payload.get("error"):
            events.log(conn, run_id, "error", node=payload["name"], delta={"error": str(payload["error"])}, simulated=simulated)
            return
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        events.log(conn, run_id, "end", node=payload["name"],
                   delta={k: v for k, v in result.items() if k not in ("usage", "rate_limit")},
                   usage=result.get("usage") or None, rate_limit=result.get("rate_limit"), simulated=simulated)


def execute(app, inputs, config: dict, conn, simulated: bool) -> str:
    run_id = config["configurable"]["thread_id"]
    if inputs is not None:
        events.log(conn, run_id, "run_start", simulated=simulated)
    try:
        for _, ev in app.stream(inputs, config, stream_mode=["debug"]):
            _log_debug(conn, run_id, ev, simulated)
    except RateLimited as e:
        events.log(conn, run_id, "paused", rate_limit={"resets_at": e.resets_at}, simulated=simulated)
        return "paused"
    if app.get_state(config).next:
        return "interrupted"
    events.log(conn, run_id, "run_end", simulated=simulated)
    return "done"
```

- [ ] **Step 6: Implementation — simulate.py**

`harness/simulate.py`:
```python
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .graph import build_graph, initial_state
from .nodes.common import make_pick_task, make_report
from .nodes.fake import Script, fake_nodes
from .projects import Config, Project
from .runner import run_config


@dataclass
class Simulation:
    app: object
    config: dict
    run_id: str
    inputs: dict
    script: Script
    backlog_path: Path
    lock: threading.Lock = field(default_factory=threading.Lock)


def load_scenario(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _render_backlog(tasks: list[dict]) -> str:
    out: dict[str, list[str]] = {}
    for t in tasks:
        out.setdefault(t["project"], []).append(f"- [ ] P{str(t['priority'])[-1]} {t['id']} {t['title']}")
    return "".join(f"## {p}\n" + "\n".join(lines) + "\n\n" for p, lines in out.items())


def prepare(scenario_path: Path, workdir: Path, checkpointer, step: bool = False, delay: float | None = None) -> Simulation:
    sc = load_scenario(scenario_path)
    workdir.mkdir(parents=True, exist_ok=True)
    backlog_path = workdir / "BACKLOG.md"
    backlog_path.write_bytes(_render_backlog(sc["tasks"]).encode("utf-8"))
    projects = {t["project"]: Project(t["project"], workdir, "main", (), ("true",), (),
                                      sc.get("max_attempts", 3), 800) for t in sc["tasks"]}
    cfg = Config("subscription", sc.get("max_tasks", 3), 0.7, projects)
    script = Script(sc.get("nodes") or {}, sc.get("delay", 0) if delay is None else delay)
    nodes = fake_nodes(script) | {"pick_task": make_pick_task(backlog_path, cfg), "report": make_report(backlog_path)}
    run_id = f"sim-{Path(scenario_path).stem}-{int(time.time() * 1000)}"
    app = build_graph(nodes, checkpointer, step)
    return Simulation(app, run_config(run_id), run_id, initial_state(run_id, cfg, True), script, backlog_path)
```

- [ ] **Step 7: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_scenarios.py -v`
Expected: 10 passed. Pokud `test_two_tasks_priority` selže na `delta["task"]`, vypiš `events.events_after` a ověř tvar `task_result.payload.result`. Spike ukázal `dict` s výstupem uzlu.

- [ ] **Step 8: Full suite + commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: všechno zelené.

CHANGELOG: `## [2026-10-01] - Simulace: falešné uzly, 8 scénářů, runner s event logem`.
```bash
git add harness/nodes/fake.py harness/simulate.py harness/runner.py scenarios tests/test_scenarios.py CHANGELOG.md
git commit -m "feat: scenario-driven simulation and event-logging runner"
```

---

### Task 7: CLI (simulate)

**Files:**
- Create: `harness/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `prepare`, `execute`, `events.connect`
- Produces: `main(argv: list[str] | None = None) -> int`; `open_db(home: Path) -> tuple[sqlite3.Connection, SqliteSaver]`; subcommands `simulate <scenario> [--step] [--delay S]`, `run [--resume RUN_ID]`, `dashboard [--port N]`, `prune`. Globální volba `--home DIR` (výchozí `Path.cwd()`), ve které jsou `projects.yaml`, `BACKLOG.md`, `harness.db`, `scenarios/`. `run`, `dashboard` a `prune` se doplní v Tasks 9–10; tady vyhodí `SystemExit("not implemented yet")`.

- [ ] **Step 1: Failing test**

`tests/test_cli.py`:
```python
import shutil
from pathlib import Path

from harness import events
from harness.cli import main

ROOT = Path(__file__).parent.parent


def test_simulate_by_name(tmp_path, capsys):
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    assert main(["--home", str(tmp_path), "simulate", "happy-path", "--delay", "0"]) == 0
    out = capsys.readouterr().out
    assert "done" in out and "[x]" in out
    conn = events.connect(tmp_path / "harness.db")
    [run] = events.runs(conn)
    assert run["simulated"] and run["last_kind"] == "run_end"


def test_simulate_unknown(tmp_path):
    (tmp_path / "scenarios").mkdir()
    assert main(["--home", str(tmp_path), "simulate", "nope"]) == 2
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'harness.cli'`

- [ ] **Step 3: Implementation**

`harness/cli.py`:
```python
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
```

- [ ] **Step 4: Run tests + ruční ověření**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: 2 passed

Run: `.venv/Scripts/harness simulate flaky-tests --delay 0`
Expected: vypíše `run sim-flaky-tests-…`, `done` a BACKLOG s `- [x] P1 sim-01 Demo úkol → https://example.invalid/sim/sim-01`.

- [ ] **Step 5: Commit**

CHANGELOG: `## [2026-10-01] - CLI: harness simulate (plynulý i krokovací režim)`.
```bash
git add harness/cli.py tests/test_cli.py CHANGELOG.md
git commit -m "feat: simulate CLI with step mode"
```

---

### Task 8: SDK wrapper, prompty, izolační spike

**Files:**
- Create: `harness/sdk.py`, `harness/prompts/plan.md`, `harness/prompts/implement.md`, `scripts/check_isolation.py`
- Test: `tests/test_sdk.py`

**Interfaces:**
- Consumes: `RateLimited`
- Produces: `WorkerResult(text: str, usage: dict, quota: float | None, rate_limit: dict | None)`, `run_agent(prompt: str, *, cwd: Path | str, model: str, tools: list[str], disallowed: list[str] = (), permission_mode: str = "acceptEdits", max_turns: int = 60) -> WorkerResult`, `check_billing(billing: str, env: Mapping[str, str]) -> str | None`, `render_prompt(name: str, **fields) -> str`, konstanty `READ_TOOLS`, `WRITE_TOOLS`, `NO_PUBLISH`, `WORKER_APPEND`. `usage` má klíče `model, input_tokens, output_tokens, cost_usd`.

- [ ] **Step 1: Failing test**

`tests/test_sdk.py`:
```python
from harness.sdk import check_billing, render_prompt


def test_subscription_rejects_api_key():
    assert "ANTHROPIC_API_KEY" in check_billing("subscription", {"ANTHROPIC_API_KEY": "sk-x"})


def test_subscription_ok_without_key():
    assert check_billing("subscription", {"PATH": "x"}) is None


def test_api_requires_key():
    assert check_billing("api", {}) == "billing=api but ANTHROPIC_API_KEY is not set"
    assert check_billing("api", {"ANTHROPIC_API_KEY": "sk"}) is None


def test_render_prompt_with_braces_in_values():
    text = render_prompt("implement.md", title="T", description="D", plan="P",
                         failure="Previous attempt failed:\n{weird} output")
    assert "T" in text and "{weird} output" in text


def test_render_plan_prompt():
    text = render_prompt("plan.md", title="Lazy loading", description="Popis")
    assert "Lazy loading" in text and "UNCLEAR:" in text
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python -m pytest tests/test_sdk.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'harness.sdk'`

- [ ] **Step 3: Prompty**

`harness/prompts/plan.md`:
```markdown
You are the planner in an automated coding harness. The current directory is a fresh git worktree of the project.

Task: {title}
{description}

Explore the relevant code (read-only). Then write a concise implementation plan: which files change, the steps, and how to verify the result with the project's existing tests.

If the task is too unclear to plan responsibly, answer with ONLY one line and nothing else:
UNCLEAR: <one concrete question for the owner>
```

`harness/prompts/implement.md`:
```markdown
You are implementing one task in this git worktree.

Task: {title}
{description}

Plan:
{plan}

{failure}

Rules:
- Make the smallest change that satisfies the task. Follow the existing code style.
- Run the project's tests yourself before finishing.
- Do not commit, push, or open pull requests. The harness does that.
- Finish with a 2-3 sentence summary of what you changed.
```

- [ ] **Step 4: Implementation**

`harness/sdk.py`:
```python
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, RateLimitEvent, ResultMessage, TextBlock, query

from .graph import RateLimited

PROMPTS = Path(__file__).parent / "prompts"
READ_TOOLS = ["Read", "Glob", "Grep"]
WRITE_TOOLS = READ_TOOLS + ["Edit", "Write", "Bash"]
NO_PUBLISH = ["Bash(git push*)", "Bash(git commit*)", "Bash(gh *)"]
WORKER_APPEND = "You are a worker in an automated harness. Work only inside the current directory."


@dataclass
class WorkerResult:
    text: str
    usage: dict
    quota: float | None
    rate_limit: dict | None


def check_billing(billing: str, env: Mapping[str, str]) -> str | None:
    has_key = bool(env.get("ANTHROPIC_API_KEY"))
    if billing == "subscription" and has_key:
        return "billing=subscription but ANTHROPIC_API_KEY is set (would bill the API) — unset it"
    if billing == "api" and not has_key:
        return "billing=api but ANTHROPIC_API_KEY is not set"
    return None


def render_prompt(name: str, **fields) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").format(**fields)


def run_agent(prompt: str, *, cwd, model: str, tools: list[str], disallowed=(),
              permission_mode: str = "acceptEdits", max_turns: int = 60) -> WorkerResult:
    options = ClaudeAgentOptions(
        cwd=str(cwd), model=model, setting_sources=["project"],
        system_prompt={"type": "preset", "preset": "claude_code", "append": WORKER_APPEND},
        allowed_tools=list(tools), disallowed_tools=list(disallowed),
        permission_mode=permission_mode, max_turns=max_turns,
    )
    return asyncio.run(_collect(prompt, options, model))


def _rate_limit(info) -> dict:
    return {"type": info.rate_limit_type, "status": info.status,
            "utilization": info.utilization, "resets_at": info.resets_at}


async def _collect(prompt: str, options: ClaudeAgentOptions, model: str) -> WorkerResult:
    texts: list[str] = []
    result, rate_limit = None, None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, RateLimitEvent):
            info = msg.rate_limit_info
            if info.rate_limit_type == "five_hour" or rate_limit is None:
                rate_limit = _rate_limit(info)
            if info.status == "rejected":
                raise RateLimited(info.resets_at)
        elif isinstance(msg, AssistantMessage):
            texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
        elif isinstance(msg, ResultMessage):
            result = msg
    if result is None:
        raise RuntimeError("agent produced no result message")
    if result.api_error_status == 429:
        raise RateLimited(rate_limit and rate_limit["resets_at"])
    if result.is_error:
        raise RuntimeError(f"agent error: {result.subtype} {result.errors or ''}")
    u = result.usage or {}
    usage = {"model": model, "input_tokens": u.get("input_tokens", 0),
             "output_tokens": u.get("output_tokens", 0), "cost_usd": result.total_cost_usd}
    text = result.result or (texts[-1] if texts else "")
    return WorkerResult(text, usage, rate_limit and rate_limit["utilization"], rate_limit)
```

- [ ] **Step 5: Run unit tests**

Run: `.venv/Scripts/python -m pytest tests/test_sdk.py -v`
Expected: 5 passed

- [ ] **Step 6: Izolační spike (živé volání, kritérium č. 5)**

`scripts/check_isolation.py`:
```python
"""Ověří, že worker se setting_sources=["project"] nevidí globální pluginy/skills z ~/.claude.
Spouští jeden krátký dotaz (haiku) přes subscription."""
import asyncio
import json
import os
import sys
import tempfile

from claude_agent_sdk import ClaudeAgentOptions, SystemMessage, query

FORBIDDEN = ("caveman", "ponytail", "superpowers", "impeccable")


async def main() -> int:
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is set — unset it to test subscription", file=sys.stderr)
        return 2
    opts = ClaudeAgentOptions(cwd=tempfile.mkdtemp(), model="haiku", setting_sources=["project"],
                              allowed_tools=["Read"], permission_mode="dontAsk", max_turns=1)
    init = None
    async for msg in query(prompt="Reply with the single word: ok", options=opts):
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            init = msg.data
    dump = json.dumps(init, ensure_ascii=False).lower()
    print(json.dumps(init, indent=2, ensure_ascii=False)[:3000])
    leaked = [f for f in FORBIDDEN if f in dump]
    print("LEAKED:" if leaked else "ISOLATED", leaked)
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

Run: `.venv/Scripts/python scripts/check_isolation.py`
Expected: `ISOLATED []`, exit 0. Init data obsahuje `apiKeySource` nebo podobné pole, které **nesmí** být `ANTHROPIC_API_KEY`. Zapiš do CHANGELOGu, co ukazuje.
- Pokud `CLINotFoundError`: najdi `claude` (`where claude`) a do `ClaudeAgentOptions` v `sdk.py` i ve skriptu doplň `cli_path=shutil.which("claude")`.
- Pokud `LEAKED`: zkus `setting_sources=[]` + `plugins=[]` a znovu spusť. Když i tak unikají, zastav se a nahlas uživateli; je to rozhodnutí pro spec, ne pro tento plán.

- [ ] **Step 7: Commit**

CHANGELOG: `## [2026-10-01] - Agent SDK wrapper (subscription, rate limit), prompty, izolační spike`.
```bash
git add harness/sdk.py harness/prompts scripts tests/test_sdk.py CHANGELOG.md
git commit -m "feat: agent sdk wrapper with rate-limit handling and prompts"
```

---

### Task 9: Skutečné uzly + `harness run` + `prune`

**Files:**
- Create: `harness/nodes/real.py`
- Modify: `harness/cli.py` (nahradit `_todo` pro `run` a `prune`)
- Test: `tests/test_real_git.py`

**Interfaces:**
- Consumes: `run_agent`, `render_prompt`, `check_billing`, `READ_TOOLS`, `WRITE_TOOLS`, `NO_PUBLISH`, `check_diff`, `new_failures`, `Config`, `Project`, `make_pick_task`, `make_report`, `build_graph`, `initial_state`, `run_config`, `execute`, `open_db`
- Produces: `real_nodes(cfg: Config) -> dict[str, Callable]` (uzly kromě `pick_task`/`report`), `sh(cmd: str, cwd) -> tuple[bool, str]`, `git(args: list[str], cwd) -> str`, `worktree_path(project: Project, task_id: str) -> Path`, `ensure_worktree(project: Project, task_id: str, branch: str) -> Path`, `commit_if_dirty(worktree, message: str) -> bool`, `numstat_lines(text: str) -> int`

- [ ] **Step 1: Failing test (lokální git repa, bez sítě a bez modelu)**

`tests/test_real_git.py`:
```python
import subprocess
from pathlib import Path

from harness.nodes.real import commit_if_dirty, ensure_worktree, git, numstat_lines, worktree_path
from harness.projects import Project


def make_repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "projects" / "demo"
    repo.mkdir(parents=True)
    git(["init", "-q", "-b", "main"], repo)
    git(["config", "user.email", "t@t"], repo)
    git(["config", "user.name", "t"], repo)
    (repo / "a.txt").write_text("a\n")
    git(["add", "-A"], repo)
    git(["commit", "-q", "-m", "init"], repo)
    git(["remote", "add", "origin", str(remote)], repo)
    git(["push", "-q", "origin", "main"], repo)
    return repo


def project(repo: Path) -> Project:
    return Project("demo", repo, "main", (), ("true",), (), 3, 800)


def test_worktree_created_from_origin_and_reused(tmp_path):
    repo = make_repo(tmp_path)
    git(["checkout", "-q", "-b", "user-wip"], repo)
    (repo / "wip.txt").write_text("user work\n")
    p = project(repo)
    wt = ensure_worktree(p, "t-1", "harness/t-1")
    assert wt == worktree_path(p, "t-1") == tmp_path / "projects" / ".harness-worktrees" / "demo" / "t-1"
    assert (wt / "a.txt").exists() and not (wt / "wip.txt").exists()
    assert git(["branch", "--show-current"], wt) == "harness/t-1"
    assert ensure_worktree(p, "t-1", "harness/t-1") == wt
    assert git(["branch", "--show-current"], repo) == "user-wip"


def test_commit_if_dirty_is_idempotent(tmp_path):
    repo = make_repo(tmp_path)
    wt = ensure_worktree(project(repo), "t-2", "harness/t-2")
    git(["config", "user.email", "t@t"], wt)
    git(["config", "user.name", "t"], wt)
    (wt / "b.txt").write_text("b\n")
    assert commit_if_dirty(wt, "harness: t-2") is True
    assert commit_if_dirty(wt, "harness: t-2") is False
    assert git(["log", "-1", "--format=%s"], wt) == "harness: t-2"


def test_numstat_lines_skips_binary():
    assert numstat_lines("3\t1\ta.py\n-\t-\timg.png\n10\t0\tb.py\n") == 14
    assert numstat_lines("") == 0
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python -m pytest tests/test_real_git.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'harness.nodes.real'`

- [ ] **Step 3: Implementation — real.py**

`harness/nodes/real.py`:
```python
import os
import subprocess
from pathlib import Path

from ..guard import check_diff, new_failures
from ..projects import Config, Project
from ..sdk import NO_PUBLISH, READ_TOOLS, WRITE_TOOLS, check_billing, render_prompt, run_agent


def sh(cmd: str, cwd, timeout: int = 900) -> tuple[bool, str]:
    p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode == 0, p.stdout + p.stderr


def git(args: list[str], cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def worktree_path(project: Project, task_id: str) -> Path:
    return project.path.parent / ".harness-worktrees" / project.name / task_id


def ensure_worktree(project: Project, task_id: str, branch: str) -> Path:
    path = worktree_path(project, task_id)
    if not path.exists():
        git(["fetch", "-q", "origin", project.base], project.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        git(["worktree", "add", "-q", "-B", branch, str(path), f"origin/{project.base}"], project.path)
    return path


def commit_if_dirty(worktree, message: str) -> bool:
    git(["add", "-A"], worktree)
    if not git(["diff", "--cached", "--name-only"], worktree):
        return False
    git(["commit", "-q", "-m", message], worktree)
    return True


def numstat_lines(text: str) -> int:
    total = 0
    for line in text.splitlines():
        added, deleted, _ = line.split("\t", 2)
        if added != "-":
            total += int(added) + int(deleted)
    return total


def _failed(msg: str) -> dict:
    return {"outcome": "failed", "last_failure": msg}


def _agent_out(node: str, r) -> dict:
    return {"usage": [{"node": node, **r.usage}], "quota": r.quota, "rate_limit": r.rate_limit}


def _pr_body(s: dict) -> str:
    usage = s.get("usage") or []
    tokens = sum((u.get("input_tokens") or 0) + (u.get("output_tokens") or 0) for u in usage)
    baseline = ", ".join(s.get("baseline_failures") or []) or "none"
    return (f"Automated by harness run `{s['run_id']}`.\n\n## Plan\n\n{s.get('plan', '')}\n\n"
            f"## Verify\n\nPassed after {s.get('attempts', 0)} attempt(s). Commands failing on base: {baseline}.\n\n"
            f"Tokens (whole run so far): {tokens}")


def real_nodes(cfg: Config) -> dict:
    def proj(s) -> Project:
        return cfg.projects[s["task"]["project"]]

    def preflight(s):
        if err := check_billing(cfg.billing, os.environ):
            return _failed(err)
        ok, out = sh("gh auth status", proj(s).path)
        if not ok:
            return _failed(f"gh not authenticated: {out[-300:]}")
        branch = f"harness/{s['task']['id']}"
        if git(["ls-remote", "--heads", "origin", branch], proj(s).path):
            return _failed(f"branch {branch} already exists on origin")
        return {"branch": branch}

    def make_worktree(s):
        return {"worktree": str(ensure_worktree(proj(s), s["task"]["id"], s["branch"]))}

    def plan(s):
        t = s["task"]
        r = run_agent(render_prompt("plan.md", title=t["title"], description=t["description"]),
                      cwd=s["worktree"], model="opus", tools=READ_TOOLS, permission_mode="dontAsk", max_turns=30)
        out = _agent_out("plan", r)
        first = r.text.strip().splitlines()[0] if r.text.strip() else ""
        if first.startswith("UNCLEAR:"):
            return out | {"outcome": "needs_clarification", "last_failure": first.removeprefix("UNCLEAR:").strip()}
        return out | {"plan": r.text}

    def setup(s):
        p, wt = proj(s), s["worktree"]
        for cmd in p.setup:
            ok, out = sh(cmd, wt)
            if not ok:
                return _failed(f"setup `{cmd}` failed: {out[-1500:]}")
        return {"baseline_failures": [c for c in p.verify if not sh(c, wt)[0]]}

    def implement(s):
        t = s["task"]
        failure = (f"The previous attempt failed verification. Fix this:\n```\n{s['last_failure']}\n```"
                   if s.get("last_failure") else "")
        prompt = render_prompt("implement.md", title=t["title"], description=t["description"],
                               plan=s.get("plan", ""), failure=failure)
        r = run_agent(prompt, cwd=s["worktree"], model="sonnet", tools=WRITE_TOOLS, disallowed=NO_PUBLISH, max_turns=80)
        return _agent_out("implement", r)

    def verify(s):
        p, wt = proj(s), s["worktree"]
        results = [(cmd, *sh(cmd, wt)) for cmd in p.verify]
        new = new_failures(s.get("baseline_failures") or [], [c for c, ok, _ in results if not ok])
        git(["add", "-A"], wt)
        changed = git(["diff", "--cached", "--name-only"], wt).splitlines()
        lines = numstat_lines(git(["diff", "--cached", "--numstat"], wt))
        failures = [f"$ {c}\n{out[-1500:]}" for c, ok, out in results if c in new]
        if violation := check_diff(changed, lines, p.allowed, p.max_diff_lines):
            failures.insert(0, f"guard: {violation}")
        return {"verify_ok": not failures, "last_failure": "\n\n".join(failures)[-4000:],
                "attempts": s.get("attempts", 0) + 1}

    def open_pr(s):
        p, wt, t = proj(s), s["worktree"], s["task"]
        commit_if_dirty(wt, f"harness: {t['id']} {t['title']}")
        git(["push", "-q", "-u", "origin", s["branch"]], wt)
        existing = subprocess.run(["gh", "pr", "view", s["branch"], "--json", "url", "-q", ".url"],
                                  cwd=wt, capture_output=True, text=True, encoding="utf-8").stdout.strip()
        if existing.startswith("http"):
            return {"outcome": "pr_opened", "pr_url": existing}
        out = subprocess.run(["gh", "pr", "create", "--draft", "--base", p.base, "--head", s["branch"],
                              "--title", f"[harness] {t['title']}", "--body", _pr_body(s)],
                             cwd=wt, check=True, capture_output=True, text=True, encoding="utf-8").stdout
        return {"outcome": "pr_opened", "pr_url": out.strip().splitlines()[-1]}

    return {"preflight": preflight, "make_worktree": make_worktree, "plan": plan, "setup": setup,
            "implement": implement, "verify": verify, "open_pr": open_pr}
```

- [ ] **Step 4: Run git tests**

Run: `.venv/Scripts/python -m pytest tests/test_real_git.py -v`
Expected: 3 passed

- [ ] **Step 5: CLI `run` a `prune`**

V `harness/cli.py` přidat importy a funkce a v `handlers` nahradit `_todo` pro `run` a `prune`:
```python
import time

from . import backlog
from .graph import build_graph, initial_state
from .nodes.common import make_pick_task, make_report
from .nodes.real import git, real_nodes, worktree_path
from .projects import load_config
from .runner import run_config


def cmd_run(args) -> int:
    cfg = load_config(args.home / "projects.yaml")
    backlog_path = args.home / "BACKLOG.md"
    conn, saver = open_db(args.home)
    nodes = real_nodes(cfg) | {"pick_task": make_pick_task(backlog_path, cfg), "report": make_report(backlog_path)}
    app = build_graph(nodes, saver)
    run_id = args.resume or f"run-{time.strftime('%Y%m%d-%H%M%S')}"
    inputs = None if args.resume else initial_state(run_id, cfg, simulated=False)
    status = execute(app, inputs, run_config(run_id), conn, simulated=False)
    print(f"{run_id} {status}")
    if status == "paused":
        print(f"resume: harness run --resume {run_id}")
    return 0


def cmd_prune(args) -> int:
    cfg = load_config(args.home / "projects.yaml")
    done = {t.id for t in backlog.parse((args.home / "BACKLOG.md").read_bytes().decode("utf-8")) if t.status == "x"}
    for project in cfg.projects.values():
        for task_id in done:
            path = worktree_path(project, task_id)
            if path.exists():
                git(["worktree", "remove", "--force", str(path)], project.path)
                print(f"removed {path}")
    return 0
```
`handlers = {"simulate": cmd_simulate, "run": cmd_run, "dashboard": _todo, "prune": cmd_prune}`

- [ ] **Step 6: Full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: všechno zelené.

- [ ] **Step 7: Commit**

CHANGELOG: `## [2026-10-01] - Skutečné uzly (worktree, Agent SDK, verify, draft PR), harness run/prune`.
```bash
git add harness/nodes/real.py harness/cli.py tests/test_real_git.py CHANGELOG.md
git commit -m "feat: real nodes, run and prune commands"
```

---

### Task 10: Dashboard API

**Files:**
- Create: `harness/dashboard/app.py`, `harness/dashboard/static/index.html` (zatím jen `<!doctype html><title>harness</title>`, plnou verzi doplní Task 11)
- Modify: `harness/cli.py` (handler `dashboard`)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `events.*`, `graph_mermaid`, `prepare`, `Simulation`, `execute`, `backlog.parse`, `open_db`
- Produces: `create_app(home: Path) -> FastAPI`. Endpointy: `GET /`, `GET /graph` → `{"mermaid": str}`, `GET /runs`, `GET /runs/{run_id}/events?after=0`, `GET /runs/{run_id}/state` → `{"next": [..], "simulated": bool}` (jen simulace, jinak 404), `GET /events/stream?after=0` (SSE), `GET /backlog?sim=<run_id>`, `GET /usage` → `{"summary": [...], "rate_limit": dict | None}`, `GET /scenarios` → `[{"name", "title"}]`, `POST /simulate` body `{"scenario": str, "step": bool, "delay": float | None}` → `{"run_id": str}`, `POST /runs/{run_id}/step` → `{"ok": true}` (409 když krok běží).

- [ ] **Step 1: Failing test**

`tests/test_dashboard.py`:
```python
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.dashboard.app import create_app

ROOT = Path(__file__).parent.parent


@pytest.fixture
def client(tmp_path):
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    return TestClient(create_app(tmp_path))


def wait_for(client, run_id, kind, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        kinds = [e["kind"] for e in client.get(f"/runs/{run_id}/events").json()]
        if kind in kinds:
            return kinds
        time.sleep(0.05)
    raise AssertionError(f"{kind} not reached: {kinds}")


def test_graph(client):
    assert "verify" in client.get("/graph").json()["mermaid"]


def test_scenarios_listed(client):
    names = {s["name"] for s in client.get("/scenarios").json()}
    assert {"happy-path", "flaky-tests", "rate-limit-pause-resume"} <= names


def test_simulate_runs_to_end(client):
    run_id = client.post("/simulate", json={"scenario": "happy-path", "delay": 0}).json()["run_id"]
    wait_for(client, run_id, "run_end")
    assert any(r["run_id"] == run_id for r in client.get("/runs").json())
    assert client.get(f"/backlog?sim={run_id}").json()[0]["status"] == "x"
    assert client.get("/usage").json()["summary"]


def test_unknown_or_traversal_scenario_404(client):
    assert client.post("/simulate", json={"scenario": "../../etc/passwd"}).status_code == 404
    assert client.post("/simulate", json={"scenario": "nope"}).status_code == 404


def test_step_mode(client):
    run_id = client.post("/simulate", json={"scenario": "happy-path", "step": True, "delay": 0}).json()["run_id"]
    deadline = time.time() + 5
    while client.get(f"/runs/{run_id}/state").json()["next"] != ["pick_task"]:
        assert time.time() < deadline
        time.sleep(0.05)
    assert client.post(f"/runs/{run_id}/step").json() == {"ok": True}
    deadline = time.time() + 5
    while client.get(f"/runs/{run_id}/state").json()["next"] != ["preflight"]:
        assert time.time() < deadline
        time.sleep(0.05)


def test_state_unknown_run_404(client):
    assert client.get("/runs/nope/state").status_code == 404
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'harness.dashboard.app'`

- [ ] **Step 3: Implementation**

`harness/dashboard/app.py`:
```python
import asyncio
import json
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import backlog, events
from ..graph import graph_mermaid
from ..runner import execute
from ..simulate import Simulation, prepare

STATIC = Path(__file__).parent / "static"


class SimulateRequest(BaseModel):
    scenario: str
    step: bool = False
    delay: float | None = None


def create_app(home: Path) -> FastAPI:
    from ..cli import open_db

    app = FastAPI(title="harness")
    conn, saver = open_db(home)
    sims: dict[str, Simulation] = {}
    mermaid = graph_mermaid()

    def scenarios() -> dict[str, Path]:
        return {p.stem: p for p in sorted((home / "scenarios").glob("*.yaml"))}

    def run_sim(sim: Simulation, inputs) -> None:
        with sim.lock:
            execute(sim.app, inputs, sim.config, conn, simulated=True)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/graph")
    def graph():
        return {"mermaid": mermaid}

    @app.get("/runs")
    def runs():
        return events.runs(conn)

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, after: int = 0):
        return events.events_after(conn, after, run_id)

    @app.get("/runs/{run_id}/state")
    def run_state(run_id: str):
        sim = sims.get(run_id)
        if sim is None:
            raise HTTPException(404, "only simulations started from this dashboard have live state")
        return {"next": list(sim.app.get_state(sim.config).next), "simulated": True}

    @app.get("/events/stream")
    async def stream(after: int = 0):
        async def gen():
            last = after
            while True:
                for e in events.events_after(conn, last):
                    last = e["id"]
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/backlog")
    def get_backlog(sim: str | None = None):
        path = sims[sim].backlog_path if sim in sims else home / "BACKLOG.md"
        if not path.exists():
            return []
        return [asdict(t) for t in backlog.parse(path.read_bytes().decode("utf-8"))]

    @app.get("/usage")
    def usage():
        return {"summary": events.usage_summary(conn), "rate_limit": events.latest_rate_limit(conn)}

    @app.get("/scenarios")
    def list_scenarios():
        return [{"name": name, "title": (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("name", name)}
                for name, p in scenarios().items()]

    @app.post("/simulate")
    def simulate(req: SimulateRequest):
        path = scenarios().get(req.scenario)
        if path is None:
            raise HTTPException(404, f"unknown scenario {req.scenario!r}")
        sim = prepare(path, Path(tempfile.mkdtemp(prefix="harness-sim-")), saver, step=req.step, delay=req.delay)
        sims[sim.run_id] = sim
        threading.Thread(target=run_sim, args=(sim, sim.inputs), daemon=True).start()
        return {"run_id": sim.run_id}

    @app.post("/runs/{run_id}/step")
    def step(run_id: str):
        sim = sims.get(run_id)
        if sim is None:
            raise HTTPException(404, "unknown simulation")
        if sim.lock.locked():
            raise HTTPException(409, "step already running")
        threading.Thread(target=run_sim, args=(sim, None), daemon=True).start()
        return {"ok": True}

    return app
```

Do `harness/cli.py` přidat:
```python
def cmd_dashboard(args) -> int:
    import uvicorn

    from .dashboard.app import create_app
    uvicorn.run(create_app(args.home), host="127.0.0.1", port=args.port)
    return 0
```
a v `handlers` nastavit `"dashboard": cmd_dashboard`. Funkce `_todo` už není potřeba, smaž ji.

`harness/dashboard/static/index.html` (placeholder): `<!doctype html><title>harness</title>`

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

CHANGELOG: `## [2026-10-01] - Dashboard API (FastAPI, SSE, spouštění simulací)`.
```bash
git add harness/dashboard harness/cli.py tests/test_dashboard.py CHANGELOG.md
git commit -m "feat: dashboard api with simulation control"
```

---

### Task 11: Dashboard frontend

**Files:**
- Modify: `harness/dashboard/static/index.html` (plná verze)

**Interfaces:**
- Consumes: endpointy z Task 10. Mermaid uzly mají v SVG id ve tvaru `flowchart-<name>-<n>`, aktivní uzel se proto hledá přes `[id^="flowchart-<name>-"]`.

- [ ] **Step 1: Implementation**

`harness/dashboard/static/index.html`:
```html
<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harness</title>
<style>
  :root { --bg:#f7f7f8; --panel:#fff; --text:#1b1b1f; --muted:#6b6b76; --line:#e2e2e7; --accent:#4b5cf0; --ok:#1f8a4c; --bad:#c8372d; --warn:#b7791f; }
  @media (prefers-color-scheme: dark) { :root { --bg:#111114; --panel:#1b1b20; --text:#ececf1; --muted:#9a9aa6; --line:#2c2c33; --accent:#8c98ff; --ok:#4cc38a; --bad:#ff6b5e; --warn:#f0b75a; } }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 system-ui, sans-serif; background:var(--bg); color:var(--text); }
  header { display:flex; gap:12px; align-items:center; padding:12px 16px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0 12px 0 0; }
  select, button { font:inherit; padding:6px 10px; border-radius:6px; border:1px solid var(--line); background:var(--panel); color:var(--text); }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  button:disabled { opacity:.5; }
  main { display:grid; grid-template-columns: minmax(0,1.2fr) minmax(0,1fr); gap:16px; padding:16px; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  section { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; min-width:0; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:0 0 8px; }
  #graph svg { max-width:100%; height:auto; }
  #graph .node.active rect, #graph .node.active polygon, #graph .node.active circle { stroke:var(--accent) !important; stroke-width:3px !important; fill:color-mix(in srgb, var(--accent) 25%, transparent) !important; }
  #graph .node.error rect { stroke:var(--bad) !important; stroke-width:3px !important; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  td, th { text-align:left; padding:4px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
  tr.sel { background:color-mix(in srgb, var(--accent) 12%, transparent); }
  tr[data-run] { cursor:pointer; }
  .tag { font-size:11px; padding:1px 6px; border-radius:99px; border:1px solid var(--line); color:var(--muted); }
  .k-end { color:var(--ok); } .k-error, .k-paused { color:var(--bad); } .k-start { color:var(--muted); }
  pre { white-space:pre-wrap; word-break:break-word; font-size:12px; background:var(--bg); padding:8px; border-radius:6px; max-height:260px; overflow:auto; margin:0; }
  .meter { height:8px; background:var(--line); border-radius:99px; overflow:hidden; }
  .meter > div { height:100%; background:var(--accent); }
  .muted { color:var(--muted); }
  .scroll { max-height:320px; overflow:auto; }
</style>
</head>
<body>
<header>
  <h1>Harness</h1>
  <select id="scenario" aria-label="Scénář"></select>
  <label><input type="checkbox" id="stepMode"> krokovat</label>
  <button class="primary" id="start">Spustit simulaci</button>
  <button id="step" disabled>Další krok</button>
  <span id="status" class="muted"></span>
</header>
<main>
  <section><h2>Graf</h2><div id="graph"></div><h2 style="margin-top:12px">Detail uzlu</h2><pre id="detail">Klikni na uzel nebo event.</pre></section>
  <section><h2>Běhy</h2><div class="scroll"><table id="runs"></table></div>
    <h2 style="margin-top:12px">Události běhu</h2><div class="scroll"><table id="events"></table></div></section>
  <section><h2>Backlog</h2><table id="backlog"></table></section>
  <section><h2>Spotřeba</h2><div id="quota"></div><table id="usage"></table></section>
</main>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, opts) => { const r = await fetch(path, opts); if (!r.ok) throw new Error(await r.text()); return r.json(); };
let selectedRun = null, lastEventId = 0, lastNodeData = {};

function nodeEl(name) { return document.querySelector(`#graph [id^="flowchart-${name}-"]`); }
function highlight(name, cls) {
  document.querySelectorAll("#graph .node").forEach((n) => n.classList.remove("active", "error"));
  const el = name && nodeEl(name);
  if (el) el.classList.add(cls);
}

async function renderGraph() {
  const { mermaid: src } = await api("/graph");
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "default" });
  const { svg } = await mermaid.render("g", src);
  $("graph").innerHTML = svg;
  $("graph").querySelectorAll(".node").forEach((n) => {
    const name = n.id.replace(/^flowchart-/, "").replace(/-\d+$/, "");
    n.style.cursor = "pointer";
    n.onclick = () => { $("detail").textContent = JSON.stringify(lastNodeData[name] ?? { node: name, info: "zatím bez dat v tomto běhu" }, null, 2); };
  });
}

async function loadScenarios() {
  const list = await api("/scenarios");
  $("scenario").innerHTML = list.map((s) => `<option value="${esc(s.name)}">${esc(s.title)}</option>`).join("");
}

async function loadRuns() {
  const runs = await api("/runs");
  $("runs").innerHTML = "<tr><th>Běh</th><th>Typ</th><th>Stav</th></tr>" + runs.slice(0, 30).map((r) =>
    `<tr data-run="${esc(r.run_id)}" class="${r.run_id === selectedRun ? "sel" : ""}"><td>${esc(r.run_id)}</td><td><span class="tag">${r.simulated ? "sim" : "real"}</span></td><td class="k-${esc(r.last_kind)}">${esc(r.last_kind)}</td></tr>`).join("");
  $("runs").querySelectorAll("tr[data-run]").forEach((tr) => tr.onclick = () => selectRun(tr.dataset.run));
}

async function selectRun(runId) {
  selectedRun = runId; lastNodeData = {};
  const evs = await api(`/runs/${encodeURIComponent(runId)}/events`);
  $("events").innerHTML = "";
  evs.forEach(addEventRow);
  const last = evs.filter((e) => e.node).at(-1);
  highlight(last?.node, last?.kind === "error" ? "error" : "active");
  await Promise.all([loadRuns(), loadBacklog(), refreshStepButton()]);
}

function addEventRow(e) {
  if (e.node && e.kind !== "start") lastNodeData[e.node] = e;
  const t = new Date(e.ts * 1000).toLocaleTimeString();
  const tr = document.createElement("tr");
  tr.innerHTML = `<td class="muted">${t}</td><td>${esc(e.node ?? "")}</td><td class="k-${esc(e.kind)}">${esc(e.kind)}</td>`;
  tr.style.cursor = "pointer";
  tr.onclick = () => { $("detail").textContent = JSON.stringify(e, null, 2); };
  $("events").prepend(tr);
}

async function loadBacklog() {
  const q = selectedRun?.startsWith("sim-") ? `?sim=${encodeURIComponent(selectedRun)}` : "";
  const tasks = await api(`/backlog${q}`);
  $("backlog").innerHTML = "<tr><th>Stav</th><th>P</th><th>Úkol</th><th>Projekt</th></tr>" + tasks.map((t) =>
    `<tr><td>[${esc(t.status)}]</td><td>P${t.priority}</td><td>${esc(t.id)} ${esc(t.title)}</td><td class="muted">${esc(t.project)}</td></tr>`).join("");
}

async function loadUsage() {
  const { summary, rate_limit } = await api("/usage");
  const util = rate_limit?.utilization;
  $("quota").innerHTML = util == null ? '<p class="muted">Kvóta: zatím žádná data ze skutečného běhu.</p>' :
    `<p>Okno ${esc(rate_limit.type)}: ${(util * 100).toFixed(0)} %${rate_limit.resets_at ? ", reset " + new Date(rate_limit.resets_at * 1000).toLocaleTimeString() : ""}</p><div class="meter"><div style="width:${Math.min(util * 100, 100)}%"></div></div>`;
  $("usage").innerHTML = "<tr><th>Den</th><th>Uzel</th><th>Model</th><th>Tokeny</th><th>$</th></tr>" + summary.map((u) =>
    `<tr><td>${esc(u.day)}${u.simulated ? ' <span class="tag">sim</span>' : ""}</td><td>${esc(u.node)}</td><td>${esc(u.model)}</td><td>${u.input_tokens + u.output_tokens}</td><td>${(u.cost_usd ?? 0).toFixed(3)}</td></tr>`).join("");
}

async function refreshStepButton() {
  if (!selectedRun) return;
  try {
    const st = await api(`/runs/${encodeURIComponent(selectedRun)}/state`);
    $("step").disabled = st.next.length === 0;
    $("status").textContent = st.next.length ? `čeká před: ${st.next.join(", ")}` : "";
    if (st.next.length) highlight(st.next[0], "active");
  } catch { $("step").disabled = true; }
}

$("start").onclick = async () => {
  const { run_id } = await api("/simulate", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario: $("scenario").value, step: $("stepMode").checked }) });
  await selectRun(run_id);
  setTimeout(refreshStepButton, 300);
};
$("step").onclick = async () => {
  $("step").disabled = true;
  await api(`/runs/${encodeURIComponent(selectedRun)}/step`, { method: "POST" });
  setTimeout(refreshStepButton, 300);
};

function listen() {
  const es = new EventSource(`/events/stream?after=${lastEventId}`);
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    lastEventId = e.id;
    if (e.run_id === selectedRun) {
      addEventRow(e);
      if (e.node) highlight(e.node, e.kind === "error" ? "error" : "active");
      if (e.kind === "end" && (e.node === "report" || e.node === "pick_task")) loadBacklog();
      if (["run_end", "paused"].includes(e.kind)) { highlight(null); $("status").textContent = e.kind; }
      if (e.kind === "end") refreshStepButton();
    }
    if (["run_start", "run_end", "paused"].includes(e.kind)) loadRuns();
    if (e.usage) loadUsage();
  };
}

(async () => {
  await Promise.all([renderGraph(), loadScenarios(), loadRuns(), loadBacklog(), loadUsage()]);
  const runs = await api("/runs");
  if (runs.length) {
    const evs = await api(`/runs/${encodeURIComponent(runs[0].run_id)}/events`);
    lastEventId = Math.max(0, ...evs.map((e) => e.id));
  }
  listen();
})();
</script>
</body>
</html>
```

Pozn. k poslednímu bloku: `lastEventId` se nastaví na nejvyšší ID nejnovějšího běhu, takže SSE neposílá celou historii.

**SRI pro Mermaid:** nahraď `mermaid@11` přesnou aktuální verzí (`npm view mermaid version`) a doplň `integrity` + `crossorigin="anonymous"`. Hash spočítej, nevymýšlej:
```bash
curl -sL https://cdn.jsdelivr.net/npm/mermaid@<verze>/dist/mermaid.min.js | openssl dgst -sha384 -binary | openssl base64 -A
```
→ `<script src="https://cdn.jsdelivr.net/npm/mermaid@<verze>/dist/mermaid.min.js" integrity="sha384-<hash>" crossorigin="anonymous"></script>`

- [ ] **Step 2: Ruční ověření (kritérium č. 2)**

Run (dva terminály):
1. `.venv/Scripts/harness dashboard`
2. Otevřít `http://127.0.0.1:8765`, vybrat „Testy dvakrát spadnou, potřetí projdou“, zaškrtnout „krokovat“ a klikat „Další krok“.

Expected:
- Graf se vykreslí.
- Aktivní uzel se zvýrazňuje.
- `verify` → `implement` se zopakuje dvakrát, pak přijde `open_pr`.
- V Backlogu se objeví `[x]`.
- Ve Spotřebě jsou řádky `plan/opus` a `implement/sonnet` se štítkem sim.

Udělej screenshot přes Playwright MCP (`browser_navigate` + `browser_take_screenshot`) a ulož ho do `docs/screenshots/flaky-tests-step.png`.

Navíc v CLI (kritérium č. 2 doslovně): `.venv/Scripts/harness simulate flaky-tests --step` při otevřeném dashboardu. Po kliknutí na nový běh v tabulce Běhy se eventy objevují živě přes SSE.

- [ ] **Step 3: Commit**

CHANGELOG: `## [2026-10-01] - Dashboard frontend (graf, běhy, backlog, spotřeba, krokování)`.
```bash
git add harness/dashboard/static/index.html docs/screenshots CHANGELOG.md
git commit -m "feat: dashboard frontend with live graph and step control"
```

---

### Task 12: Reálná konfigurace a E2E běh

**Files:**
- Create: `projects.yaml`, `BACKLOG.md`, `README.md` (krátký: instalace, příkazy)

**Interfaces:**
- Consumes: vše výše.

- [ ] **Step 1: Ověřit verify příkazy pilotních projektů**

Pro každý projekt v čerstvém worktree spustit setup a verify a zapsat výsledek:
```bash
cd /c/Users/TGJ/Projects
for p in guth-pictures akano3 secondbrain; do
  git -C $p fetch -q origin main && git -C $p worktree add -q ../.harness-worktrees/_check/$p origin/main
done
```
Pak v každém `/c/Users/TGJ/Projects/.harness-worktrees/_check/<p>` ručně spustit příkazy z tabulky níže přes `cmd.exe` (harness používá `shell=True`, což na Windows znamená `cmd.exe`, ne bash): `cmd //c "npm ci && npm test"` atd.

| Projekt | setup | verify |
|---|---|---|
| guth-pictures | `npm ci` | `npm test`, `npx tsc --noEmit` |
| akano3 | `npm ci` | `node --test lib/*.test.mjs`, `npx tsc --noEmit` |
| secondbrain | `npm ci --prefix web`, `python -m pip install -q -r bot/requirements.txt pytest` | `python -m pytest bot -q`, `npm test --prefix web` |

Expected: zjistit, které příkazy na `main` procházejí. Ty, které nefungují vůbec (špatný příkaz), oprav v tabulce. Ty, které padají kvůli kódu, nechej: pokryje je baseline. **Nic v projektech neopravuj.** Bugy jen nahlas uživateli. Pozor: `pip install` u secondbrainu instaluje do globálního Pythonu; pokud to nechceš, zeptej se uživatele.

Úklid: `git -C <p> worktree remove --force <cesta>` pro všechny tři.

- [ ] **Step 2: `projects.yaml` a `BACKLOG.md`**

`projects.yaml` (s příkazy opravenými podle Step 1):
```yaml
defaults:
  max_attempts: 3
  max_diff_lines: 800
billing: subscription
max_tasks_per_run: 3
quota_threshold: 0.7

projects:
  guth-pictures:
    path: ../guth-pictures
    base: main
    setup: ["npm ci"]
    verify: ["npm test", "npx tsc --noEmit"]
  akano3:
    path: ../akano3
    base: main
    setup: ["npm ci"]
    verify: ["node --test lib/*.test.mjs", "npx tsc --noEmit"]
  secondbrain:
    path: ../secondbrain
    base: main
    setup: ["npm ci --prefix web", "python -m pip install -q -r bot/requirements.txt pytest"]
    verify: ["python -m pytest bot -q", "npm test --prefix web"]
    allowed: ["bot/**", "web/**", "scripts/**"]
```

`BACKLOG.md`: **první reálný úkol vybírá uživatel.** Zeptej se ho a navrhni malý, ověřitelný úkol v guth-pictures (např. „přidat unit test pro existující util X“). Formát:
```markdown
# Backlog

## guth-pictures
- [ ] P1 gp-01 <název od uživatele>
  <popis a akceptační kritérium>
```

`README.md`:
```markdown
# harness

LangGraph orchestrátor: úkol z BACKLOG.md → plán (Opus) → implementace (Sonnet) ve worktree → verify → draft PR.

    python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
    .venv/Scripts/harness simulate flaky-tests [--step]
    .venv/Scripts/harness dashboard          # http://127.0.0.1:8765
    .venv/Scripts/harness run [--resume RUN_ID]
    .venv/Scripts/harness prune              # smaže worktree hotových úkolů

Subscription: `ANTHROPIC_API_KEY` nesmí být nastavený. Design: docs/specs/, plán: docs/plans/.
```

- [ ] **Step 3: E2E běh (kritérium č. 3)**

Run: `unset ANTHROPIC_API_KEY; .venv/Scripts/harness run`
Expected:
- `run-… done`
- V `BACKLOG.md` je `[x] … → https://github.com/RandomUsernameForMe/guth-pictures/pull/N`
- PR je **draft** a obsahuje plán
- `harness.db` obsahuje event s neprázdným `rate_limit` (`sqlite3 harness.db "select rate_limit from events where rate_limit is not null limit 1"`)

Pokud něco selže: zjisti root cause (`events`, `last_failure` v BACKLOGu, worktree zůstává) a nepouštěj `run` naslepo znovu. Max 2 pokusy se stejnou chybou, pak nahlas uživateli.

- [ ] **Step 4: Resume po zabití (kritérium č. 4)**

1. Přidej druhý malý úkol (`gp-02`, opět vybírá uživatel).
2. `.venv/Scripts/harness run`. Jakmile `sqlite3 harness.db "select node, kind from events order by id desc limit 1"` ukáže `implement|start`, ukonči proces (Ctrl+C).
3. `.venv/Scripts/harness run --resume <run_id>`

Expected:
- Úkol skončí `[x]`.
- `git -C ../guth-pictures branch -a | grep harness/gp-02` ukáže lokální a vzdálenou větev, každou jednou.
- `gh pr list --head harness/gp-02` ukáže právě 1 PR.

Ctrl+C během `implement` vyhodí `KeyboardInterrupt` a checkpoint zůstane na `implement`. Resume uzel spustí znovu, protože worktree se znovu použije.

- [ ] **Step 5: Commit**

CHANGELOG: `## [2026-10-01] - Pilotní konfigurace, první reálný běh (PR #N)`.
```bash
git add projects.yaml BACKLOG.md README.md CHANGELOG.md
git commit -m "chore: pilot project config and backlog"
```

---

## Mapování na kritéria hotovosti specu

| Kritérium | Kde se ověřuje |
|---|---|
| 1. pytest zelený, všechny scénáře | Tasks 1–10, `pytest -q` |
| 2. Krokovaná simulace v dashboardu | Task 11 Step 2 |
| 3. Reálný draft PR přes subscription + rate_limit v logu | Task 12 Step 3 |
| 4. Resume po zabití, 1 větev, 1 PR | Task 12 Step 4 |
| 5. Worker bez globálních pluginů | Task 8 Step 6 |
