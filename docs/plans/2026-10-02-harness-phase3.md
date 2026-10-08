# Harness fáze 3 — implementační plán

**Spec:** `docs/specs/2026-10-02-harness-phase3-design.md`

**Goal:** autonomní `harness serve` ovládaný z Telegramu; nejasné úkoly se ptají přes LangGraph `interrupt`.

**Architecture:** worker subgraf dostane uzel `clarify` (interrupt/resume). Runner rozliší `paused`/`waiting` a resumuje vybrané interrupty. `serve.py` = tik (Telegram updaty → rozhodnutí → běh ve vlákně), všechny vedlejší efekty injektované (bot, hodiny, spawn), takže je testovatelný bez sítě.

**Tech Stack:** Python 3.11, LangGraph, stdlib `urllib` (Telegram), pytest.

Exekuce: inline (pod `/goal`). Každý krok: test → implementace → `python -m pytest` zelený → commit (s řádkem v CHANGELOG.md, stage zvlášť před commitem — hook).

## Global Constraints

- Žádná nová závislost.
- Token a chat id jen z env `HARNESS_TELEGRAM_TOKEN`, `HARNESS_TELEGRAM_CHAT_ID`.
- Max 2 kola upřesnění; timeout dotazu `serve.question_timeout_hours` (výchozí 24).
- Updaty z jiného chatu se ignorují.
- Scénáře fáze 1–2 beze změny výsledků (kromě `unclear-task`: `waiting` → přeskočit → stejné `[?]`).

## Review Focus

1. Odpověď přijde, když resume předchozí odpovědi ještě běží → odpověď se zařadí, nepoužije se na stale interrupt. Test v kroku 5 (`answers` čekají, dokud vlákno běží).
2. Zodpovězený interrupt hotového workera dál v `get_state().interrupts` → nesmí se poslat znovu ani resumovat. Test v kroku 2 (`parallel-clarify`).
3. Telegram nedostupný / 401 → smyčka nespadne, zkusí znovu. Test v kroku 4 + 5.
4. Okno přes půlnoc (`22:00-07:00`) → 23:00 i 06:59 v okně, 07:00 ne. Test v kroku 5.
5. Deterministický pád běhu → nesmí se restartovat dokola. Test v kroku 5 (pád → `paused`).

---

### 1. Backlog: stav `[-]` + sdílený zápis

**Files:** `harness/backlog.py`, `harness/nodes/common.py`, `tests/test_backlog.py`

- `TASK_RE` přijme `-`; `_title` bere `-` jako stav s možnou poznámkou (stejně jako `x!?`).
- `common.edit_backlog(path: Path, fn: Callable[[str], str]) -> None` — read-modify-write pod `_backlog_lock`; `report` ho použije.
- Test: `parse` vrátí `status == "-"`, `rank` ho nevybere.

### 2. Graf: uzel `clarify`

**Files:** `harness/graph.py`, `harness/nodes/real.py`, `harness/prompts/plan.md`, `harness/nodes/fake.py`, `harness/runner.py`, `scenarios/clarify-*.yaml`, `scenarios/parallel-clarify.yaml`, `tests/test_scenarios.py`, `tests/test_graph.py`

**Produces:**
- `WorkerState.clarifications: list[str]`; `MAX_CLARIFY = 2`; `WORKER_NODES` obsahuje `clarify` (před `setup`).
- `after_plan(state) -> "clarify" | "report" | "setup"`; `after_clarify(state) -> "plan" | "report"`.
- `clarify(state)`: `answer = interrupt({"question": last_failure, "task": id, "title", "project"})`; neprázdná → `{"outcome": None, "last_failure": "", "clarifications": [*old, answer]}`, jinak `{}`.
- `runner.open_interrupts(app, config) -> list[Interrupt]` (tasky s `result is None`), `runner.questions(app, config) -> list[dict]` (`{"id", "task", "title", "project", "question"}`).
- `execute(app, inputs, config, conn, simulated, answers: dict[str, str] | None = None) -> "paused" | "waiting" | "interrupted" | "done"`; event `waiting` s `delta.questions`.
- `real.plan` + `plan.md`: `{clarifications}` blok. `fake.plan` zapíše `clarifications` do výsledku, aby test viděl, že plan je dostal.

Testy (scénáře):
- `unclear-task`: `waiting`; resume `{id: ""}` → `[?]`, `UNCLEAR: Co přesně měřit?`, `setup` 0×.
- `clarify-answered` (`plan: [{unclear: …}, ok]`): odpověď `"měř LCP"` → `[x]`, `plan` 2×.
- `clarify-max-rounds` (`plan: {unclear: …}`): 2 odpovědi → `[?]`, `plan` 3×, žádný třetí dotaz.
- `parallel-clarify`: a1 nejasné, b1 projde → `waiting`, b1 `[x]`, a1 `[ ]`; `questions` vrací jen a1; po odpovědi `done`, a1 `[x]`, b1 `open_pr` 1×.
- smíšené (`clarify-rate-limit`): rate limit + dotaz → `paused`, dotaz je v `questions`; jeden resume s odpovědí → `done`.

### 3. CLI + dashboard: odpověď na dotaz

**Files:** `harness/cli.py`, `harness/dashboard/app.py`, `harness/dashboard/static/index.html`, `tests/test_cli.py`, `tests/test_dashboard.py`

- `cmd_simulate` / `cmd_run`: při `waiting` pro každý dotaz `input("<task>: <question> (Enter = přeskočit) > ")` a `execute(..., answers=…)`.
- `/runs/{id}/state` + `questions`; `POST /runs/{id}/answer {id, answer}` (409 když běží, 404 neznámý dotaz) → resume ve vlákně.
- Frontend: blok s otázkou, input, tlačítka Odpovědět / Přeskočit.
- Test: dashboard `unclear-task` → `questions` neprázdné → answer `""` → `run_end`, backlog `?`.

### 4. Telegram klient

**Files:** `harness/telegram.py`, `tests/test_telegram.py`

**Produces:** `Telegram(token: str, chat_id: int)` (frozen dataclass) s metodami
`updates(offset: int, timeout: int) -> list[dict]`, `send(text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int` (message_id),
`edit(message_id: int, text: str) -> None`, `ack(callback_id: str, text: str = "") -> None`. Text > 4096 se ořízne. `TelegramError` pro `ok: false` / HTTP chybu.
Test: mock `urllib.request.urlopen` — URL, JSON payload (`reply_markup.inline_keyboard`), oříznutí, `HTTPError 401` → `TelegramError`.

### 5. Démon

**Files:** `harness/serve.py`, `harness/projects.py` (`Config.serve`), `harness/cli.py` (`serve`, `scan_all`), `projects.yaml`, `.gitignore`, `tests/test_serve.py`

**Consumes:** krok 1–4. **Produces:**
- `projects.Serve(window: tuple[time, time] | None, question_timeout_hours: float, scan: bool)`; `Config.serve: Serve`.
- `serve.in_window(t: time, window) -> bool`.
- `serve.Ctx` (dataclass): `home, cfg, bot, app, backlog_path, conn, new_inputs: Callable[[str], dict], state: dict, spawn: Callable[[Callable], None], now: Callable[[], datetime], scan: Callable[[], int]`.
- `serve.tick(ctx, updates: list[dict]) -> None`, `serve.handle(ctx, update)`, `serve.digest_text(conn, since: float, backlog_text: str) -> str`, `serve.load_state(path) / save_state(path, state)`, `serve.loop(ctx)`.
- `cli.scan_all(cfg, text, limit=5) -> tuple[str, int]` (vytaženo z `cmd_scan`).

Testy (fake bot zaznamenává `send/edit/ack`, `spawn = lambda f: f()`, simulované uzly):
okno přes půlnoc; mimo okno žádný start, `/run` startuje; `/pause` blokuje; dotaz se pošle 1×, reply → resume → `[x]`; volný text s 1 dotazem = odpověď; timeout → `[?]` + zpráva; tlačítko ✅/❌ → `[ ]`/`[-]` + `edit`; nové `[~]` se pošle 1×; digest po konci okna 1× denně; `paused` → resume až po `resets_at`; stav `running` po restartu → resume; cizí chat ignorován; výjimka v běhu → `paused=True` + zpráva; odpověď během běžícího vlákna čeká ve frontě; `TelegramError` v `send` nespadne tik.

### 6. Ověření a docs

- `python -m pytest`, `harness serve` bez tokenu → chyba, s neplatným tokenem → 401 hlášeno, běží dál (ukončit).
- README (serve, env, BotFather, Task Scheduler), CHANGELOG, spec stav.
