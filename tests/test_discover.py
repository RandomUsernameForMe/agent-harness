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


def test_parse_proposals_kind_follows_lens():
    text = '[{"title": "Export do CSV", "kind": "bug"}, {"title": "Nový dashboard", "kind": "feature"}]'
    assert [p["kind"] for p in parse_proposals(text, "demo", [], "feature")] == ["feature", "feature"]
    assert [p["kind"] for p in parse_proposals(text, "demo", [])] == ["bug", "debt"]


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
    text = (f'[{{"id": "{ids[2]}", "why": "první"}}, {{"id": "zzz"}}, {{"id": "{ids[2]}"}},'
            f' {{"id": "{ids[0]}"}}, {{"id": "{ids[1]}"}}]')
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
    assert f"- [ ] P2 {p[0]['id']} [test] Schválený\n  d\n  Hotovo = ok\n" in text
    assert {t.kind for t in backlog.parse(text) if t.id != "t1"} == {"test"}


def test_apply_approval_quit_parks_everything():
    p = props("A", "B")
    text = apply_approval("", p, p, None)
    assert {t.status for t in backlog.parse(text)} == {"~"}


def test_format_plan():
    plan = [props("A")[0], candidates([], [task("t1", "Ruční")], {"demo"})[0] | {"why": "proč"}]
    out = format_plan(plan)
    assert "1. [demo] test A (nový)" in out
    assert "2. [demo] - Ruční (z BACKLOGu)" in out and "proč" in out


def test_json_array_skips_bracketed_prose():
    assert json_array('I read [README.md] and found:\n[{"title": "a"}]') == [{"title": "a"}]
    assert json_array('[{"title": "a"}]\nSee [README] for more') == [{"title": "a"}]
    assert json_array('footnote [1], then [{"title": "a"}]') == [{"title": "a"}]
    assert json_array("Nothing to do: []") == []


def test_parse_ranking_ignores_unhashable_ids():
    cands = candidates(props("A"), [], {"demo"})
    assert parse_ranking('[{"id": ["x"]}, {"id": {"a": 1}}]', cands, 2) == []
