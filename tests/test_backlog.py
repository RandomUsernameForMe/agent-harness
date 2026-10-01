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
