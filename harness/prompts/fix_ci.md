This git worktree is a pull request branch whose CI checks fail on GitHub. Failed job log (tail):

```
{log}
```

Find the root cause and fix it so the checks pass again. Keep the intent of the pull request.

Rules:
- Fix only what makes CI fail. No refactoring, no new features.
- If the failure is unrelated to this branch (flaky test, broken base, infrastructure), change nothing and say so.
- Run the project's tests yourself before finishing.
- Do not commit, push, or open pull requests. The harness does that.
- Finish with 1-2 sentences on what failed and what you changed.
