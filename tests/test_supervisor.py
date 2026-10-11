import asyncio

from harness import supervisor
from harness.graph import after_verify, replan
from harness.supervisor import Watch, is_test_path

TASK = {"title": "Oprav splitPoints", "description": "Hotovo = test projde"}


def judge_of(answers):
    calls = []

    def judge(state, questions):
        calls.append((state, questions))
        return answers(len(calls)) if callable(answers) else answers
    return judge, calls


def pre(watch, steps, tool, tool_input):
    hook = watch.hooks(steps)["PreToolUse"][0].hooks[0]
    return asyncio.run(hook({"tool_name": tool, "tool_input": tool_input}, "t1", None))


def post(watch, steps, tool="Bash", tool_input=None):
    hook = watch.hooks(steps)["PostToolUse"][0].hooks[0]
    return asyncio.run(hook({"tool_name": tool, "tool_input": tool_input or {"command": "npm test"},
                             "tool_response": "1 failed"}, "t1", None))


def test_test_paths():
    for p in ["tests/test_x.py", "lib/utils.test.mjs", "app/__tests__/route.test.ts", "src/a.spec.tsx",
              "C:\\w\\lib\\__tests__\\kv.test.ts", "bot/test_vault.py"]:
        assert is_test_path(p), p
    for p in ["lib/utils.ts", "harness/supervisor.py", "docs/testing.md", "latest.ts"]:
        assert not is_test_path(p), p


def test_weakening_test_edit_is_denied_and_noted():
    judge, calls = judge_of({"weakens": 0.92})
    steps = []
    out = pre(Watch(TASK, "plan", judge), steps, "Edit",
              {"file_path": "lib/a.test.mjs", "old_string": "assert.equal(f(4), [1,1,2])", "new_string": ""})
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny" and "weaken" in spec["permissionDecisionReason"]
    assert calls[0][0]["edit"]["old"] == "assert.equal(f(4), [1,1,2])"
    assert steps[-1]["kind"] == "supervisor" and steps[-1]["is_error"]


def test_honest_test_edit_and_source_edit_pass():
    judge, calls = judge_of({"weakens": 0.1})
    steps = []
    w = Watch(TASK, "plan", judge)
    assert pre(w, steps, "Edit", {"file_path": "lib/a.test.mjs", "old_string": "x", "new_string": "x\ny"}) == {}
    assert pre(w, steps, "Edit", {"file_path": "lib/a.ts", "old_string": "x", "new_string": ""}) == {}
    assert pre(w, steps, "Write", {"file_path": "lib/new.test.mjs", "content": "test()"}) == {}  # new file
    assert len(calls) == 1 and steps[-1]["kind"] == "supervisor" and not steps[-1]["is_error"]


def test_no_judge_answer_means_no_intervention():
    judge, _ = judge_of(None)
    steps = []
    w = Watch(TASK, "plan", judge)
    assert pre(w, steps, "Edit", {"file_path": "a.test.ts", "old_string": "expect(1)", "new_string": ""}) == {}
    assert all(post(w, steps) == {} for _ in range(20))
    assert steps == [] and w.stop == ""


def test_progress_checked_every_n_tools_warns_then_stops():
    judge, calls = judge_of({"progress": "repeating", "progress_confidence": 0.9})
    steps = [{"kind": "tool_use", "tool": "Bash", "summary": "npm test", "is_error": False}]
    w = Watch(TASK, "plan", judge)
    outs = [post(w, steps) for _ in range(supervisor.CHECK_EVERY * 2)]
    assert len(calls) == 2 and "Bash" in str(calls[0][0]["recent_steps"])
    warn = outs[supervisor.CHECK_EVERY - 1]
    assert warn["hookSpecificOutput"]["additionalContext"] == supervisor.WARNINGS["repeating"]
    assert outs[-1]["continue_"] is False and w.stop
    assert all(o == {} for o in outs[:supervisor.CHECK_EVERY - 1])


def test_progressing_or_unsure_verdict_does_nothing():
    for answer in ({"progress": "progressing", "progress_confidence": 0.9},
                   {"progress": "repeating", "progress_confidence": 0.3}):
        judge, calls = judge_of(answer)
        w = Watch(TASK, "plan", judge)
        assert all(post(w, []) == {} for _ in range(supervisor.CHECK_EVERY * 2))
        assert len(calls) == 2 and w.stop == ""


def test_jev_judge_without_key_is_silent(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setattr(supervisor, "ENV_FILE", tmp_path / ".env")
    assert supervisor.jev_judge({"x": 1}, supervisor.PROGRESS) is None


def test_jev_key_from_env_file_stays_out_of_environ(monkeypatch, tmp_path):
    import os
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    (tmp_path / ".env").write_text('# klíče\nHARNESS_X=1\nJEV_API_KEY="k-123"\n', encoding="utf-8")
    monkeypatch.setattr(supervisor, "ENV_FILE", tmp_path / ".env")
    assert supervisor.jev_key() == "k-123" and "JEV_API_KEY" not in os.environ
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-env")
    assert supervisor.jev_key() == "from-env"


def test_supervisor_stop_goes_to_replan_with_reason():
    s = {"verify_ok": False, "attempts": 1, "max_attempts": 3, "supervisor_stop": "repeating", "last_failure": "tsc"}
    assert after_verify(s) == "replan"
    assert after_verify(s | {"replanned": True}) == "report"
    out = replan(s)
    assert out["supervisor_stop"] == "" and "repeating" in out["last_failure"] and "tsc" in out["last_failure"]
