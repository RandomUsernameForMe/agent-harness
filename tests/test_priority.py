from datetime import date

from harness.backlog import parse
from harness.priority import focus, pick_batch, rank

TASKS = """## a
- [ ] P2 a-1 A
- [ ] P1 a-2 B
- [x] P1 a-3 done
- [~] P1 a-4 proposal
## b
- [ ] P1 b-1 C
- [ ] P2 b-2 D
## c
- [ ] P1 c-1 E
"""


def ids(tasks):
    return [t.id for t in tasks]


def test_rank_priority_then_file_order():
    assert ids(rank(parse(TASKS), {"a", "b", "c"})) == ["a-2", "b-1", "c-1", "a-1", "b-2"]


def test_rank_skips_closed_proposals_and_unknown_projects():
    assert ids(rank(parse(TASKS), {"a"})) == ["a-2", "a-1"]
    assert rank(parse(TASKS), {"zzz"}) == []


def test_focus_breaks_ties_before_weight():
    ranked = rank(parse(TASKS), {"a", "b", "c"}, focus={"c": 2, "b": 1}, weights={"b": 5})
    assert ids(ranked) == ["c-1", "b-1", "a-2", "b-2", "a-1"]


def test_weight_breaks_ties_without_focus():
    assert ids(rank(parse(TASKS), {"a", "b", "c"}, weights={"c": 1}))[:3] == ["c-1", "a-2", "b-1"]


def test_pick_batch_one_per_project():
    ranked = rank(parse(TASKS), {"a", "b", "c"})
    assert ids(pick_batch(ranked, 2)) == ["a-2", "b-1"]
    assert ids(pick_batch(ranked, 5)) == ["a-2", "b-1", "c-1"]
    assert pick_batch(ranked, 0) == []


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_focus_counts_recent_files_mentioning_project(tmp_path):
    write(tmp_path, "Thoughts/2026-09-30.md", "- **10:00** guth pictures potřebuje galerii\n- guth-pictures znovu\n")
    write(tmp_path, "Thoughts/2026-09-25.md", "Guth-Pictures a akano3\n")
    write(tmp_path, "Inbox/2026-09-29T08-00.md", "nápad na AKANO3 web")
    write(tmp_path, "Thoughts/2026-08-01.md", "guth-pictures staré")       # older than 14 days
    write(tmp_path, "Subjects/Digital Projects.md", "guth-pictures akano3")  # not a dated capture
    write(tmp_path, "Thoughts/notes.md", "guth-pictures")                  # undated name
    write(tmp_path, "Thoughts/2026-09-30-b.md", "secondbrainy")            # prefix of a name ≠ mention
    got = focus(tmp_path, ["guth-pictures", "akano3", "secondbrain"], days=14, today=date(2026, 10, 1))
    assert got == {"guth-pictures": 2, "akano3": 2, "secondbrain": 0}


def test_focus_missing_dir_is_empty(tmp_path):
    assert focus(tmp_path / "nope", ["a"], days=14, today=date(2026, 10, 1)) == {"a": 0}


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
