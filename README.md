# harness

LangGraph orchestrátor: úkol z `BACKLOG.md` → plán (Opus) → implementace (Sonnet) ve worktree → verify → draft PR. Modely běží přes Claude Agent SDK na subscription.

    python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
    .venv/Scripts/harness simulate flaky-tests [--step]
    .venv/Scripts/harness dashboard          # http://127.0.0.1:8765
    .venv/Scripts/harness run [--resume RUN_ID]
    .venv/Scripts/harness prune              # smaže worktree hotových úkolů
    .venv/Scripts/harness queue              # seřazená fronta (P → fokus ze secondbrainu → weight)
    .venv/Scripts/harness scan [--write]     # TODO/FIXME z origin/<base> → návrhy [~] v BACKLOGu

Fáze 2: `run` zpracovává až `max_parallel` úkolů z různých projektů najednou (LangGraph `Send` → worker subgraf). Návrh `[~]` se nespustí, dokud ho ručně nezměníš na `[ ]`. Běhy z fáze 1 nejdou resumovat novým grafem.

Subscription: `ANTHROPIC_API_KEY` nesmí být nastavený. Izolaci workerů ověří `.venv/Scripts/python scripts/check_isolation.py`.

Design: `docs/specs/`, plán: `docs/plans/`.
