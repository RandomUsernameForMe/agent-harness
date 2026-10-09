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


def test_merge_pr_readies_draft_then_drops_worktree_and_branches(tmp_path, monkeypatch):
    from harness import traces
    repo = make_repo(tmp_path)
    wt = ensure_worktree(project(repo), "t-1", "harness/t-1")
    git(["push", "-q", "-u", "origin", "harness/t-1"], wt)
    calls = []

    def gh(args, cwd):
        calls.append(args[:2])
        out = '{"state": "OPEN", "isDraft": true, "headRefName": "harness/t-1"}' if args[1] == "view" else ""
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(traces, "_gh", gh)
    assert traces.merge_pr(repo, 5) == "PR #5 zmergován (squash)."
    assert calls == [["pr", "view"], ["pr", "ready"], ["pr", "merge"]]
    assert not wt.exists()
    assert git(["branch", "--list", "harness/t-1"], repo) == ""
    assert git(["ls-remote", "--heads", "origin", "harness/t-1"], repo) == ""


def test_merge_pr_refuses_closed_and_keeps_user_worktree(tmp_path, monkeypatch):
    import pytest

    from harness import traces
    repo = make_repo(tmp_path)
    user_wt = tmp_path / "mine"
    git(["worktree", "add", "-q", "-b", "feat", str(user_wt)], repo)
    view = {"state": "CLOSED"}
    monkeypatch.setattr(traces, "_gh", lambda args, cwd: subprocess.CompletedProcess(
        args, 0, '{"state": "%s", "isDraft": false, "headRefName": "feat"}' % view["state"], ""))
    with pytest.raises(RuntimeError, match="closed"):
        traces.merge_pr(repo, 5)
    view["state"] = "OPEN"
    traces.merge_pr(repo, 5)
    assert user_wt.exists()  # only harness worktrees are force-removed
