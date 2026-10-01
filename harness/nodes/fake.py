import threading
import time
from collections import Counter

from ..graph import RateLimited


class Script:
    """Scenario-driven node results. `spec` is shared by all tasks (a list advances per call of that node
    across tasks); `per_task` overrides it for one task with its own call sequence."""

    def __init__(self, spec: dict, delay: float = 0.0, per_task: dict[str, dict] | None = None):
        self.spec, self.delay, self.per_task = spec, delay, per_task or {}
        self.calls, self._seq, self._lock = Counter(), Counter(), threading.Lock()

    def next(self, node: str, task_id: str = "") -> dict:
        own = self.per_task.get(task_id) or {}
        key = (task_id, node) if node in own else node
        with self._lock:
            i = self._seq[key]
            self._seq[key] += 1
            self.calls[node] += 1
        if self.delay:
            time.sleep(self.delay)
        value = own.get(node, self.spec.get(node, "ok"))
        if isinstance(value, list):
            value = value[min(i, len(value) - 1)]
        if value in ("ok", "pass"):
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"unknown scenario value for {node}: {value!r}")
        if "rate_limit" in value:
            raise RateLimited(int(time.time()) + int(value["rate_limit"]))
        if "error" in value:
            raise RuntimeError(value["error"])
        return value


def _usage(node: str, model: str) -> list[dict]:
    return [{"node": node, "model": model, "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.0}]


def _failed(value: dict) -> dict:
    return {"outcome": "failed", "last_failure": value.get("fail", str(value))}


def fake_nodes(script: Script) -> dict:
    def preflight(s):
        v = script.next("preflight", s["task"]["id"])
        return _failed(v) if v else {"branch": f"harness/{s['task']['id']}"}

    def make_worktree(s):
        v = script.next("make_worktree", s["task"]["id"])
        return _failed(v) if v else {"worktree": f"/sim/{s['task']['id']}"}

    def plan(s):
        v = script.next("plan", s["task"]["id"])
        if "unclear" in v:
            return {"outcome": "needs_clarification", "last_failure": v["unclear"], "usage": _usage("plan", "opus")}
        if v:
            return _failed(v)
        notes = "".join(f"\nUpřesnění: {c}" for c in s.get("clarifications") or [])
        return {"plan": "1. Simulovaný krok" + notes, "usage": _usage("plan", "opus")}

    def setup(s):
        v = script.next("setup", s["task"]["id"])
        return _failed(v) if v else {"baseline_failures": []}

    def implement(s):
        v = script.next("implement", s["task"]["id"])
        return _failed(v) if v else {"usage": _usage("implement", "sonnet")}

    def verify(s):
        v = script.next("verify", s["task"]["id"])
        attempts = s.get("attempts", 0) + 1
        if not v:
            return {"verify_ok": True, "last_failure": "", "attempts": attempts}
        msg = v.get("fail") or f"guard: {v.get('guard')}"
        return {"verify_ok": False, "last_failure": msg, "attempts": attempts}

    def open_pr(s):
        v = script.next("open_pr", s["task"]["id"])
        return _failed(v) if v else {"outcome": "pr_opened", "pr_url": f"https://example.invalid/sim/{s['task']['id']}"}

    return {"preflight": preflight, "make_worktree": make_worktree, "plan": plan, "setup": setup,
            "implement": implement, "verify": verify, "open_pr": open_pr}
