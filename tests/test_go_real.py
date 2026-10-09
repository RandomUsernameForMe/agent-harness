from datetime import date
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
    (brain / f"{date.today().isoformat()} x.md").write_text("demo potřebuje export do CSV", encoding="utf-8")
    p = Project("demo", repo, "main", (), ("npm test",), (), 3, 800)
    cfg = Config("subscription", 3, 0.7, {"demo": p}, secondbrain=(tmp_path / "brain", 14))
    calls = []
    monkeypatch.setattr(real, "run_agent", fake_agent(calls, '[{"title": "X"}]'))
    explore = real.real_explore(cfg)
    known = [Task("d1", "demo", 2, "Starý úkol", "-", "")]
    text, agent = explore("demo", known)
    assert text == '[{"title": "X"}]' and agent["usage"][0]["node"] == "discover" and agent["quota"] == 0.1
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
    text, agent = real.real_rank(cands, 3, {"demo": 2}, {"demo": 1})
    assert text == '[{"id": "a"}]' and agent["usage"][0]["node"] == "prioritize" and agent["quota"] == 0.1
    c = calls[0]
    assert c["tools"] == [] and c["max_turns"] == 1 and c["model"] == "opus"
    assert "at most 3 tasks" in c["prompt"] and '"id": "a"' in c["prompt"] and "{nebezpečné}" in c["prompt"]


def test_rank_gets_owner_history(monkeypatch):
    calls = []
    monkeypatch.setattr(real, "run_agent", fake_agent(calls))
    real.real_rank([], 3, {}, {}, history_of=lambda: {"demo": {"zamítnuto": 4}})
    assert '{"demo": {"zamítnuto": 4}}' in calls[0]["prompt"]


def test_reflect_sends_feedback_and_known_lessons(monkeypatch):
    calls = []
    monkeypatch.setattr(real, "run_agent", fake_agent(calls, '[{"project": "demo", "text": "L"}]'))
    items = [{"key": "rejected:d-1", "project": "demo", "task": "d-1", "kind": "rejected", "text": "Testy {lib}"}]
    text, agent = real.real_reflect(items, [{"project": "demo", "text": "stará lekce", "status": "retired"}])
    assert text == '[{"project": "demo", "text": "L"}]' and agent["usage"][0]["node"] == "reflect"
    c = calls[0]
    assert c["tools"] == [] and c["max_turns"] == 1 and c["model"] == "opus"
    assert '"key": "rejected:d-1"' in c["prompt"] and "Testy {lib}" in c["prompt"] and "stará lekce" in c["prompt"]
