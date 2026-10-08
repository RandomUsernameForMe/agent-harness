# harness go — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `harness go` — Opus projde projekty, navrhne a seřadí úkoly, uživatel plán jednou schválí v terminálu, workery z něj udělají draft PR.

**Architecture:** Rodičovský graf dostane větev `START → discover ×projekt (Send) → prioritize → approve (interrupt) → pick_tasks`. Čisté funkce (parsování výstupů modelu, plán, zápis do BACKLOGu) žijí v `harness/discover.py`; uzly v `nodes/common.py` berou injektované funkce `explore` / `rank_fn` (reálné v `nodes/real.py`, falešné v `nodes/fake.py`), takže simulace i reálný běh jdou stejnou cestou parsování.

**Tech Stack:** Python 3.11, LangGraph (`Send`, `interrupt`, `Command`), claude-agent-sdk, pytest.

**Spec:** `docs/specs/2026-10-07-harness-go-design.md`

## Global Constraints

- Volání modelu jen přes `harness.sdk.run_agent`; `discover` a `prioritize` = `model="opus"`.
- `discover`: `tools=READ_TOOLS`, `permission_mode="dontAsk"`, `max_turns=40`; `prioritize`: `tools=[]`, `max_turns=1`.
- Max 5 návrhů na projekt; ID `<projekt>-ai-<sha1(title)[:6]>`; nové úkoly mají prioritu P2.
- Délka plánu = `max_tasks_per_run`.
- Stavy BACKLOGu: schválené `[ ]`, vyškrtnuté `[-]`, nevybrané nebo `q` → `[~]`; úkoly z BACKLOGu se v `approve` nemění.
- `harness run` se chová beze změny (bez `discover` v počátečním stavu).
- Prompty se renderují přes `str.format` → složené závorky v JSON příkladech zdvojit `{{ }}`.
- Kód: funkcionální styl, krátké funkce, komentáře jen tam, kde kód sám nestačí. Každý commit s řádkem v `CHANGELOG.md` (`## [2026-10-07] - popis`) — hook commit bez něj zablokuje; `git add` pusť samostatně před `git commit`.

## Review Focus

1. **Odpověď modelu obalená textem / s ```json blokem / úplně bez JSON** → návrhy se vytáhnou, resp. projekt vrátí `[]` a chybu v `go_errors`, běh pokračuje (testy v Tasku 1 a 2).
2. **Překlep při schvalování (`7` u plánu se 3 položkami, `2,` , `abc`)** → terminál vypíše chybu a ptá se znovu, nic se nezapíše (Task 1 `parse_approval`, Task 3 CLI test).
3. **Rate limit uprostřed `discover`** → běh `paused`, po resume se dokončené projekty znovu nevolají (Task 3, scénář `go-discover-rate-limit`).
4. **Opakovaný `harness go`** → dřív odmítnuté (`[-]`) ani hotové návrhy se znovu neobjeví a nic se nezapíše dvakrát (Task 1 dedup + idempotentní `apply_approval`).
5. **Žádný projekt nic nenavrhne a BACKLOG je prázdný** → bez dotazu, běh `done`, žádný worker (Task 2 test `approve` s prázdným plánem).

---

### Task 1: Čisté funkce `harness/discover.py` + `captures_about`

**Files:**
- Create: `harness/discover.py`
- Modify: `harness/priority.py` (přidat `captures_about`)
- Test: `tests/test_discover.py`, `tests/test_priority.py`

**Interfaces:**
- Produces:
  - `json_array(text: str) -> list | None`
  - `proposal_id(project: str, title: str) -> str`
  - `parse_proposals(text: str, project: str, known: list[Task]) -> list[dict]` — dict klíče `id, project, title, kind, description, why, done, source="new"`
  - `candidates(proposals: list[dict], tasks: list[Task], projects: set[str]) -> list[dict]` — přidá `priority` (návrhy 2), úkoly z BACKLOGu mají `source="backlog"`
  - `parse_ranking(text: str, cands: list[dict], n: int) -> list[dict]`
  - `fallback_plan(cands: list[dict], focus: dict[str, int], weights: dict[str, int], n: int) -> list[dict]`
  - `parse_approval(answer: str, n: int) -> list[int] | str` — `"q"` nebo ponechané 0-based indexy; jinak `ValueError`
  - `apply_approval(text: str, proposals: list[dict], plan: list[dict], kept: list[str] | None) -> str` — `None` = `q`
  - `format_plan(plan: list[dict]) -> str`
  - `priority.captures_about(root: Path, project: str, days: int, limit: int = 8000, today: date | None = None) -> str`

- [ ] **Step 1: Testy (padají)** — `tests/test_discover.py`:

```python
import pytest

from harness import backlog
from harness.backlog import Task
from harness.discover import (apply_approval, candidates, fallback_plan, format_plan, json_array, parse_approval,
                              parse_proposals, parse_ranking, proposal_id)


def task(id, title, status=" ", project="demo", priority=2, description=""):
    return Task(id, project, priority, title, status, description)


def test_json_array_from_fence_or_bare_text():
    assert json_array('Tady:\n```json\n[{"a": 1}]\n```\nhotovo') == [{"a": 1}]
    assert json_array('Návrhy: [{"a": 1}] konec') == [{"a": 1}]
    assert json_array("nic tu není") is None
    assert json_array('{"a": 1}') is None


def test_parse_proposals_normalizes_and_limits():
    items = ",".join(f'{{"title": "Úkol {i}", "kind": "test", "done": "pytest"}}' for i in range(7))
    props = parse_proposals(f"[{items}]", "demo", [])
    assert len(props) == 5
    assert props[0] == {"id": proposal_id("demo", "Úkol 0"), "project": "demo", "title": "Úkol 0", "kind": "test",
                        "description": "", "why": "", "done": "pytest", "source": "new"}


def test_parse_proposals_drops_junk_and_duplicates():
    text = ('[{"title": "  Víc\\nřádků  ", "kind": "nonsense"}, {"title": ""}, "x",'
            ' {"title": "Hotové"}, {"title": "víc řádků"}]')
    props = parse_proposals(text, "demo", [task("old", "Hotové", status="x")])
    assert [(p["title"], p["kind"]) for p in props] == [("Víc řádků", "debt")]


def test_parse_proposals_skips_known_id_and_bad_json():
    known = [task(proposal_id("demo", "Testy"), "Jiný název", status="-")]
    assert parse_proposals('[{"title": "Testy"}]', "demo", known) == []
    assert parse_proposals("not json", "demo", []) == []


def test_proposal_id_is_stable_and_safe():
    assert proposal_id("guth pictures", "X") == proposal_id("guth pictures", "X")
    assert proposal_id("guth pictures", "X").startswith("guth-pictures-ai-")


def props(*titles, project="demo"):
    return [{"id": proposal_id(project, t), "project": project, "title": t, "kind": "test", "description": "d",
             "why": "w", "done": "ok", "source": "new"} for t in titles]


def test_candidates_new_sorted_by_project_then_open_backlog():
    cands = candidates(props("B", project="web") + props("A"),
                       [task("t1", "Otevřený"), task("t2", "Hotový", status="x"), task("t3", "Cizí", project="nope")],
                       {"demo", "web"})
    assert [(c["title"], c["source"], c["priority"]) for c in cands] == [
        ("A", "new", 2), ("B", "new", 2), ("Otevřený", "backlog", 2)]


def test_parse_ranking_drops_unknown_and_repeats_and_caps():
    cands = candidates(props("A", "B", "C"), [], {"demo"})
    ids = [c["id"] for c in cands]
    text = f'[{{"id": "{ids[2]}", "why": "první"}}, {{"id": "zzz"}}, {{"id": "{ids[2]}"}}, {{"id": "{ids[0]}"}}, {{"id": "{ids[1]}"}}]'
    plan = parse_ranking(text, cands, 2)
    assert [(p["id"], p["why"]) for p in plan] == [(ids[2], "první"), (ids[0], "w")]
    assert parse_ranking("garbage", cands, 2) == []


def test_fallback_plan_uses_priority_and_focus():
    cands = candidates(props("A") + props("B", project="web"), [task("t1", "Naléhavý", priority=1)], {"demo", "web"})
    plan = fallback_plan(cands, {"web": 3}, {}, 2)
    assert [p["title"] for p in plan] == ["Naléhavý", "B"]
    assert plan[0]["why"].startswith("fallback")


@pytest.mark.parametrize("answer,expected", [("", [0, 1, 2]), ("  ", [0, 1, 2]), ("2", [0, 2]),
                                             ("1,3", [1]), ("1 3", [1]), ("2,", [0, 2]), ("Q", "q")])
def test_parse_approval(answer, expected):
    assert parse_approval(answer, 3) == expected


@pytest.mark.parametrize("answer", ["4", "0", "abc", "1-2"])
def test_parse_approval_rejects(answer):
    with pytest.raises(ValueError):
        parse_approval(answer, 3)


def test_apply_approval_writes_statuses_once():
    p = props("Schválený", "Vyškrtnutý", "Nevybraný")
    plan = p[:2]
    text = apply_approval("## demo\n- [ ] P1 t1 Ruční\n", p, plan, [p[0]["id"]])
    text = apply_approval(text, p, plan, [p[0]["id"]])  # idempotent
    st = {t.title: t.status for t in backlog.parse(text)}
    assert st == {"Ruční": " ", "Schválený": " ", "Vyškrtnutý": "-", "Nevybraný": "~"}
    assert f"- [ ] P2 {p[0]['id']} Schválený\n  d\n  Hotovo = ok\n" in text


def test_apply_approval_quit_parks_everything():
    p = props("A", "B")
    text = apply_approval("", p, p, None)
    assert {t.status for t in backlog.parse(text)} == {"~"}


def test_format_plan():
    plan = [props("A")[0], candidates([], [task("t1", "Ruční")], {"demo"})[0] | {"why": "proč"}]
    out = format_plan(plan)
    assert "1. [demo] test A (nový)" in out
    assert "2. [demo] - Ruční (z BACKLOGu)" in out and "proč" in out
```

Do `tests/test_priority.py` přidat (soubor už importuje `date` a `Path`; jinak doplnit):

```python
def test_captures_about_newest_first_and_capped(tmp_path):
    from harness.priority import captures_about
    (tmp_path / "Thoughts").mkdir()
    (tmp_path / "Thoughts" / "2026-10-01 a.md").write_text("starší guth-pictures", encoding="utf-8")
    (tmp_path / "Thoughts" / "2026-10-05 b.md").write_text("novější guth pictures", encoding="utf-8")
    (tmp_path / "Thoughts" / "2026-10-06 c.md").write_text("o ničem", encoding="utf-8")
    (tmp_path / "Thoughts" / "2026-01-01 d.md").write_text("stará guth-pictures", encoding="utf-8")
    out = captures_about(tmp_path, "guth-pictures", 14, today=date(2026, 10, 7))
    assert out.index("novější") < out.index("starší") and "o ničem" not in out and "stará" not in out
    assert len(captures_about(tmp_path, "guth-pictures", 14, limit=20, today=date(2026, 10, 7))) == 20
```

- [ ] **Step 2: Spustit** `.venv/Scripts/python -m pytest tests/test_discover.py tests/test_priority.py -q` → FAIL (`ModuleNotFoundError: harness.discover`, `ImportError: captures_about`).

- [ ] **Step 3: Implementace** — `harness/discover.py`:

```python
"""Pure helpers of `harness go`: parse model answers, build the plan, write the owner's approval to BACKLOG."""
import hashlib
import json
import re

from . import backlog
from .backlog import Task
from .priority import rank

KINDS = ("test", "bug", "debt", "feature", "docs")
MAX_PROPOSALS = 5
NEW_PRIORITY = 2
FENCE_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)


def json_array(text: str) -> list | None:
    """The JSON array of a model answer: a ```json fence, else the outermost [...]."""
    m = FENCE_RE.search(text)
    raw = m[1] if m else text[text.find("["): text.rfind("]") + 1]
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _line(value) -> str:
    return " ".join(str(value or "").split())


def proposal_id(project: str, title: str) -> str:
    digest = hashlib.sha1(title.encode()).hexdigest()[:6]
    return f"{re.sub(r'[^A-Za-z0-9._-]', '-', project)}-ai-{digest}"


def parse_proposals(text: str, project: str, known: list[Task]) -> list[dict]:
    """Up to MAX_PROPOSALS new proposals. `known` = backlog tasks in any status, so rejected ones never return."""
    ids, titles = {t.id for t in known}, {t.title.casefold() for t in known}
    out: list[dict] = []
    for item in json_array(text) or []:
        title = _line(item.get("title"))[:100] if isinstance(item, dict) else ""
        pid = proposal_id(project, title)
        if not title or pid in ids or title.casefold() in titles:
            continue
        ids.add(pid)
        titles.add(title.casefold())
        out.append({"id": pid, "project": project, "title": title,
                    "kind": item.get("kind") if item.get("kind") in KINDS else "debt",
                    "description": str(item.get("description") or "").strip(),
                    "why": _line(item.get("why")), "done": _line(item.get("done")), "source": "new"})
    return out[:MAX_PROPOSALS]


def candidates(proposals: list[dict], tasks: list[Task], projects: set[str]) -> list[dict]:
    """New proposals (stable by project — parallel discover finishes in any order) + open backlog tasks."""
    new = [p | {"priority": NEW_PRIORITY} for p in sorted(proposals, key=lambda p: p["project"])]
    old = [{"id": t.id, "project": t.project, "title": t.title, "kind": "", "description": t.description,
            "why": "", "done": "", "source": "backlog", "priority": t.priority}
           for t in tasks if t.status == " " and t.project in projects]
    return new + old


def parse_ranking(text: str, cands: list[dict], n: int) -> list[dict]:
    by_id = {c["id"]: c for c in cands}
    plan: list[dict] = []
    for item in json_array(text) or []:
        cid = item.get("id") if isinstance(item, dict) else None
        if cid in by_id and all(p["id"] != cid for p in plan):
            plan.append(by_id[cid] | {"why": _line(item.get("why")) or by_id[cid]["why"]})
    return plan[:n]


def fallback_plan(cands: list[dict], focus: dict[str, int], weights: dict[str, int], n: int) -> list[dict]:
    tasks = [Task(c["id"], c["project"], c["priority"], c["title"], " ", c["description"]) for c in cands]
    by_id = {c["id"]: c for c in cands}
    ranked = rank(tasks, {c["project"] for c in cands}, focus, weights)
    return [by_id[t.id] | {"why": "fallback: pořadí podle priority"} for t in ranked[:n]]


def parse_approval(answer: str, n: int) -> list[int] | str:
    """"" = keep all, "2,5" = strike 2 and 5, "q" = quit. Anything else raises ValueError."""
    a = answer.strip().lower()
    if a == "q":
        return "q"
    drop = set()
    for part in filter(None, re.split(r"[,\s]+", a)):
        if not part.isdigit() or not 1 <= int(part) <= n:
            raise ValueError(f"neplatná volba: {part} (čísla 1–{n}, Enter, q)")
        drop.add(int(part) - 1)
    return [i for i in range(n) if i not in drop]


def task_lines(p: dict, status: str) -> list[str]:
    lines = [f"- [{status}] P{NEW_PRIORITY} {p['id']} {p['title']}"]
    lines += [f"  {row.strip()}" for row in p["description"].splitlines() if row.strip()]
    return lines + ([f"  Hotovo = {p['done']}"] if p["done"] else [])


def _status(pid: str, planned: set[str], kept: list[str] | None) -> str:
    if kept is None:
        return "~"
    if pid in kept:
        return " "
    return "-" if pid in planned else "~"


def apply_approval(text: str, proposals: list[dict], plan: list[dict], kept: list[str] | None) -> str:
    """New proposals into BACKLOG once: approved [ ], struck out [-], not planned / quit [~]."""
    planned, known = {p["id"] for p in plan}, {t.id for t in backlog.parse(text)}
    for p in proposals:
        if p["id"] not in known:
            text = backlog.add_tasks(text, p["project"], task_lines(p, _status(p["id"], planned, kept)))
    return text


def format_plan(plan: list[dict]) -> str:
    rows: list[str] = []
    for i, p in enumerate(plan, 1):
        src = "nový" if p["source"] == "new" else "z BACKLOGu"
        rows.append(f"{i}. [{p['project']}] {p['kind'] or '-'} {p['title']} ({src})")
        if p["why"]:
            rows.append(f"     {p['why']}")
    return "\n".join(rows)
```

Do `harness/priority.py` (pod `focus`):

```python
def captures_about(root: Path, project: str, days: int, limit: int = 8000, today: date | None = None) -> str:
    """Recent secondbrain captures mentioning the project, newest first, at most `limit` characters."""
    since = (today or date.today()) - timedelta(days=days)
    pattern, out = _mention_re(project), ""
    for p in sorted(_recent_captures(Path(root), since), key=lambda p: p.name, reverse=True):
        text = p.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text):
            out += f"### {p.name}\n{text.strip()}\n\n"
        if len(out) >= limit:
            break
    return out[:limit]
```

- [ ] **Step 4: Spustit** stejný příkaz → PASS.
- [ ] **Step 5: Commit** — CHANGELOG `## [2026-10-07] - harness go: čisté funkce (parsování návrhů a pořadí, schválení, zápis do BACKLOGu), poznámky secondbrainu k projektu`; `git add harness/discover.py harness/priority.py tests/test_discover.py tests/test_priority.py CHANGELOG.md`, pak `git commit -m "feat: harness go pure helpers"`.

---

### Task 2: Graf — větev go, uzly `discover` / `prioritize` / `approve`, `pick_tasks` podle plánu

**Files:**
- Modify: `harness/graph.py`, `harness/nodes/common.py`
- Test: `tests/test_graph.py`, `tests/test_go_nodes.py` (nový)

**Interfaces:**
- Consumes: Task 1 (`parse_proposals`, `json_array`, `candidates`, `parse_ranking`, `fallback_plan`, `apply_approval`).
- Produces:
  - `graph.NODES = ("discover", "prioritize", "approve", "pick_tasks", *WORKER_NODES)`
  - `graph.State` nové klíče: `discover: bool`, `projects: list[str]`, `proposals: Annotated[list, operator.add]`, `plan: list[dict]`, `planned: list[str] | None`, `go_errors: Annotated[list, operator.add]`
  - `graph.start(state) -> list[Send] | str`, `graph.after_approve(state) -> str`
  - `graph.initial_state(run_id, cfg, simulated, discover: bool = False) -> dict`
  - `common.make_discover(backlog_path: Path, explore: Callable[[str, list[Task]], tuple[str, list[dict]]])`
  - `common.make_prioritize(backlog_path: Path, cfg: Config, rank_fn: Callable[[list[dict], int, dict, dict], tuple[str, list[dict]]])`
  - `common.make_approve(backlog_path: Path)` — `interrupt({"plan": plan})`, resume hodnota `list[int] | "q"`

- [ ] **Step 1: Testy (padají)** — do `tests/test_graph.py`:

```python
from harness.graph import after_approve, start


def test_start_routes_go_and_run():
    assert start({"discover": False}) == "pick_tasks"
    assert start({}) == "pick_tasks"
    assert start({"discover": True, "projects": ["a", "b"]}) == [Send("discover", {"project": "a"}),
                                                                Send("discover", {"project": "b"})]
    assert start({"discover": True, "projects": []}) == "prioritize"


def test_after_approve():
    assert after_approve({"planned": ["x"]}) == "pick_tasks"
    assert after_approve({"planned": []}) == END
```

Nový `tests/test_go_nodes.py`:

```python
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from harness import backlog
from harness.discover import proposal_id
from harness.graph import NODES, RateLimited, build_graph, initial_state
from harness.nodes.common import make_approve, make_discover, make_pick_tasks, make_prioritize, make_report
from harness.projects import Config, Project


def cfg(*names):
    return Config("subscription", 3, 0.7, {n: Project(n, ".", "main", (), ("true",), (), 1, 800) for n in names})


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


def read(path):
    return {t.id: t.status for t in backlog.parse(path.read_bytes().decode("utf-8"))}


def test_discover_passes_known_tasks_and_parses(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "## demo\n- [-] P2 old Odmítnuté\n## web\n- [ ] P1 w1 Jiné\n")
    seen = {}

    def explore(name, known):
        seen[name] = [t.id for t in known]
        return '[{"title": "Odmítnuté"}, {"title": "Nové", "kind": "bug"}]', [{"node": "discover"}]
    out = make_discover(bp, explore)({"project": "demo"})
    assert seen == {"demo": ["old"]}
    assert [p["title"] for p in out["proposals"]] == ["Nové"]
    assert out["usage"] == [{"node": "discover"}] and out["go_errors"] == []


def test_discover_failure_costs_only_this_project(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")
    boom = make_discover(bp, lambda n, k: (_ for _ in ()).throw(RuntimeError("agent died")))
    assert boom({"project": "demo"}) == {"proposals": [], "go_errors": ["demo: RuntimeError: agent died"]}
    prose = make_discover(bp, lambda n, k: ("Nic jsem nenašel.", []))
    assert prose({"project": "demo"})["go_errors"] == ["demo: odpověď bez JSON pole"]
    empty = make_discover(bp, lambda n, k: ("[]", []))
    assert empty({"project": "demo"})["go_errors"] == []


def test_discover_lets_rate_limit_through(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")

    def limited(name, known):
        raise RateLimited(123)
    with pytest.raises(RateLimited):
        make_discover(bp, limited)({"project": "demo"})


def proposal(title, project="demo"):
    return {"id": proposal_id(project, title), "project": project, "title": title, "kind": "test",
            "description": "", "why": "", "done": "", "source": "new"}


def test_prioritize_uses_model_order_or_falls_back(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "## demo\n- [ ] P1 t1 Ruční\n")
    state = {"proposals": [proposal("A"), proposal("B")], "max_tasks": 2}
    a, b = state["proposals"][0]["id"], state["proposals"][1]["id"]
    ok = make_prioritize(bp, cfg("demo"), lambda c, n, f, w: (json.dumps([{"id": b}, {"id": "t1"}]), [{"u": 1}]))
    out = ok(state)
    assert [p["id"] for p in out["plan"]] == [b, "t1"] and out["usage"] == [{"u": 1}]
    bad = make_prioritize(bp, cfg("demo"), lambda c, n, f, w: (_ for _ in ()).throw(RuntimeError("x")))
    out = bad(state)
    assert [p["id"] for p in out["plan"]] == ["t1", a]
    assert out["go_errors"] == ["prioritize: RuntimeError: x"]


def test_prioritize_without_candidates_skips_model(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "")
    called = []
    node = make_prioritize(bp, cfg("demo"), lambda *a: called.append(a) or ("[]", []))
    assert node({"proposals": []}) == {"plan": []} and not called


def go_graph(tmp_path, explore, rank_fn, workers):
    bp = tmp_path / "BACKLOG.md"
    c = cfg("demo", "web")
    nodes = {n: (lambda s: {}) for n in NODES} | {
        "discover": make_discover(bp, explore), "prioritize": make_prioritize(bp, c, rank_fn),
        "approve": make_approve(bp), "pick_tasks": make_pick_tasks(bp, c), "report": make_report(bp),
        "verify": lambda s: {"verify_ok": True},  # noop verify would loop implement ↔ verify forever
        "open_pr": lambda s: workers.append(s["task"]["id"]) or {"outcome": "pr_opened", "pr_url": "u"}}
    return build_graph(nodes, InMemorySaver()), bp, c


def test_go_end_to_end_with_approval(tmp_path):
    workers = []
    explore = lambda name, known: (json.dumps([{"title": f"Úkol {name}"}]), [])
    rank_all = lambda cands, n, f, w: (json.dumps([{"id": c["id"]} for c in cands]), [])
    app, bp, c = go_graph(tmp_path, explore, rank_all, workers)
    write(bp, "## demo\n- [ ] P3 t1 Ruční\n")
    config = {"configurable": {"thread_id": "go"}, "recursion_limit": 100}
    app.invoke(initial_state("go", c, True, discover=True), config)
    asked = app.get_state(config).tasks[0].interrupts[0]
    assert [p["title"] for p in asked.value["plan"]] == ["Úkol demo", "Úkol web", "Ruční"]
    app.invoke(Command(resume={asked.id: [0, 2]}), config)
    demo, web = proposal_id("demo", "Úkol demo"), proposal_id("web", "Úkol web")
    assert read(bp) == {"t1": "x", demo: "x", web: "-"}
    assert sorted(workers) == sorted(["t1", demo])


def test_go_with_empty_plan_ends_without_question(tmp_path):
    workers = []
    app, bp, c = go_graph(tmp_path, lambda n, k: ("[]", []), lambda *a: ("[]", []), workers)
    write(bp, "")
    config = {"configurable": {"thread_id": "go"}, "recursion_limit": 100}
    app.invoke(initial_state("go", c, True, discover=True), config)
    assert not app.get_state(config).next and not workers


def test_pick_tasks_follows_planned_order(tmp_path):
    bp = tmp_path / "BACKLOG.md"
    write(bp, "## demo\n- [ ] P1 a A\n- [ ] P3 b B\n## web\n- [ ] P1 c C\n- [ ] P1 d D\n")
    pick = make_pick_tasks(bp, cfg("demo", "web"))
    batch = pick({"planned": ["b", "a", "d"], "max_tasks": 3, "max_parallel": 2, "tasks_done": 0})["batch"]
    assert [i["task"]["id"] for i in batch] == ["b", "d"]
    assert [i["task"]["id"] for i in pick({"max_tasks": 3, "max_parallel": 2})["batch"]] == ["a", "c"]
```

- [ ] **Step 2: Spustit** `.venv/Scripts/python -m pytest tests/test_graph.py tests/test_go_nodes.py -q` → FAIL (`ImportError: after_approve`, `make_discover`).

- [ ] **Step 3: Implementace `harness/graph.py`:**

`NODES`:
```python
NODES = ("discover", "prioritize", "approve", "pick_tasks", *WORKER_NODES)
```

Do `State` přidat:
```python
    discover: bool
    projects: list[str]
    proposals: Annotated[list, operator.add]  # parallel discover writes
    plan: list[dict]
    planned: list[str] | None  # approved ids in order; None = `harness run`, rank the whole backlog
    go_errors: Annotated[list, operator.add]
```

Routery (pod `fan_out`):
```python
def start(state: State) -> list[Send] | str:
    if not state.get("discover"):
        return "pick_tasks"
    return [Send("discover", {"project": p}) for p in state.get("projects") or []] or "prioritize"


def after_approve(state: State) -> str:
    return "pick_tasks" if state.get("planned") else END
```

`_safe` rozdělit (chování workerů beze změny):
```python
def _pausing(fn: Callable) -> Callable:
    def wrapped(state: dict) -> dict:
        try:
            return fn(state)
        except RateLimited as e:
            limited = e
        # interrupt, not an exception: an exception cancels the step and parallel siblings would finish
        # unobserved; an interrupt lets them complete. Each resume re-runs the node once; interrupts answered
        # by earlier resumes return immediately, the first unanswered one pauses the run again.
        while True:
            interrupt({"rate_limited": limited.resets_at})
    return wrapped


def _safe(fn: Callable) -> Callable:
    def guarded(state: WorkerState) -> dict:
        try:
            return fn(state)
        except RateLimited:
            raise
        except Exception as e:
            return {"outcome": "failed", "last_failure": f"{type(e).__name__}: {e}"}
    return _pausing(guarded)
```

`build_graph` — nahradit tělo za `g = StateGraph(State)`:
```python
    g = StateGraph(State)
    g.add_node("discover", _pausing(nodes["discover"]))
    g.add_node("prioritize", _pausing(nodes["prioritize"]))
    g.add_node("approve", nodes["approve"])  # not _pausing: its interrupt must propagate
    g.add_node("pick_tasks", nodes["pick_tasks"])
    g.add_node("worker", _worker_node(build_worker(nodes, step)))
    g.add_conditional_edges(START, start, ["discover", "prioritize", "pick_tasks"])
    g.add_edge("discover", "prioritize")
    g.add_edge("prioritize", "approve")
    g.add_conditional_edges("approve", after_approve, ["pick_tasks", END])
    g.add_conditional_edges("pick_tasks", fan_out, ["worker", END])
    g.add_edge("worker", "pick_tasks")
    return g.compile(checkpointer=checkpointer,
                     interrupt_before=["discover", "prioritize", "pick_tasks"] if step else None)
```

`initial_state`:
```python
def initial_state(run_id: str, cfg: Config, simulated: bool, discover: bool = False) -> dict:
    return {"run_id": run_id, "simulated": simulated, "max_tasks": cfg.max_tasks_per_run,
            "max_parallel": cfg.max_parallel, "quota_threshold": cfg.quota_threshold, "tasks_done": 0, "usage": [],
            "discover": discover, "projects": list(cfg.projects)}
```

- [ ] **Step 4: Implementace `harness/nodes/common.py`** — importy:
```python
from langgraph.types import interrupt

from ..backlog import Task
from ..discover import apply_approval, candidates, fallback_plan, json_array, parse_proposals, parse_ranking
from ..graph import RateLimited
```

V `make_pick_tasks` nahradit řádek `ranked = rank(...)`:
```python
        planned = state.get("planned")
        if planned is None:
            ranked = rank(tasks, set(cfg.projects), config_focus(cfg), weights)
        else:
            order = {tid: i for i, tid in enumerate(planned)}
            ranked = sorted((t for t in tasks if t.status == " " and t.id in order), key=lambda t: order[t.id])
```

Nové továrny (na konec souboru):
```python
def _error(where: str, e: Exception) -> str:
    return f"{where}: {type(e).__name__}: {e}"


def make_discover(backlog_path: Path, explore: Callable[[str, list[Task]], tuple[str, list[dict]]]):
    """Proposals of one project (Send payload {"project"}). A failure costs only this project's proposals."""
    def discover(state: dict) -> dict:
        name = state["project"]
        with _backlog_lock:
            known = [t for t in backlog.parse(_read(backlog_path)) if t.project == name]
        try:
            text, usage = explore(name, known)
        except RateLimited:
            raise
        except Exception as e:
            return {"proposals": [], "go_errors": [_error(name, e)]}
        errors = [] if json_array(text) is not None else [f"{name}: odpověď bez JSON pole"]
        return {"proposals": parse_proposals(text, name, known), "usage": usage, "go_errors": errors}
    return discover


def make_prioritize(backlog_path: Path, cfg: Config,
                    rank_fn: Callable[[list[dict], int, dict, dict], tuple[str, list[dict]]]):
    weights = {name: p.weight for name, p in cfg.projects.items()}

    def prioritize(state: dict) -> dict:
        with _backlog_lock:
            tasks = backlog.parse(_read(backlog_path))
        cands = candidates(state.get("proposals") or [], tasks, set(cfg.projects))
        if not cands:
            return {"plan": []}
        n, focus = state.get("max_tasks", cfg.max_tasks_per_run), config_focus(cfg)
        try:
            text, usage = rank_fn(cands, n, focus, weights)
            errors = []
        except RateLimited:
            raise
        except Exception as e:
            text, usage, errors = "", [], [_error("prioritize", e)]
        plan = parse_ranking(text, cands, n) or fallback_plan(cands, focus, weights, n)
        return {"plan": plan, "usage": usage, "go_errors": errors}
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
```

- [ ] **Step 5: Doplnit chybějící uzly všude, kde se staví graf** (jinak `missing nodes`). Dočasně, než přijdou Task 3/4, stačí v `harness/simulate.py` a `harness/cli.py::_real_app` přidat:
```python
"discover": make_discover(backlog_path, lambda name, known: ("[]", [])),
"prioritize": make_prioritize(backlog_path, cfg, lambda c, n, f, w: ("[]", [])),
"approve": make_approve(backlog_path),
```
(+ import z `.nodes.common`). Task 3 a 4 je nahradí skutečnými funkcemi.

- [ ] **Step 6: Spustit celou sadu** `.venv/Scripts/python -m pytest -q` → vše PASS (včetně 143 starých; `harness run` větev nesmí být dotčena).
- [ ] **Step 7: Commit** — CHANGELOG `## [2026-10-07] - harness go: graf (Send discover → prioritize → approve interrupt), pick_tasks podle schváleného plánu`; `git add harness/graph.py harness/nodes/common.py harness/simulate.py harness/cli.py tests/test_graph.py tests/test_go_nodes.py CHANGELOG.md`, `git commit -m "feat: go branch of the graph (discover, prioritize, approve)"`.

---

### Task 3: Runner, simulace a terminál (`approving`, falešné uzly, scénáře)

**Files:**
- Modify: `harness/runner.py`, `harness/nodes/fake.py`, `harness/simulate.py`, `harness/cli.py`
- Create: `scenarios/go-happy.yaml`, `scenarios/go-reject-all.yaml`, `scenarios/go-discover-rate-limit.yaml`
- Test: `tests/test_scenarios.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 2 (`make_discover`, `make_prioritize`, `make_approve`, `initial_state(..., discover=)`), Task 1 (`format_plan`, `parse_approval`).
- Produces:
  - `runner.approvals(app, config) -> list[dict]` (`{"id", "plan"}`)
  - `runner.execute(...)` vrací nově i `"approving"`; `answers: dict[str, object] | None`
  - `fake.fake_explore(script: Script) -> Callable`, `fake.fake_rank(script: Script) -> Callable`
  - `cli.ask_approval(app, config) -> dict`, `cli.answer_in_terminal(app, config, status) -> dict`, příkaz `harness go`

- [ ] **Step 1: Scénáře.**

`scenarios/go-happy.yaml`:
```yaml
name: harness go — dva projekty, plán, schválení, PR
tasks:
  - {project: demo, id: sim-01, title: "Ruční úkol z BACKLOGu", priority: P2}
discover:
  demo:
    proposals:
      - {title: "Testy pro parser", description: "Pokryj parse() v lib/parser.ts", kind: test, why: "chybí testy", done: "npm test zelený"}
  web:
    proposals:
      - {title: "Opravit odkaz v patičce", kind: bug, why: "vede na 404", done: "odkaz vede na /kontakt"}
      - {title: "Přepsat celý frontend", kind: feature, why: "proč ne", done: "?"}
nodes:
  # 1-based indexy kandidátů: návrhy seřazené podle projektu, pak otevřené úkoly z BACKLOGu
  prioritize: {pick: [1, 2, 4]}
max_tasks: 3
delay: 0.5
```

`scenarios/go-reject-all.yaml`:
```yaml
name: harness go — vlastník plán zamítne (q)
tasks: []
discover:
  demo:
    proposals:
      - {title: "Testy pro parser", kind: test, done: "npm test zelený"}
delay: 0.5
```

`scenarios/go-discover-rate-limit.yaml`:
```yaml
name: harness go — rate limit při objevování, resume nevolá hotové projekty znovu
tasks: []
discover:
  demo:
    proposals:
      - {title: "Testy pro parser", kind: test, done: "npm test zelený"}
  web:
    - {rate_limit: 1}
    - proposals:
        - {title: "Opravit odkaz v patičce", kind: bug, done: "odkaz vede na /kontakt"}
delay: 0.5
```

- [ ] **Step 2: Testy (padají)** — do `tests/test_scenarios.py`:

```python
from harness.discover import proposal_id
from harness.runner import approvals


def test_go_happy_runs_approved_plan(tmp_path):
    sim, conn, status = run("go-happy", tmp_path)
    assert status == "approving"
    asked = approvals(sim.app, sim.config)[0]
    assert [p["title"] for p in asked["plan"]] == ["Testy pro parser", "Opravit odkaz v patičce", "Ruční úkol z BACKLOGu"]
    status = execute(sim.app, None, sim.config, conn, simulated=True, answers={asked["id"]: [0, 1, 2]})
    assert status == "done"
    assert statuses(sim) == {"sim-01": "x", proposal_id("demo", "Testy pro parser"): "x",
                             proposal_id("web", "Opravit odkaz v patičce"): "x",
                             proposal_id("web", "Přepsat celý frontend"): "~"}
    assert started(conn, sim)[:4] == ["discover", "discover", "prioritize", "approve"]


def test_go_struck_task_is_rejected_and_not_run(tmp_path):
    sim, conn, _ = run("go-happy", tmp_path)
    asked = approvals(sim.app, sim.config)[0]
    execute(sim.app, None, sim.config, conn, simulated=True, answers={asked["id"]: [0, 2]})
    assert statuses(sim)[proposal_id("web", "Opravit odkaz v patičce")] == "-"
    assert sim.script.calls["open_pr"] == 2


def test_go_reject_all_parks_proposals(tmp_path):
    sim, conn, status = run("go-reject-all", tmp_path)
    asked = approvals(sim.app, sim.config)[0]
    assert execute(sim.app, None, sim.config, conn, simulated=True, answers={asked["id"]: "q"}) == "done"
    assert statuses(sim) == {proposal_id("demo", "Testy pro parser"): "~"}
    assert "preflight" not in started(conn, sim)


def test_go_discover_rate_limit_resumes_only_limited_project(tmp_path):
    sim, conn, status = run("go-discover-rate-limit", tmp_path)
    assert status == "paused"
    assert execute(sim.app, None, sim.config, conn, simulated=True) == "approving"
    assert sim.script.calls["discover"] == 3  # demo once, web twice
    assert len(approvals(sim.app, sim.config)[0]["plan"]) == 2
```

Do `tests/test_cli.py`:

```python
def test_simulate_go_asks_for_plan_approval_until_valid(tmp_path, capsys, monkeypatch):
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    answers = iter(["9", "2"])
    monkeypatch.setattr("builtins.input", lambda p: next(answers))
    assert main(["--home", str(tmp_path), "simulate", "go-happy", "--delay", "0"]) == 0
    out = capsys.readouterr().out
    assert "1. [demo] test Testy pro parser (nový)" in out
    assert "neplatná volba: 9" in out
    assert "[-] P2 web-ai-" in out and "[x] P2 sim-01" in out
```

- [ ] **Step 3: Spustit** `.venv/Scripts/python -m pytest tests/test_scenarios.py tests/test_cli.py -q` → FAIL (`ImportError: approvals`).

- [ ] **Step 4: `harness/runner.py`:**

```python
def approvals(app, config: dict) -> list[dict]:
    return [{"id": i.id, **i.value} for i in open_interrupts(app, config)
            if isinstance(i.value, dict) and "plan" in i.value]
```
V `execute` změnit signaturu na `answers: dict[str, object] | None = None`, docstring doplnit o `approving` (odpověď = seznam ponechaných indexů nebo "q"), a za blok `questions` přidat:
```python
    if plans := approvals(app, config):
        logger.log("waiting", delta={"plan": plans[0]["plan"]})
        return "approving"
```
V `_Logger.debug` vložit před řádek `worker = None` (paralelní `discover` se v event logu/dashboardu rozliší projektem ve sloupci `worker`; chyby jdou dál obecnou cestou):
```python
        if not ns and payload["name"] == "discover" and not payload.get("error"):
            project = (payload.get("input") or {}).get("project")
            if ev["type"] == "task":
                self.log("start", node="discover", worker=project)
            else:
                result = payload.get("result") or {}
                if not isinstance(result, dict):
                    result = {}
                self.log("end", node="discover", worker=project,
                         delta={"proposals": len(result.get("proposals") or []), "go_errors": result.get("go_errors")},
                         usage=result.get("usage") or None)
            return
```

- [ ] **Step 5: `harness/nodes/fake.py`** (+ `import json`, `from typing import Callable`):

```python
def fake_explore(script: Script) -> Callable:
    """Scenario `discover: {project: {proposals: [...]}}`; a list value advances per call of that project."""
    def explore(project: str, known) -> tuple[str, list[dict]]:
        v = script.next("discover", project)
        return json.dumps(v.get("proposals", []), ensure_ascii=False), _usage("discover", "opus")
    return explore


def fake_rank(script: Script) -> Callable:
    """Scenario `nodes: {prioritize: {pick: [1-based candidate indexes]}}`; default = all in candidate order."""
    def rank_fn(cands: list[dict], n: int, focus: dict, weights: dict) -> tuple[str, list[dict]]:
        v = script.next("prioritize")
        picked = [cands[i - 1] for i in v.get("pick", range(1, len(cands) + 1))]
        return json.dumps([{"id": c["id"], "why": "simulace"} for c in picked]), _usage("prioritize", "opus")
    return rank_fn
```

- [ ] **Step 6: `harness/simulate.py::prepare`** — projekty i z klíče `discover`, uzly go, `discover` v počátečním stavu:
```python
    discover = sc.get("discover") or {}
    names = [*dict.fromkeys([t["project"] for t in sc["tasks"]] + list(discover))]
    projects = {n: Project(n, workdir, "main", (), ("true",), (), sc.get("max_attempts", 3), 800) for n in names}
    cfg = Config("subscription", sc.get("max_tasks", 3), 0.7, projects, sc.get("max_parallel", 2))
    per_task = {t["id"]: t["nodes"] for t in sc["tasks"] if t.get("nodes")}
    per_task |= {name: {"discover": v} for name, v in discover.items()}
    script = Script(sc.get("nodes") or {}, sc.get("delay", 0) if delay is None else delay, per_task)
    nodes = fake_nodes(script) | {"pick_tasks": make_pick_tasks(backlog_path, cfg), "report": make_report(backlog_path),
                                  "discover": make_discover(backlog_path, fake_explore(script)),
                                  "prioritize": make_prioritize(backlog_path, cfg, fake_rank(script)),
                                  "approve": make_approve(backlog_path)}
    ...
    return Simulation(app, run_config(run_id), run_id, initial_state(run_id, cfg, True, discover=bool(discover)),
                      script, backlog_path, cfg)
```
(`sc["tasks"]` může být `[]`; `_render_backlog([])` vrací `""`.)

- [ ] **Step 7: `harness/cli.py`:**

```python
def ask_approval(app, config: dict) -> dict:
    out = {}
    for a in approvals(app, config):
        print(format_plan(a["plan"]))
        while True:
            try:
                out[a["id"]] = parse_approval(input("Enter = vše, čísla = vyškrtnout (2,5), q = konec > "),
                                              len(a["plan"]))
                break
            except ValueError as e:
                print(e)
    return out


def answer_in_terminal(app, config: dict, status: str) -> dict:
    return ask_approval(app, config) if status == "approving" else ask_in_terminal(app, config)
```
`cmd_simulate`: smyčka `while status in ("interrupted", "waiting", "approving"):` a větev `if status in ("waiting", "approving"): status = execute(..., answers=answer_in_terminal(sim.app, sim.config, status))`.

`cmd_run` → sdílená `_run(args, discover: bool)`; `cmd_run = lambda args: _run(args, False)` jako funkce, `cmd_go(args) -> _run(args, True)`:
```python
def _run(args, discover: bool) -> int:
    cfg = load_config(args.home / "projects.yaml")
    # preflight checks this per task, but --resume jumps straight back into implement
    if err := check_billing(cfg.billing, os.environ):
        print(err, file=sys.stderr)
        return 2
    app, backlog_path, conn = _real_app(args.home, cfg)
    prefix = "go" if discover else "run"
    run_id = getattr(args, "resume", None) or f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"
    inputs = None if getattr(args, "resume", None) else initial_state(run_id, cfg, simulated=False, discover=discover)
    config = run_config(run_id)
    status = execute(app, inputs, config, conn, simulated=False)
    while status in ("waiting", "approving"):
        status = execute(app, None, config, conn, simulated=False, answers=answer_in_terminal(app, config, status))
    for err in app.get_state(config).values.get("go_errors") or []:
        print(f"go: {err}", file=sys.stderr)
    print(f"{run_id} {status}")
    if status == "paused":
        print(f"resume: harness run --resume {run_id}")
    return 0


def cmd_run(args) -> int:
    return _run(args, discover=False)


def cmd_go(args) -> int:
    return _run(args, discover=True)
```
`main`: `sub.add_parser("go")`, `handlers["go"] = cmd_go`. Importy: `approvals` z runneru, `format_plan, parse_approval` z `.discover`.

- [ ] **Step 8: Spustit** `.venv/Scripts/python -m pytest -q` → vše PASS. Ručně: `.venv/Scripts/harness simulate go-happy --delay 0` → plán, Enter → `done`, BACKLOG se 3× `[x]` a 1× `[~]`.
- [ ] **Step 9: Commit** — CHANGELOG `## [2026-10-07] - harness go v terminálu: schválení plánu, simulace (go-happy, go-reject-all, go-discover-rate-limit)`; `git add harness/runner.py harness/nodes/fake.py harness/simulate.py harness/cli.py scenarios/go-*.yaml tests/test_scenarios.py tests/test_cli.py CHANGELOG.md`, `git commit -m "feat: harness go command with terminal plan approval and scenarios"`.

---

### Task 4: Reálné `explore` / `rank_fn` + prompty

**Files:**
- Create: `harness/prompts/discover.md`, `harness/prompts/prioritize.md`
- Modify: `harness/nodes/real.py`, `harness/cli.py::_real_app`
- Test: `tests/test_go_real.py`

**Interfaces:**
- Consumes: `sdk.run_agent`, `sdk.render_prompt`, `ensure_worktree`, `git`, `priority.captures_about`, `_agent_out`.
- Produces: `real.real_explore(cfg: Config) -> Callable[[str, list[Task]], tuple[str, list[dict]]]`, `real.real_rank(cands, n, focus, weights) -> tuple[str, list[dict]]`, `real.DISCOVER_WT = "_discover"`.

- [ ] **Step 1: Testy (padají)** — `tests/test_go_real.py`:

```python
import json
from pathlib import Path

from harness.backlog import Task
from harness.nodes import real
from harness.nodes.real import git
from harness.projects import Config, Project
from harness.sdk import WorkerResult
from tests.test_real_git import make_repo


def fake_agent(calls, text="[]"):
    def run_agent(prompt, **kw):
        calls.append({"prompt": prompt, **kw})
        return WorkerResult(text, {"model": kw["model"], "input_tokens": 1, "output_tokens": 1, "cost_usd": 0}, 0.1, None)
    return run_agent


def test_explore_reads_fresh_origin_with_context(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    brain = tmp_path / "brain" / "Thoughts"
    brain.mkdir(parents=True)
    from datetime import date
    (brain / f"{date.today().isoformat()} x.md").write_text("demo potřebuje export do CSV", encoding="utf-8")
    p = Project("demo", repo, "main", (), ("npm test",), (), 3, 800)
    cfg = Config("subscription", 3, 0.7, {"demo": p}, secondbrain=(tmp_path / "brain", 14))
    calls = []
    monkeypatch.setattr(real, "run_agent", fake_agent(calls, '[{"title": "X"}]'))
    explore = real.real_explore(cfg)
    known = [Task("d1", "demo", 2, "Starý úkol", "-", "")]
    text, usage = explore("demo", known)
    assert text == '[{"title": "X"}]' and usage[0]["node"] == "discover"
    c = calls[0]
    assert c["model"] == "opus" and c["tools"] == real.READ_TOOLS and c["max_turns"] == 40
    assert "npm test" in c["prompt"] and "Starý úkol" in c["prompt"] and "export do CSV" in c["prompt"]
    # a new commit on origin is visible on the next discover (worktree reset, not reused stale)
    wt = Path(c["cwd"])
    (repo / "b.txt").write_text("b\n")
    git(["add", "-A"], repo)
    git(["commit", "-q", "-m", "b"], repo)
    git(["push", "-q", "origin", "main"], repo)
    explore("demo", known)
    assert (wt / "b.txt").exists()


def test_rank_sends_candidates_and_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(real, "run_agent", fake_agent(calls, '[{"id": "a"}]'))
    cands = [{"id": "a", "project": "demo", "title": "{nebezpečné}", "kind": "test", "why": "w", "done": "d",
              "source": "new", "priority": 2, "description": "dlouhý popis"}]
    text, usage = real.real_rank(cands, 3, {"demo": 2}, {"demo": 1})
    assert text == '[{"id": "a"}]' and usage[0]["node"] == "prioritize"
    c = calls[0]
    assert c["tools"] == [] and c["max_turns"] == 1 and c["model"] == "opus"
    assert "at most 3 tasks" in c["prompt"] and '"id": "a"' in c["prompt"] and "{nebezpečné}" in c["prompt"]
```

- [ ] **Step 2: Spustit** `.venv/Scripts/python -m pytest tests/test_go_real.py -q` → FAIL (`AttributeError: real_explore`).

- [ ] **Step 3: Prompty.**

`harness/prompts/discover.md`:
```
You are the task scout of an automated coding harness. The current directory is a fresh read-only checkout of the project `{project}` (state of origin).

Find up to 5 valuable, independent tasks. Sources, most important first:
1. The owner's recent notes about this project (below) — what they actually want.
2. The project's own docs: README, roadmaps, specs, plans, CHANGELOG — unfinished or planned work.
3. The code: missing tests, bugs, tech debt, TODO/FIXME.

Every task must:
- fit one pull request of at most {max_diff_lines} changed lines and not depend on the other tasks,
- be verifiable by the project's checks:
{verify}
- not repeat an existing backlog task (including done or rejected ones):
{known}

Owner's notes:
{notes}

Explore read-only. Then answer with ONLY a JSON array and no other text:
[{{"title": "...", "description": "...", "kind": "test|bug|debt|feature|docs", "why": "...", "done": "..."}}]
Write title, description, why and done in Czech. title: at most 80 characters. description: what to change and where (files). done: a concrete check that proves the task is finished. Fewer strong tasks beat five weak ones; [] is a valid answer.
```

`harness/prompts/prioritize.md`:
```
You are the orchestrator of an automated coding harness. Choose and order at most {n} tasks for this run from the candidates below (one JSON object per line).

Weigh:
- the owner's focus (how often each project appears in their recent notes): {focus}
- project weights: {weights}
- explicit backlog priority (1 = highest; new proposals are 2)
- value for the owner against risk; prefer tasks a worker can finish and verify without asking the owner
- workers run in parallel, one task per project at a time — spread across projects when value is similar

Candidates:
{candidates}

Answer with ONLY a JSON array ordered from most to least important:
[{{"id": "...", "why": "one short Czech sentence"}}]
```

- [ ] **Step 4: `harness/nodes/real.py`** (+ `import json`, `from typing import Callable`, `from ..backlog import Task`, `from ..priority import captures_about`):

```python
DISCOVER_WT = "_discover"
RANK_FIELDS = ("id", "project", "title", "kind", "why", "done", "source", "priority")


def _fresh_discover_worktree(p: Project) -> Path:
    """Read-only checkout of origin/<base>; reused between runs, so reset it to the latest origin every time."""
    wt = ensure_worktree(p, DISCOVER_WT, f"harness/{DISCOVER_WT}")
    git(["fetch", "-q", "origin", p.base], p.path)
    git(["reset", "-q", "--hard", f"origin/{p.base}"], wt)
    return wt


def real_explore(cfg: Config) -> Callable[[str, list[Task]], tuple[str, list[dict]]]:
    def explore(name: str, known: list[Task]) -> tuple[str, list[dict]]:
        p = cfg.projects[name]
        wt = _fresh_discover_worktree(p)
        notes = captures_about(cfg.secondbrain[0], name, cfg.secondbrain[1]) if cfg.secondbrain else ""
        prompt = render_prompt("discover.md", project=name, max_diff_lines=p.max_diff_lines,
                               verify="\n".join(f"  {c}" for c in p.verify),
                               known="\n".join(f"  [{t.status}] {t.title}" for t in known) or "  (none)",
                               notes=notes or "(none)")
        r = run_agent(prompt, cwd=wt, model="opus", tools=READ_TOOLS, permission_mode="dontAsk", max_turns=40)
        return r.text, _agent_out("discover", r)["usage"]
    return explore


def real_rank(cands: list[dict], n: int, focus: dict, weights: dict) -> tuple[str, list[dict]]:
    rows = "\n".join(json.dumps({k: c[k] for k in RANK_FIELDS}, ensure_ascii=False) for c in cands)
    prompt = render_prompt("prioritize.md", n=n, candidates=rows,
                           focus=json.dumps(focus, ensure_ascii=False), weights=json.dumps(weights, ensure_ascii=False))
    r = run_agent(prompt, cwd=tempfile.gettempdir(), model="opus", tools=[], permission_mode="dontAsk", max_turns=1)
    return r.text, _agent_out("prioritize", r)["usage"]
```
Pozn.: `render_prompt` formátuje jen šablonu; `{…}` v dosazených hodnotách (titulky) se znovu neformátuje — test to hlídá.

- [ ] **Step 5: `harness/cli.py::_real_app`** — dočasné lambdy z Tasku 2 nahradit:
```python
    nodes = real_nodes(cfg) | {"pick_tasks": make_pick_tasks(backlog_path, cfg), "report": make_report(backlog_path),
                               "discover": make_discover(backlog_path, real_explore(cfg)),
                               "prioritize": make_prioritize(backlog_path, cfg, real_rank),
                               "approve": make_approve(backlog_path)}
```

- [ ] **Step 6: Spustit** `.venv/Scripts/python -m pytest -q` → vše PASS.
- [ ] **Step 7: Commit** — CHANGELOG `## [2026-10-07] - harness go: reálný průzkum projektů (Opus, čerstvý worktree z origin, poznámky secondbrainu) a prioritizace`; `git add harness/prompts/discover.md harness/prompts/prioritize.md harness/nodes/real.py harness/cli.py tests/test_go_real.py CHANGELOG.md`, `git commit -m "feat: real discover and prioritize via opus"`.

---

### Task 5: Docs a ověření naostro

**Files:**
- Modify: `README.md`, `docs/specs/2026-10-07-harness-go-design.md` (stav), `CHANGELOG.md`, `.gitignore` netřeba (`.harness-worktrees` je mimo repo)

- [ ] **Step 1: README** — do bloku příkazů přidat `.venv/Scripts/harness go                 # Opus navrhne a seřadí úkoly, schválíš plán, workery udělají draft PR` a krátký odstavec „`harness go`“: tok discover → prioritize → schválení (Enter / `2,5` / `q`), stavy `[ ]`/`[-]`/`[~]`, resume `harness run --resume go-…`, simulace `harness simulate go-happy`.
- [ ] **Step 2: Ověření bez modelu** — `.venv/Scripts/python -m pytest -q` (vše PASS, zapsat počet) a `.venv/Scripts/harness simulate go-happy --delay 0` (Enter → `done`).
- [ ] **Step 3: Ostrý běh (spotřebuje subscription kvótu — spustit až po souhlasu uživatele)** — `.venv/Scripts/harness go`: plán ≥3 smysluplné úkoly → schválit → ≥1 draft PR se zeleným verify. Výsledek (PR odkazy, tokeny z dashboardu, co model navrhl a jestli to dávalo smysl) zapsat do CHANGELOG a stavu ve spec.
- [ ] **Step 4: Commit** — `docs: harness go usage and first real run`.
