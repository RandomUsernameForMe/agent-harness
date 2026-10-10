import pytest

from harness.backlog import add_tasks, parse, set_status

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


def test_unsafe_id_is_not_a_task():
    assert parse("## p\n- [ ] P1 a;rm-rf Bad\n") == []


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


def test_proposal_status_parsed_and_title_kept():
    [t] = parse("## p\n- [~] P3 p-todo-abc123 TODO: fix — later\n  src/a.ts:3\n")
    assert (t.status, t.title, t.description) == ("~", "TODO: fix — later", "src/a.ts:3")


def test_kind_tag_parsed_and_kept_by_set_status():
    text = "## p\n- [~] P2 p-ai-1 [feature] Export do CSV\n- [ ] P2 p-2 [WIP] Ruční\n"
    t1, t2 = parse(text)
    assert (t1.kind, t1.title) == ("feature", "Export do CSV")
    assert (t2.kind, t2.title) == ("", "[WIP] Ruční")
    done = parse(set_status(text, "p-ai-1", "x", " → https://example.com/pull/1"))[0]
    assert (done.kind, done.title, done.note) == ("feature", "Export do CSV", "https://example.com/pull/1")


def test_add_tasks_appends_to_existing_section():
    text = "## a\n- [ ] P1 a-1 A\n  desc\n\n## b\n- [ ] P1 b-1 B\n"
    out = add_tasks(text, "a", ["- [~] P3 a-2 New", "  where"])
    assert out == "## a\n- [ ] P1 a-1 A\n  desc\n- [~] P3 a-2 New\n  where\n\n## b\n- [ ] P1 b-1 B\n"


def test_add_tasks_creates_section_and_keeps_crlf():
    out = add_tasks("## a\r\n- [ ] P1 a-1 A\r\n", "c", ["- [~] P3 c-1 New"])
    assert out == "## a\r\n- [ ] P1 a-1 A\r\n\r\n## c\r\n- [~] P3 c-1 New\r\n"
    assert parse(out)[-1].project == "c"


def test_add_tasks_to_empty_file():
    assert add_tasks("", "c", ["- [~] P3 c-1 New"]) == "## c\n- [~] P3 c-1 New\n"


def test_rejected_status_parsed_and_never_ranked():
    from harness.priority import rank
    text = set_status("## demo\n- [~] P3 d-1 TODO: x\n", "d-1", "-", " — zamítnuto")
    [t] = parse(text)
    assert (t.status, t.title) == ("-", "TODO: x")
    assert rank([t], {"demo"}) == []


def test_edit_backlog_keeps_bytes(tmp_path):
    from harness.nodes.common import edit_backlog
    p = tmp_path / "BACKLOG.md"
    p.write_bytes("## demo\r\n- [~] P3 d-1 Návrh\r\n".encode("utf-8"))
    edit_backlog(p, lambda text: set_status(text, "d-1", " "))
    assert p.read_bytes().decode("utf-8") == "## demo\r\n- [ ] P3 d-1 Návrh\r\n"
