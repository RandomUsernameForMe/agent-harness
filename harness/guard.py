from fnmatch import fnmatchcase


def check_diff(changed: list[str], diff_lines: int, allowed: tuple[str, ...], max_lines: int) -> str | None:
    if not changed:
        return "empty diff: worker changed nothing"
    if allowed:
        bad = [p for p in changed if not any(fnmatchcase(p, pat) for pat in allowed)]
        if bad:
            return f"forbidden paths: {', '.join(bad[:10])}"
    if diff_lines > max_lines:
        return f"diff too large: {diff_lines} > {max_lines} lines"
    return None


# ponytail: baseline per command, not per test; a regression inside an already-failing command goes unnoticed — parse test reporters if that bites
def new_failures(baseline: list[str], failed: list[str]) -> list[str]:
    return [c for c in failed if c not in baseline]
