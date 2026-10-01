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


def test_simulate_survives_cp1252_console(tmp_path):
    import os
    import subprocess
    import sys
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    p = subprocess.run([sys.executable, "-m", "harness.cli", "--home", str(tmp_path), "simulate", "happy-path", "--delay", "0"],
                       capture_output=True, env=env)
    assert p.returncode == 0, p.stderr.decode(errors="replace")


def test_simulate_unknown(tmp_path):
    (tmp_path / "scenarios").mkdir()
    assert main(["--home", str(tmp_path), "simulate", "nope"]) == 2


def test_queue_ranks_with_secondbrain_focus(tmp_path, capsys):
    brain = tmp_path / "brain" / "Thoughts"
    brain.mkdir(parents=True)
    import datetime
    (brain / f"{datetime.date.today()}.md").write_text("dnes beta", encoding="utf-8")
    (tmp_path / "projects.yaml").write_text(
        "secondbrain: {path: brain}\nprojects:\n  alpha: {path: ., base: main, verify: [x]}\n"
        "  beta: {path: ., base: main, verify: [x]}\n", encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text("## alpha\n- [ ] P1 a-1 A\n## beta\n- [ ] P1 b-1 B\n- [~] P1 b-2 navrh\n",
                                         encoding="utf-8")
    assert main(["--home", str(tmp_path), "queue"]) == 0
    out = capsys.readouterr().out
    assert "alpha=0, beta=1" in out
    assert out.index("b-1") < out.index("a-1") and "b-2" not in out


def test_scan_write_appends_proposals_once(tmp_path, capsys):
    from tests.test_real_git import make_repo
    from tests.test_scan import commit_files
    repo = make_repo(tmp_path)
    commit_files(repo, {"a.py": "# FIXME: crash on empty input\n"})
    (tmp_path / "projects.yaml").write_text(f"projects:\n  demo: {{path: '{repo.as_posix()}', base: main, verify: [x]}}\n",
                                            encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text("## demo\n- [x] P1 d-1 hotovo\n", encoding="utf-8")
    assert main(["--home", str(tmp_path), "scan", "--write"]) == 0
    text = (tmp_path / "BACKLOG.md").read_text(encoding="utf-8")
    assert "- [~] P3 demo-todo-" in text and "FIXME: crash on empty input" in text
    assert main(["--home", str(tmp_path), "scan", "--write"]) == 0
    assert (tmp_path / "BACKLOG.md").read_text(encoding="utf-8") == text
    assert "no new proposals" in capsys.readouterr().out
    assert main(["--home", str(tmp_path), "scan", "--project", "nope"]) == 2


def test_simulate_asks_unclear_question_in_terminal(tmp_path, capsys, monkeypatch):
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "měř LCP")
    assert main(["--home", str(tmp_path), "simulate", "clarify-answered", "--delay", "0"]) == 0
    assert prompts and "Co přesně měřit?" in prompts[0]
    assert "[x] P1 sim-01" in capsys.readouterr().out
