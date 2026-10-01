## [2026-10-01] - Design spec fáze 1 (jedna smyčka, dashboard, simulace)
## [2026-10-01] - Implementační plán fáze 1
## [2026-10-01] - Scaffold harness balíčku a načítání projects.yaml
## [2026-10-01] - BACKLOG.md parser a zápis stavu
## [2026-10-01] - Guard pro diff a baseline selhání
## [2026-10-01] - SQLite event log
## [2026-10-01] - LangGraph graf a sdílené uzly pick_task/report
## [2026-10-01] - Simulace: falešné uzly, 8 scénářů, runner s event logem
## [2026-10-01] - CLI: harness simulate (plynulý i krokovací režim)
## [2026-10-01] - Agent SDK wrapper (subscription, rate limit), prompty, izolační spike: apiKeySource=none, bez globálních pluginů, claude.ai konektory vypnuté přes strict_mcp_config
## [2026-10-01] - Skutečné uzly (worktree, Agent SDK, verify, draft PR), harness run/prune
## [2026-10-01] - Dashboard API (FastAPI, SSE, spouštění simulací)
## [2026-10-01] - Dashboard frontend (graf, běhy, backlog, spotřeba, krokování); /state vrací running, tlačítko kroku čeká na doběhnutí
## [2026-10-01] - Pilotní konfigurace (ověřené verify příkazy), BACKLOG s gp-01/gp-02, README
## [2026-10-01] - První reálný běh: PR guth-pictures#6 a #7 (pauza na limit + resume, kill + resume)
## [2026-10-01] - Opravy z finálního review: timeout zabíjí strom procesů, billing kontrola i při --resume, guard vidí přesuny, název s — se nezkracuje, tělo PR přes soubor, worker nemůže push/gh
## [2026-10-01] - Design spec a plán fáze 2 (paralelní workers, priority napříč projekty, sken)
## [2026-10-01] - Fáze 2: worker subgraf + Send fan-out (max_parallel, 1 úkol/projekt/dávku), pauza na rate limit přes interrupt (sourozenci doběhnou), ranking P → fokus secondbrainu → weight, harness queue/scan, návrhy [~], sloupec worker v event logu, dashboard se dvěma diagramy
## [2026-10-01] - První reálný paralelní běh fáze 2: PR guth-pictures#8 a akano3#1 souběžně, verify zelené napoprvé
## [2026-10-02] - Design spec fáze 3 (Telegram, dotazy přes interrupt, démon serve)
