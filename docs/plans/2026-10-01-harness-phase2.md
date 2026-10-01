# Harness fáze 2 — implementační plán

**Spec:** `docs/specs/2026-10-01-harness-phase2-design.md`

Každý krok: test → implementace → `python -m pytest` zelený → commit.

1. **Config:** `max_parallel`, `secondbrain {path, days}`, `Project.weight`. Testy v `test_projects.py`.
2. **Backlog:** stav `~` (návrh) v parseru; `add_tasks(text, project, lines)` připojí řádky na konec sekce projektu (sekci vytvoří). `next_task` nahradí `rank(tasks, projects, focus, weights)`.
3. **`harness/priority.py`:** `focus(secondbrain_path, projects, days, today)` (počet souborů `Thoughts/`, `Inbox/` se zmínkou projektu) a `pick_batch(ranked, n)` (max 1 úkol na projekt).
4. **`harness/scan.py`:** `scan_project(project)` → `git grep` na `origin/<base>` → návrhy s ID `<projekt>-todo-<hash6>`; `proposals_text`.
5. **Graf:** `WorkerState`, `build_worker`, rodič `pick_tasks` + `Send("worker")`; reducery `tasks_done`/`usage`/`quota`; step mód přes `interrupt_before` v obou grafech. `pick_task` → `pick_tasks` (vrací `batch`), `report` pod zámkem.
6. **Runner + events:** `subgraphs=True`, sloupec `worker`, migrace; `interrupted` podle `get_state().next`.
7. **Simulace:** per-task `nodes` ve scénáři, `Script` thread-safe; scénáře `parallel-projects`, `parallel-rate-limit`; `max_parallel` ve scénáři.
8. **CLI:** `queue`, `scan [--write]`; `run` předává `max_parallel` a fokus.
9. **Dashboard:** `/graph` vrací i `worker`; `/runs/{id}/state` vrací vnitřní `next`; frontend dva diagramy, víc aktivních uzlů, sloupec workera, `[~]`.
10. **Ověření:** pytest, CLI nad reálnou konfigurací, Playwright screenshot krokování, README + CHANGELOG.
