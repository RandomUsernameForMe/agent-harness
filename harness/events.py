import json
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

SCHEMA = """CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts REAL NOT NULL,
  node TEXT,
  kind TEXT NOT NULL,
  delta TEXT,
  usage TEXT,
  rate_limit TEXT,
  simulated INTEGER NOT NULL DEFAULT 0
)"""
JSON_FIELDS = ("delta", "usage", "rate_limit")
_lock = threading.Lock()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def _dump(value):
    return None if value is None else json.dumps(value, default=str, ensure_ascii=False)


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_FIELDS:
        d[k] = json.loads(d[k]) if d[k] else None
    d["simulated"] = bool(d["simulated"])
    return d


def log(conn, run_id, kind, node=None, delta=None, usage=None, rate_limit=None, simulated=False) -> None:
    with _lock:
        conn.execute(
            "INSERT INTO events (run_id, ts, node, kind, delta, usage, rate_limit, simulated) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, time.time(), node, kind, _dump(delta), _dump(usage), _dump(rate_limit), int(simulated)),
        )
        conn.commit()


def events_after(conn, after_id: int = 0, run_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM events WHERE id > ?", [after_id]
    if run_id:
        sql, args = sql + " AND run_id = ?", args + [run_id]
    with _lock:
        rows = conn.execute(sql + " ORDER BY id", args).fetchall()
    return [_row(r) for r in rows]


def runs(conn) -> list[dict]:
    with _lock:
        rows = conn.execute("""
          SELECT run_id, MIN(ts) AS started, MAX(ts) AS last, MAX(simulated) AS simulated,
                 (SELECT kind FROM events e2 WHERE e2.run_id = e.run_id ORDER BY id DESC LIMIT 1) AS last_kind
          FROM events e GROUP BY run_id ORDER BY started DESC""").fetchall()
    return [{**dict(r), "simulated": bool(r["simulated"])} for r in rows]


def usage_summary(conn) -> list[dict]:
    with _lock:
        rows = conn.execute("SELECT ts, usage, simulated FROM events WHERE usage IS NOT NULL").fetchall()
    totals: dict[tuple, dict] = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
        for u in json.loads(r["usage"]):
            t = totals[(day, u.get("node"), u.get("model"), bool(r["simulated"]))]
            t["input_tokens"] += u.get("input_tokens") or 0
            t["output_tokens"] += u.get("output_tokens") or 0
            t["cost_usd"] += u.get("cost_usd") or 0.0
    return [{"day": d, "node": n, "model": m, "simulated": s, **v} for (d, n, m, s), v in sorted(totals.items())]


def latest_rate_limit(conn) -> dict | None:
    with _lock:
        row = conn.execute("SELECT rate_limit FROM events WHERE rate_limit IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["rate_limit"]) if row else None
