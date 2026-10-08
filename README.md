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

Fáze 3: když plánovač označí úkol za nejasný, worker se zeptá (uzel `clarify`, LangGraph `interrupt`) a běh skončí ve stavu `waiting`. Na dotaz se odpovídá v terminálu (`run` / `simulate`), v dashboardu nebo z Telegramu. Po 2 kolech dotazů nebo po přeskočení se úkol zapíše jako `[?]`. Zamítnutý návrh má stav `[-]`.

## Autonomní běh: `harness serve`

    .venv/Scripts/harness serve

Jde o jeden proces: Telegram bot (long polling, nepotřebuje veřejnou URL) a plánovač. Běhy se automaticky spouštějí jen v okně `serve.window` z `projects.yaml` (`22:00-07:00`), mimo něj jen přes `/run`. Démon pracuje takto:
- dotazy k nejasným úkolům posílá hned, odpovídá se reply na zprávu (po `question_timeout_hours` se dotaz přeskočí),
- návrhy `[~]` posílá s tlačítky ✅/❌,
- po konci okna pošle denní digest (PR, chyby, tokeny),
- jednou denně na začátku okna spustí `scan`,
- po rate limitu sám pokračuje,
- po restartu naváže na rozběhnutý běh,
- když běh spadne, vypne automatiku a pošle zprávu (zapne se příkazem `/resume`).

Příkazy: `/status /queue /proposals /run /pause /resume /digest /help`. Updaty z jiného chatu se ignorují.

Nastavení:
1. Přes [@BotFather](https://t.me/BotFather) založ **nový** bot (`/newbot`). Token secondbrain bota nepoužívej, dva pollery na jednom tokenu si navzájem kradou zprávy.
2. Napiš botovi a z `https://api.telegram.org/bot<TOKEN>/getUpdates` zjisti `chat.id`.
3. Nastav proměnné prostředí `HARNESS_TELEGRAM_TOKEN` a `HARNESS_TELEGRAM_CHAT_ID`, například `setx HARNESS_TELEGRAM_TOKEN ...`.
4. Pro autostart přidej v Task Scheduleru úlohu „Při přihlášení“ → `C:\Users\TGJ\Projects\harness\.venv\Scripts\harness.exe serve` se „Spustit v“ = složka harnessu.

Stav démona se ukládá do `serve_state.json` (je v gitignore).

Subscription: `ANTHROPIC_API_KEY` nesmí být nastavený. Izolaci workerů ověří `.venv/Scripts/python scripts/check_isolation.py`.

Design: `docs/specs/`, plán: `docs/plans/`.
