This git worktree is a pull request branch in the middle of merging origin/{base} into it. These files have merge conflicts:

{files}

Resolve every conflict so the result keeps BOTH the intent of the pull request and the changes that arrived on {base}.
Read the conflict markers (<<<<<<< ours = the pull request, >>>>>>> theirs = {base}) and the surrounding code.
Usually both sides must survive: merge the additions, adapt the pull request's code to anything {base} renamed or moved.

Rules:
- Edit only to settle the conflicts. No refactoring, no new features.
- No conflict markers may remain in any file.
- Run the project's tests yourself before finishing.
- Do not commit, abort the merge, push, or open pull requests. The harness does that.
- Finish with 1-2 sentences on how you resolved each file.
