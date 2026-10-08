# Harness — fáze 4: observabilita (design)

Datum: 2026-10-08 · Stav: návrh ke schválení. Sekce 1 (zachycení dat, úložiště) schválena v konverzaci; sekce 2–3 doplněny jako defaulty k revizi.

## 1. Účel

Z roadmapy (spec fáze 1): **Observabilita: tracing, nákladový ledger** — lekce LangGraphu: tracing, metadata (`config["metadata"]`).

Otázky, na které má fáze odpovědět (uživatel):
1. **Co agent dělal** — kroky jednoho volání (nástroje, výsledky, text) pro ladění špatného PR nebo zbytečného návrhu.
2. **Kolik stojí PR** — náklad na úkol / projekt / uzel / běh včetně cache tokenů.
3. **Data pro fázi 5** — výsledek úkolu (PR merged / closed / open, selhání, počet pokusů) svázaný s voláními modelu.

Zjištěno v datech (go-20261008-010216): uložené `input_tokens` = 2–26 na volání, protože `cache_read_input_tokens` / `cache_creation_input_tokens` se zahazují; `cost_usd` je správně (~2,1 USD za kolo). Kroky agenta se neukládají vůbec. Volání, které skončí chybou, se nezapíše.

## 2. Rozhodnutí

| Otázka | Rozhodnutí | Zdroj |
|---|---|---|
| Úložiště | vlastní tabulky v `harness.db` + pohledy v dashboardu; nic do cloudu | uživatel |
| Přístup | A: kontext trace z `config` LangGraphu, zápis přímo z `run_agent` přes sink; kroky mimo stav grafu | uživatel |
| Detail kroku | `tool_use`: název + vstup zkrácený na 300 znaků; `tool_result`: `is_error` + výstup 300 znaků; `text`: 1 000 znaků; `ThinkingBlock` se neukládá | default |
| Plný detail | uloží se `session_id` → dohledání v transkriptu Claude Code | default |
| Výsledek PR | `gh pr view` stav PR úkolů `[x]`; `[!]` = failed, `[?]` = unclear; synchronizace na začátku `run`/`go` a v `harness stats` | default |
| Simulace | falešné uzly `run_agent` nevolají → v simulaci žádné `agent_calls` | default |

## 3. Zachycení dat (`harness/sdk.py`)

`_collect` z proudu zpráv vytvoří kroky:
- `AssistantMessage` → `TextBlock` = krok `text`, `ToolUseBlock` = krok `tool_use` (`tool` = název, `summary` = `json.dumps(input)` zkrácený),
- `UserMessage` s `ToolResultBlock` → krok `tool_result` (`tool` = název spárovaný přes `tool_use_id`, `is_error`, `summary` = text výsledku zkrácený),
- `ResultMessage` → `usage` (vč. `cache_read_input_tokens`, `cache_creation_input_tokens`), `total_cost_usd`, `num_turns`, `duration_ms`, `session_id`, `is_error`.

Volání se zapíše **vždy** (`try/finally`): `status = ok | error | rate_limited`, `error` = text výjimky; kroky do okamžiku přerušení. Pak se výjimka propustí dál beze změny.

`WorkerResult.usage` dostane navíc `cache_read_tokens` a `cache_write_tokens` → usage ve stavu grafu, event logu i dashboardu „Spotřeba“ je zahrnuje.

## 4. Kontext trace (lekce: metadata)

- `harness/trace.py`: `contextvars.ContextVar` s `Trace(run_id, node, worker)`; `set_sink(fn)` / sink `None` = nezapisovat (testy, simulace).
- Wrapper `_pausing` v `graph.py` (obaluje `discover`, `prioritize` a přes `_safe` všechny workerové uzly) přijme `config: RunnableConfig` a před voláním uzlu nastaví kontext: `run_id = config["configurable"]["thread_id"]`, `node = config["metadata"]["langgraph_node"]`, `worker = state["task"]["id"]` nebo `state["project"]`.
- `run_agent` přečte kontext synchronně (před `asyncio.run`) a předá sinku záznam volání. Uzly se nemění.
- CLI (`_real_app`) nastaví sink = zápis do `harness.db`.

## 5. Úložiště (`harness/traces.py`)

```
agent_calls(id, run_id, worker, node, model, started, duration_ms, num_turns,
            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
            cost_usd, status, error, session_id)
agent_steps(call_id, seq, kind, tool, summary, is_error)
task_outcomes(task_id PRIMARY KEY, project, pr_url, state, checked)
```
- Tabulky se vytvoří v `events.connect` (stejná DB, WAL, stejný zámek).
- Funkce: `record(conn, call, steps)`, `calls(conn, run_id)`, `steps(conn, call_id)`, `costs(conn)` (agregace podle `worker` = úkol, + režie `discover`/`prioritize` na běh), `sync_outcomes(conn, tasks, pr_state)`.
- `pr_state(url) -> str` volá `gh pr view <url> --json state` (`OPEN|MERGED|CLOSED` → malá písmena); chyba → stav se nemění, varování na stderr.

## 6. Výstupy

- **`harness stats`** — synchronizuje výsledky a vypíše:
  - úkoly: `task_id`, projekt, stav (`merged/open/closed/failed/unclear`), pokusy (počet volání `implement`), cena USD, tokeny vč. cache, PR,
  - součty po projektech a uzlech, režie `discover`/`prioritize` po bězích.
- **Dashboard:**
  - `GET /runs/{id}/calls` → volání agentů běhu; `GET /calls/{id}/steps` → kroky,
  - `GET /costs` → tabulka z `costs()` vč. stavu PR,
  - v detailu běhu tabulka „Volání agentů“ (uzel, worker, model, tahy, tokeny, $, stav), klik → kroky; nová sekce „Cena za úkol“.

## 7. Testy a kritérium hotovosti

Unit testy:
- `_collect` nad falešným proudem zpráv: kroky (text, tool_use, tool_result spárovaný s názvem, zkracování), cache tokeny v usage, záznam při `is_error` i při rate limitu (status, kroky do přerušení) a výjimka se propustí,
- wrapper: kontext z `config` (thread_id, langgraph_node, worker z úkolu i z projektu),
- `traces`: `record`/`calls`/`steps`, `costs` (součet po úkolu, pokusy, režie), `sync_outcomes` s falešným `pr_state` (merged, closed, failed z `[!]`, chyba gh → beze změny),
- `harness stats` výpis, dashboard endpointy.

Hotovo, když:
1. `python -m pytest` zelené včetně nových testů.
2. `harness stats` ukáže stav PR akano3#2 a guth-pictures#14 (synchronizace funguje i bez trace).
3. Reálný běh jednoho malého úkolu (`harness run`) → `agent_calls` má řádky s `cache_read_tokens > 0` a `agent_steps` s nástroji; `harness stats` ukáže cenu toho úkolu; dashboard ukáže volání → kroky.

## 8. Mimo scope

LangSmith / Langfuse, plné výstupy nástrojů, thinking bloky, trace v simulaci, rozpočty a stropy nákladů, automatické vyhodnocení kvality PR (fáze 5).

## 9. Rizika

- **Zkrácené kroky nestačí na ladění** → `session_id` vede na plný transkript; limit znaků je konstanta.
- **Volání bez kontextu trace** (sink nastavený, kontext chybí — např. přímé volání mimo graf) → zapíše se s `run_id = None`, nespadne.
- **`gh` nedostupné** → `stats` vypíše náklady bez stavu PR.
