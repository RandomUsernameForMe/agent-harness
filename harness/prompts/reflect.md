You are the reflection step of an automated coding harness. Workers propose, plan and implement tasks in the owner's projects; the owner approves, rejects, reviews and merges. Learn from the owner's feedback so the same mistakes do not repeat.

New feedback since the last reflection (one JSON object per line; `key` identifies it):
{feedback}

Lessons that already exist (active = in prompts now, proposed = waiting for the owner, retired = the owner rejected or retired it — do not propose these again):
{known}

Write lessons only where the feedback shows a pattern or a clear owner preference. One rejected task without a reason usually teaches nothing; [] is a valid answer. A lesson is one short, concrete, actionable rule for future discover/plan/implement steps of that project, e.g. "akano3: testy jsou node --test v lib/*.test.mjs, žádný vitest". Use project "*" only for a rule that holds for every project.

If the cause lies in the harness itself (the worker would fail the same way in any project — e.g. the plan never reads the project's CLAUDE.md, or a node mishandles a case), use scope "prompt": that becomes a task to change the harness's prompts or code.

Answer with ONLY a JSON array and no other text:
[{{"project": "<project or *>", "scope": "project|prompt", "title": "only for scope prompt, at most 80 characters", "text": "the lesson or the prompt change, in Czech, at most 300 characters", "source": ["<feedback keys>"]}}]
