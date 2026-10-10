import subprocess

from harness.graph import same_failure
from harness.nodes import real
from harness.nodes.real import parse_accept
from harness.projects import Config, Project
from tests.test_scenarios import run, statuses


def test_parse_accept_takes_last_accept_line_without_backticks():
    plan = "1. Do X\nACCEPT: `npx vitest run a.test.ts`\nnotes\nACCEPT: npx vitest run b.test.ts\n"
    assert parse_accept(plan) == "npx vitest run b.test.ts"
    assert parse_accept("1. Do X\n") == ""


def test_same_failure_ignores_numbers_and_whitespace():
    assert same_failure("tsc: 3 errors in 12ms", "tsc: 4 errors in 9ms\n")
    assert not same_failure("lint error", "type error")
    assert not same_failure("", "lint error")


def _verify(tmp_path, accept: str) -> dict:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("a\n")  # the guard rejects an empty diff
    p = Project("demo", tmp_path, "main", (), ("exit 0",), (), 3, 800)
    nodes = real.real_nodes(Config("subscription", 3, 0.7, {"demo": p}))
    return nodes["verify"]({"task": {"id": "a", "project": "demo"}, "worktree": str(tmp_path), "accept": accept})


def test_verify_fails_on_accept_command(tmp_path):
    out = _verify(tmp_path, "exit 1")
    assert out["verify_ok"] is False
    assert "$ exit 1" in out["last_failure"]


def test_verify_passes_with_accept_command(tmp_path):
    out = _verify(tmp_path, "exit 0")
    assert out["verify_ok"] is True, out["last_failure"]


def test_stuck_failure_replans_once_then_gives_up(tmp_path):
    sim, _, _ = run("stuck-replan", tmp_path)
    assert statuses(sim) == {"sim-01": "!"}
    # 2 attempts until stuck, 1 after the replan stuck again; without stuck detection it would be 3 + 3
    assert sim.script.calls["plan"] == 2 and sim.script.calls["implement"] == 3


def test_replan_after_exhausted_attempts_rescues_task(tmp_path):
    sim, _, _ = run("replan-rescues", tmp_path)
    assert statuses(sim) == {"sim-01": "x"}
    assert sim.script.calls["plan"] == 2 and sim.script.calls["implement"] == 3
