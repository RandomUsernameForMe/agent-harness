import pytest

from harness import serve
from harness.backlog import parse
from tests.test_serve import Env


def _tasks(env):
    return parse(env.sim.backlog_path.read_bytes().decode("utf-8"))


def test_act_add_task(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    p = next(iter(env.ctx.cfg.projects))
    reply = serve.act(env.ctx, "add", {"project": p, "title": "Přidat export", "description": "řádek 1\nřádek 2", "done": "Test: ok"})
    [t] = [t for t in _tasks(env) if t.title == "Přidat export"]
    assert t.status == " " and t.project == p and t.id in reply and t.id in serve._queue(env.ctx)
    assert t.description == "řádek 1\nřádek 2\nHotovo = Test: ok"


def test_telegram_task(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    p = next(iter(env.ctx.cfg.projects))
    env.tick(env.msg(f"/task {p} Jiný úkol\nřádek popisu"))
    [t] = [t for t in _tasks(env) if t.title == "Jiný úkol"]
    assert t.status == " " and t.description == "řádek popisu"


@pytest.mark.parametrize("text", ["/task neznamy x", "/task {p}", "/task {p}  "])
def test_telegram_task_errors_leave_backlog(tmp_path, text):
    env = Env(tmp_path, "happy-path", window=None)
    p = next(iter(env.ctx.cfg.projects))
    before = env.sim.backlog_path.read_bytes()
    env.tick(env.msg(text.format(p=p)))
    assert env.sim.backlog_path.read_bytes() == before
    reply = env.bot.texts()[-1]
    assert reply.startswith("❌") and "/task <projekt>" in reply and p in reply


def test_telegram_task_without_args_shows_usage(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    before = env.sim.backlog_path.read_bytes()
    env.tick(env.msg("/task"))
    reply = env.bot.texts()[-1]
    assert "/task <projekt>" in reply and all(n in reply for n in env.ctx.cfg.projects)
    assert env.sim.backlog_path.read_bytes() == before


def test_telegram_help_mentions_task(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    env.tick(env.msg("/help"))
    assert "/task" in env.bot.texts()[-1]


def test_act_add_unknown_project(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    with pytest.raises(ValueError):
        serve.act(env.ctx, "add", {"project": "neznamy", "title": "x"})


def _retry_env(tmp_path, status="!"):
    env = Env(tmp_path, "happy-path", window=None)
    env.sim.backlog_path.write_bytes(
        f"## p\n- [{status}] P2 a-1 Titulek — verify failed\n  popis\n".encode())
    return env


def test_act_retry_requeues_with_supplement(tmp_path):
    env = _retry_env(tmp_path)
    serve.act(env.ctx, "retry", {"task": "a-1", "text": "použij vitest"})
    text = env.sim.backlog_path.read_bytes().decode("utf-8")
    assert "- [ ] P2 a-1 Titulek\n" in text
    assert _tasks(env)[0].description == "popis\nDoplnění: použij vitest"


def test_act_retry_without_text_keeps_description(tmp_path):
    env = _retry_env(tmp_path, "?")
    serve.act(env.ctx, "retry", {"task": "a-1"})
    [t] = _tasks(env)
    assert t.status == " " and t.description == "popis"


@pytest.mark.parametrize("task,status", [("zzz", "!"), ("a-1", "x")])
def test_act_retry_rejects_unknown_or_not_failed(tmp_path, task, status):
    env = _retry_env(tmp_path, status)
    before = env.sim.backlog_path.read_bytes()
    with pytest.raises(ValueError):
        serve.act(env.ctx, "retry", {"task": task})
    assert env.sim.backlog_path.read_bytes() == before
