import time
from collections import Counter

from ..graph import RateLimited


class Script:
    def __init__(self, spec: dict, delay: float = 0.0):
        self.spec, self.delay, self.calls = spec, delay, Counter()

    def next(self, node: str) -> dict:
        i = self.calls[node]
        self.calls[node] += 1
        if self.delay:
            time.sleep(self.delay)
        value = self.spec.get(node, "ok")
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
        v = script.next("preflight")
        return _failed(v) if v else {"branch": f"harness/{s['task']['id']}"}

    def make_worktree(s):
        v = script.next("make_worktree")
        return _failed(v) if v else {"worktree": f"/sim/{s['task']['id']}"}

    def plan(s):
        v = script.next("plan")
        if "unclear" in v:
            return {"outcome": "needs_clarification", "last_failure": v["unclear"], "usage": _usage("plan", "opus")}
        return _failed(v) if v else {"plan": "1. Simulovaný krok", "usage": _usage("plan", "opus")}

    def setup(s):
        v = script.next("setup")
        return _failed(v) if v else {"baseline_failures": []}

    def implement(s):
        v = script.next("implement")
        return _failed(v) if v else {"usage": _usage("implement", "sonnet")}

    def verify(s):
        v = script.next("verify")
        attempts = s.get("attempts", 0) + 1
        if not v:
            return {"verify_ok": True, "last_failure": "", "attempts": attempts}
        msg = v.get("fail") or f"guard: {v.get('guard')}"
        return {"verify_ok": False, "last_failure": msg, "attempts": attempts}

    def open_pr(s):
        v = script.next("open_pr")
        return _failed(v) if v else {"outcome": "pr_opened", "pr_url": f"https://example.invalid/sim/{s['task']['id']}"}

    return {"preflight": preflight, "make_worktree": make_worktree, "plan": plan, "setup": setup,
            "implement": implement, "verify": verify, "open_pr": open_pr}
