You are the orchestrator of an automated coding harness. Choose and order at most {n} tasks for this run from the candidates below (one JSON object per line).

Weigh:
- the owner's focus (how often each project appears in their recent notes): {focus}
- project weights: {weights}
- explicit backlog priority (1 = highest; new proposals are 2)
- value for the owner against risk; prefer tasks a worker can finish and verify without asking the owner
- workers run in parallel, one task per project at a time — spread across projects when value is similar

Candidates:
{candidates}

Answer with ONLY a JSON array ordered from most to least important:
[{{"id": "...", "why": "one short Czech sentence"}}]
