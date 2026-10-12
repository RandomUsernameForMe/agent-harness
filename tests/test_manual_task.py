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


def test_act_add_same_title_in_other_project(tmp_path):
    env = Env(tmp_path, "parallel-projects", window=None)
    a, b = list(env.ctx.cfg.projects)[:2]
    assert "přidán" in serve.act(env.ctx, "add", {"project": a, "title": "X"})
    assert "přidán" in serve.act(env.ctx, "add", {"project": b, "title": "X"})
    assert sorted(t.project for t in _tasks(env) if t.title == "X") == sorted([a, b])
    assert "už existuje" in serve.act(env.ctx, "add", {"project": a, "title": "x"})
    assert len([t for t in _tasks(env) if t.title.casefold() == "x"]) == 2


_BL = ("## proj\r\n- [ ] P2 a-1 Jiný\r\n  popis a\r\n- [~] P2 p-1 [bug] Starý\r\n  starý popis\r\n  Hotovo = staré\r\n"
       "  ACCEPT: pytest\r\n- [~] P3 p-2 Další\r\n  x\r\n")


def _edit(env, **kw):
    env.sim.backlog_path.write_bytes(_BL.encode())
    data = {"task": "p-1", "title": "Nový", "description": "r1\nr2", "done": "Test: ok", "priority": 1, "approve": True}
    return serve.act(env.ctx, "edit", data | kw)


def test_act_edit_approves_and_keeps_rest(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    _edit(env)
    new = env.sim.backlog_path.read_bytes().decode()
    assert new == _BL.replace("- [~] P2 p-1 [bug] Starý\r\n  starý popis\r\n  Hotovo = staré\r\n",
                              "- [ ] P1 p-1 [bug] Nový\r\n  r1\r\n  r2\r\n  Hotovo = Test: ok\r\n")


def test_act_edit_without_approve_stays_proposal(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    _edit(env, approve=False)
    t = next(t for t in _tasks(env) if t.id == "p-1")
    assert t.status == "~" and t.title == "Nový" and t.priority == 1


@pytest.mark.parametrize("kw", [{"title": "  "}, {"priority": 4}])
def test_act_edit_invalid(tmp_path, kw):
    env = Env(tmp_path, "happy-path", window=None)
    with pytest.raises(ValueError):
        _edit(env, **kw)
    assert env.sim.backlog_path.read_bytes().decode() == _BL


def test_act_edit_not_a_proposal(tmp_path):
    env = Env(tmp_path, "happy-path", window=None)
    assert _edit(env, task="a-1") == "už vyřízeno"
    assert env.sim.backlog_path.read_bytes().decode() == _BL
