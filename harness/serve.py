"""`harness serve`: the autonomous daemon, controlled from Telegram.

One run at a time, executed by `spawn` (a thread in production). The main loop long-polls Telegram and makes
every decision, so the daemon state is only touched from one thread; the run thread just hands back its result."""
import json
import sys
import threading
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable

from . import backlog, events, lessons
from .nodes.common import edit_backlog
from .priority import config_focus, rank
from .projects import Config
from .runner import approvals, execute, questions, resets_at, run_config
from .telegram import Buttons, TelegramError

POLL_SECONDS = 20
UNKNOWN_RESET_WAIT = 15 * 60  # rate limit without a reset time: don't hammer it every tick
ACTIVE = ("running", "waiting", "paused", "approving")
HELP = ("Příkazy:\n/status — stav démona\n/queue — fronta úkolů\n/proposals — znovu poslat návrhy ke schválení\n"
        "/run — spustit běh hned\n/pause, /resume — vypnout/zapnout automatický start\n/digest — přehled teď\n\n"
        "Na dotaz k nejasnému úkolu odpověz reply na jeho zprávu.")


def default_state(now: datetime) -> dict:
    return {"paused": False, "run": None, "seq": 0, "offset": 0, "asked": {}, "answers": {}, "approval": None,
            "proposals_sent": [], "lessons_sent": [], "rejected_msgs": {},
            "last_scan": None, "last_digest": now.date().isoformat(), "digest_since": now.timestamp()}


def load_state(path: Path, now: datetime) -> dict:
    try:
        return default_state(now) | json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_state(now)


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def in_window(t: time, window: tuple[time, time] | None) -> bool:
    if window is None:
        return False
    start, end = window
    return start <= t < end if start < end else t >= start or t < end


def fmt_window(window) -> str:
    return f"{window[0]:%H:%M}–{window[1]:%H:%M}" if window else "žádné (jen /run)"


@dataclass
class Ctx:
    cfg: Config
    bot: object  # Telegram-like: chat_id, send, edit, ack
    app: object
    backlog_path: Path
    conn: object
    new_inputs: Callable[[str], dict]
    state: dict
    spawn: Callable[[Callable[[], None]], None]
    now: Callable[[], datetime]
    scan: Callable[[], int]
    simulated: bool = False
    state_path: Path | None = None
    # handoff: the run thread only ever sets `result`; `busy` is set and cleared by the main loop alone
    busy: bool = False
    result: tuple | None = None  # (run_id, status | Exception)
    last_error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)  # main loop vs dashboard requests
    store: object = None  # lessons (phase 5); None = feature off


def say(ctx: Ctx, text: str, buttons: Buttons | None = None) -> int | None:
    try:
        return ctx.bot.send(text, buttons)
    except TelegramError as e:
        print(f"telegram: {e}", file=sys.stderr)
        return None


def _quiet(fn: Callable, *args) -> None:
    try:
        fn(*args)
    except TelegramError as e:
        print(f"telegram: {e}", file=sys.stderr)


def _backlog_text(ctx: Ctx) -> str:
    return ctx.backlog_path.read_bytes().decode("utf-8") if ctx.backlog_path.exists() else ""


def _tasks(ctx: Ctx) -> list[backlog.Task]:
    return backlog.parse(_backlog_text(ctx))


# --- runs -------------------------------------------------------------------------------------------------

def _start(ctx: Ctx, run_id: str, inputs, answers: dict[str, str] | None = None) -> None:
    ctx.busy = True
    ctx.state["run"] = {"id": run_id, "status": "running"}

    def job() -> None:
        try:
            outcome = execute(ctx.app, inputs, run_config(run_id), ctx.conn, ctx.simulated, answers=answers)
        except Exception as e:
            outcome = e
        ctx.result = (run_id, outcome)  # one atomic assignment
    ctx.spawn(job)


def _collect(ctx: Ctx) -> None:
    if ctx.result is None:
        return
    run_id, outcome = ctx.result
    ctx.result, ctx.busy = None, False
    if isinstance(outcome, Exception):
        ctx.state["run"] = {"id": run_id, "status": "crashed"}
        ctx.state["paused"] = True  # a deterministic crash would otherwise restart forever
        say(ctx, f"💥 Běh {run_id} spadl: {type(outcome).__name__}: {str(outcome)[:500]}\n"
                 "Automatický start je vypnutý, /resume ho zapne.")
        return
    run = {"id": run_id, "status": outcome}
    if outcome == "paused":
        run["resets_at"] = resets_at(ctx.app, run_config(run_id)) or ctx.now().timestamp() + UNKNOWN_RESET_WAIT
    ctx.state["run"] = run


def _quota_reserved(ctx: Ctx) -> bool:
    """A run stops at quota_threshold, but its quota dies with it: without this a new run would start on the
    next tick and eat the owner's reserve. Hold automatic starts until that window resets."""
    info = events.latest_utilization(ctx.conn, ctx.simulated)
    if info is None or info["utilization"] < ctx.cfg.quota_threshold:
        return False
    return ctx.now().timestamp() < (info.get("resets_at") or info["ts"] + 5 * 3600)


def _only(ctx: Ctx, projects) -> list[str] | None:
    if projects is None:
        return None
    if not isinstance(projects, list) or not projects or not set(projects) <= set(ctx.cfg.projects):
        raise ValueError(f"projects must be a non-empty subset of {sorted(ctx.cfg.projects)}")
    return projects


def _start_new(ctx: Ctx, manual: bool = False, discover: bool = False, only: list[str] | None = None) -> str:
    run = ctx.state["run"]
    if ctx.busy or (run and run["status"] in ACTIVE):
        return "Běh už probíhá nebo čeká — /status."
    if not manual and (ctx.state["paused"] or not in_window(ctx.now().time(), ctx.cfg.serve.window)
                       or _quota_reserved(ctx)):
        return ""
    if not discover and not rank(_tasks(ctx), set(only or ctx.cfg.projects)):
        return "Fronta je prázdná." if manual else ""
    ctx.state["seq"] += 1
    run_id = f"{'go' if discover else 'run'}-{ctx.now():%Y%m%d-%H%M%S}-s{ctx.state['seq']}"
    _start(ctx, run_id, ctx.new_inputs(run_id) | {"discover": discover, "only": only})
    return f"▶️ Spouštím {run_id}."


def _resume(ctx: Ctx, run_id: str) -> None:
    answers, ctx.state["answers"] = ctx.state["answers"], {}
    for iid in answers:  # a late reply to these must not look accepted (_answer)
        if iid in ctx.state["asked"]:
            ctx.state["asked"][iid]["answered"] = True
    _start(ctx, run_id, None, answers)


def _advance(ctx: Ctx) -> None:
    run = ctx.state["run"]
    status = run["status"] if run else None
    if status == "running":  # the daemon died mid-run: continue from the last checkpoint
        _start(ctx, run["id"], None)
    elif status == "paused":
        # answers wait for the reset too: any resume re-runs the rate-limited node, and the quota is shared
        if ctx.now().timestamp() >= run["resets_at"]:
            _resume(ctx, run["id"])
    elif status == "waiting":
        if ctx.state["answers"]:
            _resume(ctx, run["id"])
    elif status == "approving":
        if ctx.state["approval"]:
            approval, ctx.state["approval"] = ctx.state["approval"], None
            _start(ctx, run["id"], None, approval)
    else:
        _start_new(ctx)


# --- questions --------------------------------------------------------------------------------------------

def _sync_questions(ctx: Ctx) -> None:
    """Send new questions, time out old ones, forget closed ones. Only while no run thread is busy."""
    run, st = ctx.state["run"], ctx.state
    qs = questions(ctx.app, run_config(run["id"])) if run and run["status"] in ("waiting", "paused") else []
    open_ids = {q["id"] for q in qs}
    st["asked"] = {k: v for k, v in st["asked"].items() if k in open_ids}
    st["answers"] = {k: v for k, v in st["answers"].items() if k in open_ids}
    now, limit = ctx.now().timestamp(), ctx.cfg.serve.question_timeout_hours
    for q in qs:
        asked = st["asked"].get(q["id"])
        if asked is None:
            text = (f"❓ [{q['project']}] {q['task']} {q['title']}\n\n{q['question']}\n\n"
                    "Odpověz reply na tuto zprávu.")
            if (mid := say(ctx, text, [[("Přeskočit", f"sk:{q['id']}")]])) is not None:
                st["asked"][q["id"]] = {"message_id": mid, "task": q["task"], "at": now}
        elif q["id"] not in st["answers"] and now - asked["at"] >= limit * 3600:
            st["answers"][q["id"]] = ""
            say(ctx, f"⏱ {q['task']}: bez odpovědi {limit:g} h, přeskakuji → [?]")


def _answer(ctx: Ctx, msg: dict, text: str) -> str:
    asked, answers = ctx.state["asked"], ctx.state["answers"]
    reply_id = (msg.get("reply_to_message") or {}).get("message_id")
    if (task_id := ctx.state["rejected_msgs"].pop(str(reply_id), None)) is not None:
        return _reason(ctx, task_id, text)
    iid = next((i for i, a in asked.items() if a["message_id"] == reply_id), None)
    if iid is None and reply_id is None:
        unanswered = [i for i, a in asked.items() if i not in answers and not a.get("answered")]
        iid = unanswered[0] if len(unanswered) == 1 else None
    if iid is None:
        return "Na který dotaz odpovídáš? Dej reply na jeho zprávu." if asked else HELP
    if asked[iid].get("answered"):
        return f"{asked[iid]['task']}: odpověď už mám, úkol pokračuje — tahle se nepoužije."
    answers[iid] = text
    run = ctx.state["run"]
    if run and run["status"] == "paused":
        return f"Uloženo. Běh čeká na reset limitu ({datetime.fromtimestamp(run['resets_at']):%H:%M}), pak pokračuje."
    return f"👍 {asked[iid]['task']}: odpověď uložena, pokračuji."


# --- proposals --------------------------------------------------------------------------------------------

def _notify_proposals(ctx: Ctx) -> None:
    sent = ctx.state["proposals_sent"]
    for t in _tasks(ctx):
        if t.status != "~" or t.id in sent:
            continue
        text = f"💡 Návrh [{t.project}] P{t.priority} {t.id}\n{t.title}" + (f"\n\n{t.description}" if t.description else "")
        if say(ctx, text, [[("✅ Schválit", f"ap:{t.id}"), ("❌ Zamítnout", f"rj:{t.id}")]]) is None:
            return  # Telegram down: retry next tick
        sent.append(t.id)


def _rejected(reason: str) -> str:
    reason = " ".join(reason.split())[:300]
    return f" — zamítnuto: {reason}" if reason else " — zamítnuto"


def _decide(ctx: Ctx, task_id: str, approve: bool, reason: str = "") -> str:
    note = []

    def change(text: str) -> str:
        t = next((t for t in backlog.parse(text) if t.id == task_id), None)
        if t is None or t.status != "~":
            note.append("už vyřízeno")
            return text
        note.append("schváleno → fronta" if approve else "zamítnuto")
        return backlog.set_status(text, task_id, " " if approve else "-", "" if approve else _rejected(reason))
    edit_backlog(ctx.backlog_path, change)
    return note[0]


def _reason(ctx: Ctx, task_id: str, reason: str) -> str:
    """A reason sent after the rejection (Telegram reply); the reflection reads it from the task's note."""
    def change(text: str) -> str:
        t = next((t for t in backlog.parse(text) if t.id == task_id), None)
        return backlog.set_status(text, task_id, "-", _rejected(reason)) if t and t.status == "-" else text
    edit_backlog(ctx.backlog_path, change)
    return f"📝 {task_id}: důvod uložen, reflexe se z něj poučí."


# --- lessons ----------------------------------------------------------------------------------------------

def _notify_lessons(ctx: Ctx) -> None:
    if ctx.store is None:
        return
    sent = ctx.state["lessons_sent"]
    for x in lessons.lessons(ctx.store, status="proposed"):
        ref = f"{x['project']}/{x['id']}"
        if ref in sent:
            continue
        if say(ctx, f"🧠 Lekce [{x['project']}]\n{x['text']}",
               [[("✅ Platí", f"la:{ref}"), ("❌ Zahodit", f"lr:{ref}")]]) is None:
            return
        sent.append(ref)


def _decide_lesson(ctx: Ctx, ref: str, approve: bool) -> str:
    if ctx.store is None:
        return "lekce nejsou k dispozici"
    project, _, key = ref.partition("/")
    return lessons.decide(ctx.store, project, key, "active" if approve else "retired")


# --- digest -----------------------------------------------------------------------------------------------

def digest_text(conn, since: float, backlog_text: str, simulated: bool = False) -> str:
    runs, tokens, cost = set(), 0, 0.0
    tasks: dict[str, dict] = defaultdict(dict)
    for e in events.since(conn, since, simulated):
        runs.add(e["run_id"])
        for u in e["usage"] or []:
            tokens += (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
            cost += u.get("cost_usd") or 0.0
        if e["worker"] and e["kind"] == "end":
            d = e["delta"] or {}
            tasks[e["worker"]].update({k: d[k] for k in ("outcome", "pr_url", "last_failure") if k in d})
            if e["node"] == "report":
                tasks[e["worker"]]["reported"] = True
    lines = [f"📋 Digest — běhů: {len(runs)}"]
    for task_id, t in tasks.items():
        outcome = t.get("outcome") or ("failed" if t.get("reported") else None)
        detail = (t.get("last_failure") or "").replace("\n", " ")[:150]
        if outcome == "pr_opened":
            lines.append(f"✅ {task_id} → {t.get('pr_url')}")
        elif outcome == "needs_clarification":
            lines.append(f"❓ {task_id} — nejasné: {detail}")
        elif outcome:
            lines.append(f"❌ {task_id} — {outcome}: {detail}")
        else:
            lines.append(f"⏳ {task_id} — rozpracováno")
    if not tasks:
        lines.append("Žádné úkoly.")
    open_tasks = backlog.parse(backlog_text)
    lines.append(f"Tokeny: {tokens}, ${cost:.2f}")
    lines.append(f"Fronta: {sum(t.status == ' ' for t in open_tasks)} · "
                 f"návrhy ke schválení: {sum(t.status == '~' for t in open_tasks)}")
    return "\n".join(lines)


def _send_digest(ctx: Ctx) -> None:
    now = ctx.now()
    text = digest_text(ctx.conn, ctx.state["digest_since"], _backlog_text(ctx), ctx.simulated)
    if say(ctx, text) is not None:
        ctx.state["last_digest"], ctx.state["digest_since"] = now.date().isoformat(), now.timestamp()


def _maybe_digest(ctx: Ctx) -> None:
    window, now = ctx.cfg.serve.window, ctx.now()
    if window and ctx.state["last_digest"] != now.date().isoformat() \
            and not in_window(now.time(), window) and now.time() >= window[1]:
        _send_digest(ctx)


def _maybe_scan(ctx: Ctx) -> None:
    now, window = ctx.now(), ctx.cfg.serve.window
    if not ctx.cfg.serve.scan or not in_window(now.time(), window):
        return
    # once per window: a 22:00–07:00 window started yesterday when it is 01:00
    opened = (now.date() if now.time() >= window[0] else now.date() - timedelta(days=1)).isoformat()
    if ctx.state["last_scan"] != opened:
        ctx.state["last_scan"] = opened
        try:
            ctx.scan()
        except Exception as e:
            print(f"scan failed: {e}", file=sys.stderr)


# --- Telegram input ---------------------------------------------------------------------------------------

def _status(ctx: Ctx) -> str:
    run, tasks = ctx.state["run"], _tasks(ctx)
    window = ctx.cfg.serve.window
    lines = [f"Automatika: {'vypnutá' if ctx.state['paused'] else 'zapnutá'} · okno {fmt_window(window)}"
             + (" (teď v okně)" if in_window(ctx.now().time(), window) else "")]
    if run:
        line = f"Běh: {run['id']} — {'running' if ctx.busy else run['status']}"
        if run["status"] == "paused" and not ctx.busy:
            line += f", reset limitu {datetime.fromtimestamp(run['resets_at']):%H:%M}"
        lines.append(line)
    else:
        lines.append("Běh: žádný")
    lines.append(f"Fronta: {len(rank(tasks, set(ctx.cfg.projects)))} · návrhy: {sum(t.status == '~' for t in tasks)}"
                 f" · Dotazy: {sum(1 for i in ctx.state['asked'] if i not in ctx.state['answers'])}")
    return "\n".join(lines)


def _queue(ctx: Ctx) -> str:
    ranked = rank(_tasks(ctx), set(ctx.cfg.projects), config_focus(ctx.cfg),
                  {n: p.weight for n, p in ctx.cfg.projects.items()})
    if not ranked:
        return "Fronta je prázdná."
    return "\n".join(f"{i}. P{t.priority} {t.id} [{t.project}] {t.title}" for i, t in enumerate(ranked[:10], 1))


def _command(ctx: Ctx, cmd: str) -> str:
    if cmd == "status":
        return _status(ctx)
    if cmd == "queue":
        return _queue(ctx)
    if cmd == "proposals":
        pending = {t.id for t in _tasks(ctx) if t.status == "~"}
        ctx.state["proposals_sent"] = [i for i in ctx.state["proposals_sent"] if i not in pending]
        return f"Posílám {len(pending)} návrh(ů)." if pending else "Žádné návrhy ke schválení."
    if cmd == "run":
        return _start_new(ctx, manual=True)
    if cmd == "pause":
        ctx.state["paused"] = True
        return "⏸ Automatický start vypnut. Běžící běh doběhne."
    if cmd == "resume":
        ctx.state["paused"] = False
        return f"▶️ Automatický start zapnut, okno {fmt_window(ctx.cfg.serve.window)}."
    if cmd == "digest":
        _send_digest(ctx)
        return ""
    return HELP


def _callback(ctx: Ctx, cb: dict) -> None:
    kind, _, key = (cb.get("data") or "").partition(":")
    if kind == "sk":
        note = "přeskočeno" if key in ctx.state["asked"] else "dotaz už není otevřený"
        if key in ctx.state["asked"]:
            ctx.state["answers"][key] = ""
    elif kind in ("ap", "rj"):
        note = _decide(ctx, key, kind == "ap")
        if kind == "rj" and note == "zamítnuto":
            note += " (důvod můžeš poslat reply na tuto zprávu)"
            rejected = ctx.state["rejected_msgs"]
            rejected[str(cb["message"]["message_id"])] = key
            ctx.state["rejected_msgs"] = dict(list(rejected.items())[-50:])
    elif kind in ("la", "lr"):
        note = _decide_lesson(ctx, key, kind == "la")
    else:
        note = "neznámá akce"
    _quiet(ctx.bot.ack, cb["id"], note)
    msg = cb["message"]
    _quiet(ctx.bot.edit, msg["message_id"], f"{msg.get('text', '')}\n\n→ {note}")


def handle(ctx: Ctx, update: dict) -> None:
    """Only the configured chat may control the daemon: commands start runs and spend quota."""
    if cb := update.get("callback_query"):
        if (cb.get("message") or {}).get("chat", {}).get("id") == ctx.bot.chat_id:
            _callback(ctx, cb)
        return
    msg = update.get("message") or {}
    if msg.get("chat", {}).get("id") != ctx.bot.chat_id or not (text := (msg.get("text") or "").strip()):
        return
    if text.startswith("/"):
        reply = _command(ctx, text.split()[0][1:].split("@")[0].lower())
    else:
        reply = _answer(ctx, msg, text)
    if reply:
        say(ctx, reply)


# --- dashboard --------------------------------------------------------------------------------------------

def _open(ctx: Ctx) -> dict | None:
    """Config of the run when it sits on interrupts the owner can answer (the run thread is idle)."""
    run = ctx.state["run"]
    if ctx.busy or not run or run["status"] not in ("waiting", "paused", "approving"):
        return None
    return run_config(run["id"])


def snapshot(ctx: Ctx) -> dict:
    with ctx.lock:
        _collect(ctx)
        run, config = ctx.state["run"], _open(ctx)
        plans = approvals(ctx.app, config) if config and not ctx.state["approval"] else []
        return {"paused": ctx.state["paused"], "window": fmt_window(ctx.cfg.serve.window),
                "in_window": in_window(ctx.now().time(), ctx.cfg.serve.window),
                "run": run and {**run, "status": "running" if ctx.busy else run["status"]},
                "questions": [q | {"answered": q["id"] in ctx.state["answers"]}
                              for q in (questions(ctx.app, config) if config else [])],
                "plan": plans[0] if plans else None,
                "lessons": [x for x in lessons.lessons(ctx.store) if x["status"] != "retired"]}


def _approve_plan(ctx: Ctx, keep) -> str:
    config = _open(ctx)
    plans = approvals(ctx.app, config) if config else []
    if not plans or ctx.state["approval"]:
        return "Žádný plán ke schválení."
    n = len(plans[0]["plan"])
    if keep != "q" and not (isinstance(keep, list) and all(isinstance(i, int) and 0 <= i < n for i in keep)):
        raise ValueError(f"keep must be 'q' or indices 0–{n - 1}")
    ctx.state["approval"] = {plans[0]["id"]: keep if keep == "q" else sorted(set(keep))}
    return "Běh ukončen." if keep == "q" else f"Schváleno {len(set(keep))} z {n}."


def _web_answer(ctx: Ctx, iid: str, text: str) -> str:
    config = _open(ctx)
    if not config or iid in ctx.state["answers"] or iid not in {q["id"] for q in questions(ctx.app, config)}:
        return "Dotaz už není otevřený."
    ctx.state["answers"][iid] = text
    return "Odpověď uložena."


def act(ctx: Ctx, action: str, data: dict) -> str:
    """Dashboard buttons. Same thread rules as Telegram: state changes only under the lock."""
    with ctx.lock:
        _collect(ctx)
        if action in ("run", "go"):
            reply = _start_new(ctx, manual=True, discover=action == "go", only=_only(ctx, data.get("projects")))
        elif action in ("approve", "reject"):
            reply = _decide(ctx, str(data.get("task")), action == "approve", str(data.get("reason") or ""))
        elif action == "lesson":
            reply = _decide_lesson(ctx, f"{data.get('project')}/{data.get('id')}", bool(data.get("approve")))
        elif action == "plan":
            reply = _approve_plan(ctx, data.get("keep"))
        elif action == "answer":
            reply = _web_answer(ctx, str(data.get("id")), str(data.get("text") or ""))
        elif action in ("pause", "resume"):
            reply = _command(ctx, action)
        else:
            raise KeyError(action)
        if action in ("plan", "answer") and not ctx.busy:
            _advance(ctx)
        if ctx.state_path:
            save_state(ctx.state_path, ctx.state)
        return reply


# --- loop -------------------------------------------------------------------------------------------------

def tick(ctx: Ctx, updates: list[dict]) -> None:
    for u in updates:
        ctx.state["offset"] = max(ctx.state["offset"], u["update_id"] + 1)
        try:
            handle(ctx, u)
        except Exception as e:  # one bad update must not stop the daemon
            print(f"update {u.get('update_id')}: {type(e).__name__}: {e}", file=sys.stderr)
    try:
        _maybe_scan(ctx)
        # collect right before every busy check: the run thread may finish at any moment (e.g. during the scan)
        _collect(ctx)
        if not ctx.busy:
            _sync_questions(ctx)
            _advance(ctx)
        _collect(ctx)
        if not ctx.busy:
            _sync_questions(ctx)
        _notify_proposals(ctx)
        _notify_lessons(ctx)
        _maybe_digest(ctx)
    finally:  # the offset too: a handled update must not be delivered again after a failure
        if ctx.state_path:
            save_state(ctx.state_path, ctx.state)


def loop(ctx: Ctx) -> None:
    while True:
        try:
            updates = ctx.bot.updates(ctx.state["offset"], POLL_SECONDS)
        except TelegramError as e:
            print(f"telegram: {e}", file=sys.stderr)
            updates = []
            _time.sleep(5)
        try:
            with ctx.lock:
                tick(ctx, updates)
        except Exception as e:  # e.g. a hand-edited BACKLOG.md that no longer parses: keep running, tell once
            error = f"{type(e).__name__}: {e}"
            print(f"tick: {error}", file=sys.stderr)
            if error != ctx.last_error and say(ctx, f"⚠️ Démon narazil na chybu: {error[:500]}") is not None:
                ctx.last_error = error
        else:
            ctx.last_error = None
