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


def test_serve_needs_telegram_env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HARNESS_TELEGRAM_TOKEN", raising=False)
    assert main(["--home", str(tmp_path), "serve"]) == 2
    assert "HARNESS_TELEGRAM_TOKEN" in capsys.readouterr().err


def test_serve_starts_and_stops_cleanly(tmp_path, monkeypatch, capsys):
    from harness import serve
    (tmp_path / "projects.yaml").write_text('serve: {window: "22:00-07:00"}\nprojects: {}\n', encoding="utf-8")
    monkeypatch.setenv("HARNESS_TELEGRAM_TOKEN", "T")
    monkeypatch.setenv("HARNESS_TELEGRAM_CHAT_ID", "42")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sent = []
    monkeypatch.setattr("harness.telegram.Telegram.send", lambda self, text, buttons=None: sent.append((self.chat_id, text)) or 1)

    def stop(ctx):
        raise KeyboardInterrupt
    monkeypatch.setattr(serve, "loop", stop)
    assert main(["--home", str(tmp_path), "serve"]) == 0
    assert sent and sent[0][0] == 42
    assert "22:00–07:00" in capsys.readouterr().out
    assert (tmp_path / "serve_state.json").exists()


def test_simulate_go_asks_for_plan_approval_until_valid(tmp_path, capsys, monkeypatch):
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    answers = iter(["9", "2"])
    monkeypatch.setattr("builtins.input", lambda p: next(answers))
    assert main(["--home", str(tmp_path), "simulate", "go-happy", "--delay", "0"]) == 0
    out = capsys.readouterr().out
    assert "1. [demo] test Testy pro parser (nový)" in out
    assert "neplatná volba: 9" in out
    assert "[-] P2 web-ai-" in out and "[x] P2 sim-01" in out


def test_stats_syncs_outcomes_and_prints_costs(tmp_path, capsys, monkeypatch):
    from harness import events, traces
    (tmp_path / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text("## akano3\n- [x] P1 ak-01 Testy → https://gh/pr/1\n", encoding="utf-8")
    conn = events.connect(tmp_path / "harness.db")
    base = {"run_id": "go-1", "model": "opus", "input_tokens": 10, "output_tokens": 90, "cache_read_tokens": 900,
            "cache_write_tokens": 0, "status": "ok"}
    traces.record(conn, base | {"worker": "akano3", "node": "discover", "cost_usd": 0.4}, [])
    traces.record(conn, base | {"worker": "ak-01", "node": "implement", "cost_usd": 0.6}, [])
    monkeypatch.setattr(traces, "pr_state", lambda url: "merged")
    assert main(["--home", str(tmp_path), "stats"]) == 0
    out = capsys.readouterr().out
    assert "ak-01" in out and "merged" in out and "0.60" in out and "https://gh/pr/1" in out
    assert "go-1 discover" in out and "0.40" in out and "celkem 1.00 $" in out
