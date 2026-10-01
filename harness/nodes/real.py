import os
import subprocess
import tempfile
from pathlib import Path

from ..guard import check_diff, new_failures
from ..projects import Config, Project
from ..sdk import NO_PUBLISH, READ_TOOLS, WRITE_TOOLS, check_billing, render_prompt, run_agent


def _kill_tree(proc: subprocess.Popen) -> None:
    # subprocess timeout kills only cmd.exe; grandchildren (node/vitest) keep the pipes open forever
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def sh(cmd: str, cwd, timeout: int = 900) -> tuple[bool, str]:
    proc = subprocess.Popen(cmd, cwd=cwd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        return False, f"{out}\n[harness] timeout after {timeout}s: {cmd}"
    return proc.returncode == 0, out


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


def staged_changes(worktree) -> tuple[list[str], int]:
    """Stage everything; return changed paths (both sides of moves, unquoted) and diff line count."""
    git(["add", "-A"], worktree)
    names = git(["diff", "--cached", "--no-renames", "-z", "--name-only"], worktree)
    numstat = git(["diff", "--cached", "--no-renames", "--numstat"], worktree)
    return [p for p in names.split("\0") if p], numstat_lines(numstat)


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
        changed, lines = staged_changes(wt)
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
        # body via file: Windows caps a command line at 32k chars and a long plan would exceed it
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.md"
            body.write_text(_pr_body(s), encoding="utf-8")
            out = subprocess.run(["gh", "pr", "create", "--draft", "--base", p.base, "--head", s["branch"],
                                  "--title", f"[harness] {t['title']}", "--body-file", str(body)],
                                 cwd=wt, check=True, capture_output=True, text=True, encoding="utf-8").stdout
        return {"outcome": "pr_opened", "pr_url": out.strip().splitlines()[-1]}

    return {"preflight": preflight, "make_worktree": make_worktree, "plan": plan, "setup": setup,
            "implement": implement, "verify": verify, "open_pr": open_pr}
