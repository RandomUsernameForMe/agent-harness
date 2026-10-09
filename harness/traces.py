"""Agent call ledger (cost, tokens incl. cache, steps) and task outcomes (PR merged/closed/open, failed)."""
import json
import re
import subprocess
import sys
import time
from typing import Callable

from . import backlog
from .events import _lock

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, worker TEXT, node TEXT, model TEXT, started REAL,
  duration_ms INTEGER, num_turns INTEGER, input_tokens INTEGER, output_tokens INTEGER,
  cache_read_tokens INTEGER, cache_write_tokens INTEGER, cost_usd REAL, status TEXT, error TEXT, session_id TEXT);
CREATE TABLE IF NOT EXISTS agent_steps (
  call_id INTEGER NOT NULL, seq INTEGER NOT NULL, kind TEXT, tool TEXT, summary TEXT, is_error INTEGER);
CREATE TABLE IF NOT EXISTS task_outcomes (
  task_id TEXT PRIMARY KEY, project TEXT, pr_url TEXT, state TEXT, checked REAL);
CREATE INDEX IF NOT EXISTS agent_steps_call ON agent_steps(call_id);
CREATE INDEX IF NOT EXISTS agent_calls_run ON agent_calls(run_id);
"""
CALL_FIELDS = ("run_id", "worker", "node", "model", "started", "duration_ms", "num_turns", "input_tokens",
               "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd", "status", "error", "session_id")
TOKENS = "input_tokens + output_tokens + cache_read_tokens + cache_write_tokens"
OVERHEAD_NODES = ("discover", "prioritize")
URL_RE = re.compile(r" → (https?://\S+)\s*$")
LOCAL_STATES = {"!": "failed", "?": "unclear"}
FINAL_STATES = ("merged", "closed")
GH_TIMEOUT = 20


def ensure(conn) -> None:
    conn.executescript(SCHEMA)


def record(conn, call: dict, steps: list[dict]) -> int:
    with _lock:
        cur = conn.execute(f"INSERT INTO agent_calls ({', '.join(CALL_FIELDS)}) VALUES ({', '.join('?' * len(CALL_FIELDS))})",
                           [call.get(f) for f in CALL_FIELDS])
        conn.executemany("INSERT INTO agent_steps VALUES (?,?,?,?,?,?)",
                         [(cur.lastrowid, i, s["kind"], s["tool"], s["summary"], int(s["is_error"]))
                          for i, s in enumerate(steps)])
        conn.commit()
    return cur.lastrowid


def _rows(conn, sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def calls(conn, run_id: str) -> list[dict]:
    return _rows(conn, "SELECT c.*, (SELECT COUNT(*) FROM agent_steps s WHERE s.call_id = c.id) AS steps "
                       "FROM agent_calls c WHERE run_id = ? ORDER BY id", (run_id,))


def steps(conn, call_id: int) -> list[dict]:
    return [r | {"is_error": bool(r["is_error"])}
            for r in _rows(conn, "SELECT * FROM agent_steps WHERE call_id = ? ORDER BY seq", (call_id,))]


def costs(conn) -> dict:
    placeholders = ", ".join("?" * len(OVERHEAD_NODES))
    # every task with calls or a known outcome (PRs from before the ledger existed have no calls)
    tasks = _rows(conn, f"""
        WITH agg AS (
          SELECT worker, SUM(node = 'implement' AND status != 'rate_limited') AS attempts, COUNT(*) AS calls,
                 ROUND(SUM(COALESCE(cost_usd, 0)), 6) AS cost_usd, SUM({TOKENS}) AS tokens, MIN(id) AS first
          FROM agent_calls WHERE node NOT IN ({placeholders}) AND worker IS NOT NULL GROUP BY worker),
        ids AS (SELECT worker AS task_id FROM agg UNION SELECT task_id FROM task_outcomes)
        SELECT ids.task_id, o.project, o.state, o.pr_url, COALESCE(agg.attempts, 0) AS attempts,
               COALESCE(agg.calls, 0) AS calls, COALESCE(agg.cost_usd, 0) AS cost_usd,
               COALESCE(agg.tokens, 0) AS tokens
        FROM ids LEFT JOIN agg ON agg.worker = ids.task_id LEFT JOIN task_outcomes o ON o.task_id = ids.task_id
        ORDER BY agg.first IS NULL, agg.first, ids.task_id""", OVERHEAD_NODES)
    overhead = _rows(conn, f"""
        SELECT run_id, node, COUNT(*) AS calls, ROUND(SUM(COALESCE(cost_usd, 0)), 6) AS cost_usd, SUM({TOKENS}) AS tokens
        FROM agent_calls WHERE node IN ({placeholders}) GROUP BY run_id, node ORDER BY MIN(id)""", OVERHEAD_NODES)
    return {"tasks": tasks, "overhead": overhead}


def backfill(conn) -> int:
    """Calls of real runs from before the ledger, rebuilt from event-log usage (no steps, no cache tokens)."""
    with _lock:
        # per event, not per run: a run resumed after phase 4 keeps its earlier, unrecorded calls; an event
        # is covered once a call of its run started no later than it (recorded calls and backfilled ones alike)
        rows = conn.execute("SELECT run_id, ts, node, worker, usage FROM events e WHERE usage IS NOT NULL "
                            "AND simulated = 0 AND NOT EXISTS (SELECT 1 FROM agent_calls a "
                            "WHERE a.run_id = e.run_id AND a.started <= e.ts) ORDER BY id").fetchall()
    n = 0
    for r in rows:
        for u in json.loads(r["usage"]):
            record(conn, {"run_id": r["run_id"], "worker": r["worker"], "node": u.get("node") or r["node"],
                          "model": u.get("model"), "started": r["ts"], "input_tokens": u.get("input_tokens", 0),
                          "output_tokens": u.get("output_tokens", 0), "cache_read_tokens": 0,
                          "cache_write_tokens": 0, "cost_usd": u.get("cost_usd"), "status": "ok"}, [])
            n += 1
    return n


def pr_urls(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = backlog.TASK_RE.match(line.rstrip())
        if m and m["status"] == "x" and (u := URL_RE.search(line)):
            out[m["id"]] = u[1]
    return out


def pr_state(url: str) -> str | None:
    """open / merged / closed from gh; None when gh fails (state is then left as it was)."""
    try:
        out = subprocess.run(["gh", "pr", "view", url, "--json", "state", "-q", ".state"],
                             capture_output=True, text=True, encoding="utf-8", timeout=GH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:  # gh missing or hanging must not block a run start
        print(f"gh pr view {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"gh pr view {url}: {out.stderr.strip()}", file=sys.stderr)
        return None
    return out.stdout.strip().lower() or None


def open_prs(repo_dir) -> list[dict] | None:
    """Open PRs of the repo checked out in repo_dir; None when gh fails."""
    try:
        out = subprocess.run(["gh", "pr", "list", "--state", "open", "--json", "number,title,url,isDraft,createdAt"],
                             cwd=repo_dir, capture_output=True, text=True, encoding="utf-8", timeout=GH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"gh pr list in {repo_dir}: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"gh pr list in {repo_dir}: {out.stderr.strip()}", file=sys.stderr)
        return None
    return json.loads(out.stdout or "[]")


def _upsert(conn, task_id: str, project: str, url: str | None, state: str) -> None:
    with _lock:
        conn.execute("INSERT INTO task_outcomes VALUES (?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                     "project = excluded.project, pr_url = excluded.pr_url, state = excluded.state, "
                     "checked = excluded.checked", (task_id, project, url, state, time.time()))
        conn.commit()


def sync_outcomes(conn, backlog_text: str, state_of: Callable[[str], str | None] = pr_state) -> None:
    urls, known = pr_urls(backlog_text), outcomes(conn)
    for t in backlog.parse(backlog_text):
        if t.status in LOCAL_STATES:
            _upsert(conn, t.id, t.project, None, LOCAL_STATES[t.status])
        elif t.id in urls and known.get(t.id, (None, None))[1] in FINAL_STATES:
            continue  # merged / closed never change; asking gh again only slows every run start
        elif t.id in urls and (state := state_of(urls[t.id])):
            _upsert(conn, t.id, t.project, urls[t.id], state)


def outcomes(conn) -> dict[str, tuple]:
    return {r["task_id"]: (r["project"], r["state"], r["pr_url"]) for r in _rows(conn, "SELECT * FROM task_outcomes")}
