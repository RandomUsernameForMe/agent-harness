import time as _time
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from harness import events, serve
from harness.backlog import parse
from harness.projects import Serve
from harness.simulate import prepare
from harness.telegram import TelegramError

SCENARIOS = Path(__file__).parent.parent / "scenarios"
CHAT = 1
NIGHT = datetime(2026, 10, 2, 23, 0)
NOON = datetime(2026, 10, 2, 12, 0)


class FakeBot:
    chat_id = CHAT

    def __init__(self):
        self.sent, self.edits, self.acks, self.down, self._id = [], [], [], False, 100

    def send(self, text, buttons=None):
        if self.down:
            raise TelegramError("sendMessage: down")
        self._id += 1
        self.sent.append({"id": self._id, "text": text, "buttons": buttons})
        return self._id

    def edit(self, message_id, text):
        self.edits.append((message_id, text))

    def ack(self, callback_id, text=""):
        self.acks.append((callback_id, text))

    def texts(self):
        return [m["text"] for m in self.sent]


class Env:
    def __init__(self, tmp_path, scenario, now=NIGHT, window=(time(22), time(7)), timeout=24, scan=None):
        path = scenario if isinstance(scenario, Path) else SCENARIOS / f"{scenario}.yaml"
        self.sim = prepare(path, tmp_path, InMemorySaver(), delay=0)
        self.clock = [now]
        self.bot = FakeBot()
        self.scans = []
        cfg = replace(self.sim.cfg, serve=Serve(window, timeout, scan is not None))
        state = serve.default_state(now) | {"digest_since": 0}
        self.ctx = serve.Ctx(cfg=cfg, bot=self.bot, app=self.sim.app, backlog_path=self.sim.backlog_path,
                             conn=events.connect(tmp_path / "h.db"), new_inputs=lambda rid: self.sim.inputs | {"run_id": rid},
                             state=state, spawn=lambda f: f(), now=lambda: self.clock[0],
                             scan=scan or (lambda: self.scans.append(1) or 0), simulated=True)
        self._update = 0

    def tick(self, *updates):
        serve.tick(self.ctx, list(updates))

    def statuses(self):
        return {t.id: t.status for t in parse(self.sim.backlog_path.read_bytes().decode("utf-8"))}

    def msg(self, text, reply_to=None, chat=CHAT):
        self._update += 1
        m = {"message_id": 1000 + self._update, "chat": {"id": chat}, "text": text}
        if reply_to:
            m["reply_to_message"] = {"message_id": reply_to}
        return {"update_id": self._update, "message": m}

    def press(self, data, message, chat=CHAT):
        self._update += 1
        return {"update_id": self._update, "callback_query": {
            "id": f"cb{self._update}", "data": data,
            "message": {"message_id": message["id"], "chat": {"id": chat}, "text": message["text"]}}}

    def question_msgs(self):
        return [m for m in self.bot.sent if m["text"].startswith("❓")]


def test_in_window():
    night = (time(22), time(7))
    assert serve.in_window(time(23), night) and serve.in_window(time(6, 59), night)
    assert not serve.in_window(time(7), night) and not serve.in_window(time(12), night)
    day = (time(9), time(17))
    assert serve.in_window(time(9), day) and not serve.in_window(time(17), day)
    assert not serve.in_window(time(12), None)


def test_auto_start_in_window_runs_quietly(tmp_path):
    env = Env(tmp_path, "happy-path")
    env.tick()
    assert env.statuses() == {"sim-01": "x"}
    assert env.ctx.state["run"]["status"] == "done"
    assert env.bot.sent == []  # results go to the digest, not straight to the phone
    env.tick()
    assert len(events.runs(env.ctx.conn)) == 1  # empty queue → no new run


def test_outside_window_only_manual_run(tmp_path):
    env = Env(tmp_path, "happy-path", now=NOON)
    env.tick()
    assert env.statuses() == {"sim-01": " "}
    env.tick(env.msg("/run"))
    assert env.statuses() == {"sim-01": "x"}
    assert any("Spouštím" in t for t in env.bot.texts())


def test_no_window_means_no_auto_start(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    env.tick()
    assert env.statuses() == {"sim-01": " "}


def test_pause_and_resume(tmp_path):
    env = Env(tmp_path, "happy-path")
    env.tick(env.msg("/pause"))
    assert env.statuses() == {"sim-01": " "}
    env.tick(env.msg("/resume"))
    assert env.statuses() == {"sim-01": "x"}


def test_question_sent_once_and_reply_resumes(tmp_path):
    env = Env(tmp_path, "clarify-answered")
    env.tick()
    [q] = env.question_msgs()
    assert "Co přesně měřit?" in q["text"] and "sim-01" in q["text"]
    assert q["buttons"][0][0][0] == "Přeskočit"
    env.tick()
    assert len(env.question_msgs()) == 1
    env.tick(env.msg("měř LCP", reply_to=q["id"]))
    assert env.statuses() == {"sim-01": "x"}
    assert env.sim.script.calls["plan"] == 2
    assert env.ctx.state["asked"] == {} and env.ctx.state["answers"] == {}


def test_free_text_answers_the_only_question(tmp_path):
    env = Env(tmp_path, "clarify-answered")
    env.tick()
    env.tick(env.msg("měř LCP"))
    assert env.statuses() == {"sim-01": "x"}


def test_skip_button(tmp_path):
    env = Env(tmp_path, "unclear-task")
    env.tick()
    [q] = env.question_msgs()
    env.tick(env.press(q["buttons"][0][0][1], q))
    assert env.statuses() == {"sim-01": "?"}
    assert env.bot.acks and env.bot.edits[0][0] == q["id"]


def test_question_timeout_skips(tmp_path):
    env = Env(tmp_path, "unclear-task", timeout=24)
    env.tick()
    env.clock[0] += timedelta(hours=23)
    env.tick()
    assert env.statuses() == {"sim-01": " "}
    env.clock[0] += timedelta(hours=2)
    env.tick()
    assert env.statuses() == {"sim-01": "?"}
    assert any(t.startswith("⏱") for t in env.bot.texts())


def test_answer_waits_while_run_thread_busy(tmp_path):
    sc = tmp_path / "two.yaml"
    sc.write_text("name: dva dotazy\nmax_parallel: 2\ntasks:\n"
                  "  - {project: alpha, id: a-1, title: A, priority: P1, nodes: {plan: [{unclear: 'A?'}, ok]}}\n"
                  "  - {project: beta, id: b-1, title: B, priority: P1, nodes: {plan: [{unclear: 'B?'}, ok]}}\n",
                  encoding="utf-8")
    env = Env(tmp_path, sc)
    env.tick()
    qa, qb = sorted(env.question_msgs(), key=lambda m: m["text"])
    jobs = []
    env.ctx.spawn = jobs.append
    env.tick(env.msg("A1", reply_to=qa["id"]))
    assert len(jobs) == 1 and env.ctx.busy
    env.tick(env.msg("B1", reply_to=qb["id"]))
    assert len(jobs) == 1  # queued, not a second concurrent resume
    jobs.pop()()
    env.tick()
    assert len(jobs) == 1
    jobs.pop()()
    env.tick()
    assert env.statuses() == {"a-1": "x", "b-1": "x"}


def test_proposals_sent_once_then_approved_and_rejected(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    with env.sim.backlog_path.open("a", encoding="utf-8") as f:
        f.write("## demo2\n- [~] P3 p-1 TODO: jedna\n  Popis návrhu.\n- [~] P3 p-2 TODO: dva\n")
    env.tick()
    props = [m for m in env.bot.sent if m["text"].startswith("💡")]
    assert len(props) == 2 and "Popis návrhu." in props[0]["text"]
    env.tick()
    assert len([m for m in env.bot.sent if m["text"].startswith("💡")]) == 2
    approve, reject = props[0]["buttons"][0][0][1], props[1]["buttons"][0][1][1]
    env.tick(env.press(approve, props[0]), env.press(reject, props[1]))
    assert env.statuses()["p-1"] == " " and env.statuses()["p-2"] == "-"
    assert [e[1].split("\n\n→ ", 1)[1] for e in env.bot.edits] == [
        "schváleno → fronta", "zamítnuto (důvod můžeš poslat reply na tuto zprávu)"]
    env.tick(env.press(approve, props[0]))
    assert env.bot.acks[-1][1] == "už vyřízeno"
    env.tick(env.msg("duplicitní s gp-02", reply_to=props[1]["id"]))
    assert "důvod uložen" in env.bot.texts()[-1]
    [p2] = [t for t in parse(env.sim.backlog_path.read_bytes().decode("utf-8")) if t.id == "p-2"]
    assert p2.status == "-" and p2.note == "zamítnuto: duplicitní s gp-02"


def test_proposals_command_resends(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    with env.sim.backlog_path.open("a", encoding="utf-8") as f:
        f.write("## demo2\n- [~] P3 p-1 TODO: jedna\n")
    env.tick()
    env.tick(env.msg("/proposals"))
    assert len([m for m in env.bot.sent if m["text"].startswith("💡")]) == 2


def test_digest_once_after_window(tmp_path):
    env = Env(tmp_path, "happy-path")
    env.tick()
    env.clock[0] = datetime(2026, 10, 3, 6, 0)
    env.tick()
    assert not [t for t in env.bot.texts() if t.startswith("📋")]
    env.clock[0] = datetime(2026, 10, 3, 7, 5)
    env.tick()
    env.tick()
    [digest] = [t for t in env.bot.texts() if t.startswith("📋")]
    assert "✅ sim-01 → https://example.invalid/sim/sim-01" in digest
    assert "Fronta: 0" in digest


def test_digest_text_reports_failures_and_unclear(tmp_path):
    env = Env(tmp_path, "retries-exhausted")
    env.tick()
    text = serve.digest_text(env.ctx.conn, 0, env.sim.backlog_path.read_text(encoding="utf-8"), simulated=True)
    assert "❌ sim-01 — failed: tsc: 3 type errors" in text


def test_digest_text_separates_estimate_from_paid(tmp_path):
    conn = events.connect(tmp_path / "h.db")
    u = {"node": "plan", "input_tokens": 1, "output_tokens": 1, "cost_usd": 1.0}
    events.log(conn, "r1", "end", node="plan", usage=[u, u | {"billing": "api", "cost_usd": 0.25}])
    assert "Tokeny: 4, ~$1.00 odhad (subscription) + $0.25 zaplaceno (API)" in serve.digest_text(conn, 0, "")


def test_rate_limit_resumes_only_after_reset(tmp_path):
    env = Env(tmp_path, "rate-limit-pause-resume")
    env.tick()
    run = env.ctx.state["run"]
    assert run["status"] == "paused" and run["resets_at"] > _time.time()
    env.clock[0] = datetime.fromtimestamp(run["resets_at"] - 30)  # the scenario's reset is real time
    env.tick()
    assert env.sim.script.calls["implement"] == 1
    env.clock[0] = datetime.fromtimestamp(run["resets_at"] + 1)
    env.tick()
    assert env.statuses() == {"sim-01": "x"}


def test_restart_resumes_interrupted_run(tmp_path):
    env = Env(tmp_path, "rate-limit-pause-resume")
    env.tick()
    env.ctx.state["run"]["status"] = "running"  # the daemon died while the run was going
    env.tick()
    assert env.statuses() == {"sim-01": "x"}


def test_foreign_chat_ignored(tmp_path):
    env = Env(tmp_path, "happy-path", now=NOON)
    env.tick(env.msg("/run", chat=999))
    assert env.statuses() == {"sim-01": " "} and env.bot.sent == []


def test_crash_pauses_automation_and_notifies(tmp_path):
    env = Env(tmp_path, "happy-path")

    class Broken:
        def stream(self, *a, **kw):
            raise RuntimeError("checkpoint db locked")
    env.ctx.app = Broken()
    env.tick()
    assert env.ctx.state["paused"] and env.ctx.state["run"]["status"] == "crashed"
    assert any("spadl" in t and "checkpoint db locked" in t for t in env.bot.texts())
    env.tick()
    assert len([t for t in env.bot.texts() if "spadl" in t]) == 1  # no restart loop


def test_telegram_down_retries_question_later(tmp_path):
    env = Env(tmp_path, "clarify-answered")
    env.bot.down = True
    env.tick()
    assert env.ctx.state["asked"] == {}
    env.bot.down = False
    env.tick()
    assert len(env.question_msgs()) == 1


def test_status_and_help(tmp_path):
    env = Env(tmp_path, "clarify-answered")
    env.tick()
    env.tick(env.msg("/status"), env.msg("/nesmysl"))
    status, help_text = env.bot.texts()[-2:]
    assert "22:00–07:00" in status and "waiting" in status and "Dotazy: 1" in status
    assert "/status" in help_text


def test_daily_scan_in_window_once(tmp_path):
    env = Env(tmp_path, "happy-path", scan=lambda: env.scans.append(1) or 0)
    env.tick()
    env.tick()
    assert env.scans == [1]


def test_state_roundtrip(tmp_path):
    p = tmp_path / "serve_state.json"
    st = serve.load_state(p, NIGHT)
    st["asked"]["x"] = {"message_id": 1, "task": "t", "at": 1.0}
    serve.save_state(p, st)
    assert serve.load_state(p, NIGHT)["asked"] == {"x": {"message_id": 1, "task": "t", "at": 1.0}}


def test_quota_threshold_holds_auto_start_until_reset(tmp_path):
    env = Env(tmp_path, "happy-path")
    reset = (NIGHT + timedelta(hours=2)).timestamp()
    events.log(env.ctx.conn, "earlier", "end", node="implement", simulated=True,
               rate_limit={"type": "five_hour", "utilization": 0.9, "resets_at": reset})
    env.tick()
    assert env.statuses() == {"sim-01": " "}  # 0.9 ≥ 0.7: keep the user's reserve
    env.clock[0] = NIGHT + timedelta(hours=2, minutes=1)
    env.tick()
    assert env.statuses() == {"sim-01": "x"}


def test_live_quota_overrides_stale_sdk_utilization(tmp_path):
    env = Env(tmp_path, "happy-path")
    reset = (NIGHT + timedelta(hours=2)).timestamp()
    events.log(env.ctx.conn, "earlier", "end", node="implement", simulated=True,
               rate_limit={"type": "five_hour", "utilization": 0.9, "resets_at": reset})
    env.ctx.live_quota = lambda: {"five_hour": {"utilization": 0.3, "resets_at": reset}}
    env.tick()
    assert env.statuses() == {"sim-01": "x"}  # endpoint says 30 % now: the old 90 % no longer holds the start


def test_live_quota_holds_auto_start(tmp_path):
    env = Env(tmp_path, "happy-path")
    env.ctx.live_quota = lambda: {"five_hour": {"utilization": 0.8, "resets_at": (NIGHT + timedelta(hours=1)).timestamp()}}
    env.tick()
    assert env.statuses() == {"sim-01": " "}


def test_manual_run_ignores_quota_reserve(tmp_path):
    env = Env(tmp_path, "happy-path", now=NOON)
    events.log(env.ctx.conn, "earlier", "end", node="implement", simulated=True,
               rate_limit={"utilization": 0.9, "resets_at": (NOON + timedelta(hours=2)).timestamp()})
    env.tick(env.msg("/run"))
    assert env.statuses() == {"sim-01": "x"}


def test_run_finishing_mid_tick_is_collected_not_restarted(tmp_path):
    env = Env(tmp_path, "happy-path")
    jobs = []
    env.ctx.spawn = jobs.append
    env.ctx.state["last_scan"] = env.clock[0].date().isoformat()
    env.tick()
    assert len(jobs) == 1
    env.ctx.state["last_scan"] = None
    env.ctx.cfg = replace(env.ctx.cfg, serve=replace(env.ctx.cfg.serve, scan=True))
    env.ctx.scan = lambda: jobs.pop()() or 0  # the run thread finishes between two checks of one tick
    env.tick()
    assert jobs == []  # collected as done, not mistaken for a run the daemon lost on restart
    assert env.ctx.state["run"]["status"] == "done" and not env.ctx.busy
    assert [r["last_kind"] for r in events.runs(env.ctx.conn)] == ["run_end"]


def test_unexpected_error_in_tick_is_reported_once_and_loop_survives(tmp_path, monkeypatch):
    env = Env(tmp_path, "happy-path", window=None)
    env.sim.backlog_path.write_text("## demo\n- [ ] P1 d-1 A\n- [ ] P1 d-1 B\n", encoding="utf-8")  # duplicate id
    calls = []

    def updates(offset, timeout):
        calls.append(offset)
        if len(calls) > 3:
            raise KeyboardInterrupt
        return []
    env.bot.updates = updates
    with pytest.raises(KeyboardInterrupt):
        serve.loop(env.ctx)
    assert len(calls) == 4
    errors = [t for t in env.bot.texts() if t.startswith("⚠️")]
    assert len(errors) == 1 and "duplicate task id" in errors[0]


def test_state_saved_even_when_tick_fails(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    env.ctx.state_path = tmp_path / "serve_state.json"
    env.sim.backlog_path.write_text("## demo\n- [ ] P1 d-1 A\n- [ ] P1 d-1 B\n", encoding="utf-8")
    with pytest.raises(ValueError):
        env.tick(env.msg("ahoj"))
    assert serve.load_state(env.ctx.state_path, NIGHT)["offset"] == 2  # the update is not delivered again


def test_scan_once_per_window_across_midnight(tmp_path):
    env = Env(tmp_path, "happy-path", scan=lambda: env.scans.append(1) or 0)
    env.tick()
    env.clock[0] = datetime(2026, 10, 3, 1, 0)
    env.tick()
    assert env.scans == [1]
    env.clock[0] = datetime(2026, 10, 3, 22, 0)
    env.tick()
    assert env.scans == [1, 1]


def test_reply_to_question_being_resumed_is_not_lost_silently(tmp_path):
    env = Env(tmp_path, "clarify-answered")
    env.tick()
    [q] = env.question_msgs()
    jobs = []
    env.ctx.spawn = jobs.append
    env.tick(env.msg("měř LCP", reply_to=q["id"]))
    env.tick(env.msg("vlastně TTFB", reply_to=q["id"]))
    assert "už mám" in env.bot.texts()[-1]


def test_dashboard_go_waits_for_plan_then_runs_kept_tasks(tmp_path):
    env = Env(tmp_path, "go-happy", now=NOON)
    assert "Spouštím go-" in serve.act(env.ctx, "go", {})
    snap = serve.snapshot(env.ctx)
    assert snap["run"]["status"] == "approving" and len(snap["plan"]["plan"]) == 3
    env.tick()
    assert serve.snapshot(env.ctx)["run"]["status"] == "approving"  # no tick resumes without the owner
    assert serve.act(env.ctx, "run", {}).startswith("Běh už probíhá")
    with pytest.raises(ValueError):
        serve.act(env.ctx, "plan", {"keep": [7]})
    assert serve.act(env.ctx, "plan", {"keep": [0, 2]}) == "Schváleno 2 z 3."
    snap = serve.snapshot(env.ctx)
    assert snap["run"]["status"] == "done" and snap["plan"] is None
    assert sorted(env.statuses().values()) == ["-", "x", "x", "~"]


def test_dashboard_plan_quit_ends_run(tmp_path):
    env = Env(tmp_path, "go-happy", now=NOON)
    serve.act(env.ctx, "go", {})
    assert serve.act(env.ctx, "plan", {"keep": "q"}) == "Běh ukončen."
    assert serve.snapshot(env.ctx)["run"]["status"] == "done"
    assert serve.act(env.ctx, "plan", {"keep": "q"}) == "Žádný plán ke schválení."


def test_dashboard_answers_question_and_decides_proposal(tmp_path):
    env = Env(tmp_path, "clarify-answered", now=NOON)
    serve.act(env.ctx, "run", {})
    [q] = serve.snapshot(env.ctx)["questions"]
    assert serve.act(env.ctx, "answer", {"id": "nope", "text": "x"}) == "Dotaz už není otevřený."
    assert serve.act(env.ctx, "answer", {"id": q["id"], "text": "měř LCP"}) == "Odpověď uložena."
    assert env.statuses() == {"sim-01": "x"}
    with pytest.raises(KeyError):
        serve.act(env.ctx, "nope", {})


def test_dashboard_pause_resume(tmp_path):
    env = Env(tmp_path, "happy-path", now=NOON)
    serve.act(env.ctx, "pause", {})
    assert serve.snapshot(env.ctx)["paused"] is True
    serve.act(env.ctx, "resume", {})
    assert serve.snapshot(env.ctx)["paused"] is False


def test_lessons_sent_and_decided_from_telegram_and_dashboard(tmp_path):
    from langgraph.store.memory import InMemoryStore
    from harness import lessons
    env = Env(tmp_path, "happy-path", window=None)
    env.ctx.store = store = InMemoryStore()
    a = lessons.propose(store, "demo", "Lekce A", [])
    b = lessons.propose(store, "demo", "Lekce B", [])
    env.tick()
    msgs = [m for m in env.bot.sent if m["text"].startswith("🧠")]
    assert [m["text"].split("\n")[1] for m in msgs] == ["Lekce A", "Lekce B"]
    env.tick()
    assert len([m for m in env.bot.sent if m["text"].startswith("🧠")]) == 2  # sent once
    env.tick(env.press(msgs[0]["buttons"][0][0][1], msgs[0]))
    assert env.bot.acks[-1][1] == "lekce platí"
    assert serve.act(env.ctx, "lesson", {"project": "demo", "id": b, "approve": False}) == "lekce vyřazena"
    assert lessons.active_texts(store, "demo") == ["Lekce A"]
    assert [x["id"] for x in serve.snapshot(env.ctx)["lessons"]] == [a]  # retired ones hidden


def test_dashboard_reject_with_reason(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    with env.sim.backlog_path.open("a", encoding="utf-8") as f:
        f.write("## demo2\n- [~] P3 p-1 Návrh\n")
    assert serve.act(env.ctx, "reject", {"task": "p-1", "reason": "  mimo\n fokus "}) == "zamítnuto"
    [p1] = [t for t in parse(env.sim.backlog_path.read_bytes().decode("utf-8")) if t.id == "p-1"]
    assert p1.note == "zamítnuto: mimo fokus"


def test_dashboard_run_only_selected_projects(tmp_path):
    env = Env(tmp_path, "parallel-projects", now=NOON)
    with pytest.raises(ValueError):
        serve.act(env.ctx, "run", {"projects": ["nope"]})
    with pytest.raises(ValueError):
        serve.act(env.ctx, "run", {"projects": []})
    assert "Spouštím run-" in serve.act(env.ctx, "run", {"projects": ["beta"]})
    assert env.statuses() == {"sim-a1": " ", "sim-b1": "x", "sim-a2": " "}
    assert serve.act(env.ctx, "run", {"projects": ["beta"]}) == "Fronta je prázdná."


def test_dashboard_run_limits_override_config(tmp_path):
    env = Env(tmp_path, "parallel-projects", now=NOON)
    for bad in (0, -1, "2", True, 1.5):
        with pytest.raises(ValueError):
            serve.act(env.ctx, "run", {"max_tasks": bad})
        with pytest.raises(ValueError):
            serve.act(env.ctx, "run", {"max_parallel": bad})
    serve.act(env.ctx, "run", {"max_tasks": 1, "max_parallel": 1})
    assert sorted(env.statuses().values()) == [" ", " ", "x"]


def test_dashboard_go_only_features_lands_kind_in_backlog(tmp_path):
    env = Env(tmp_path, "go-happy", now=NOON)
    for bad in (["bugs"], [], "feature"):
        with pytest.raises(ValueError):
            serve.act(env.ctx, "go", {"lenses": bad})
    serve.act(env.ctx, "go", {"lenses": ["feature"]})
    serve.act(env.ctx, "plan", {"keep": "q"})
    new = [t for t in parse(env.sim.backlog_path.read_bytes().decode("utf-8")) if t.id != "sim-01"]
    assert [(t.title, t.kind, t.status) for t in new] == [("Export galerie do ZIP", "feature", "~")]


def test_dashboard_cleanup_review_proposes_refactors_and_records_date(tmp_path):
    env = Env(tmp_path, "go-happy", now=NOON)
    serve.act(env.ctx, "go", {"lenses": ["cleanup"], "projects": ["web"]})
    serve.act(env.ctx, "plan", {"keep": "q"})
    new = [t for t in parse(env.sim.backlog_path.read_bytes().decode("utf-8")) if t.id != "sim-01"]
    assert [(t.project, t.title, t.kind, t.status) for t in new] == [("web", "Rozdělit render() v app.ts", "refactor", "~")]
    assert set(events.last_cleanups(env.ctx.conn, simulated=True)) == {"web"}
    assert events.last_cleanups(env.ctx.conn) == {}  # simulated runs never count for real projects


def test_dashboard_go_only_selected_projects(tmp_path):
    env = Env(tmp_path, "go-happy", now=NOON)
    serve.act(env.ctx, "go", {"projects": ["web"]})
    plan = serve.snapshot(env.ctx)["plan"]["plan"]
    assert plan and {p["project"] for p in plan} == {"web"}
