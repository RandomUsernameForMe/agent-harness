from fnmatch import fnmatchcase


def check_diff(changed: list[str], diff_lines: int, allowed: tuple[str, ...], max_lines: int) -> str | None:
    if not changed:
        return "empty diff: worker changed nothing"
    if allowed:
        bad = [p for p in changed if not _allowed(p, allowed)]
        if bad:
            return f"forbidden paths: {', '.join(bad[:10])}"
    if diff_lines > max_lines:
        return f"diff too large: {diff_lines} > {max_lines} lines"
    return None


def _allowed(path: str, patterns: tuple[str, ...]) -> bool:
    """`!pat` excludes (wins over any include); with only exclusions, everything else is allowed."""
    include = [p for p in patterns if not p.startswith("!")]
    if any(fnmatchcase(path, p[1:]) for p in patterns if p.startswith("!")):
        return False
    return not include or any(fnmatchcase(path, p) for p in include)


# ponytail: baseline per command, not per test; a regression inside an already-failing command goes unnoticed — parse test reporters if that bites
def new_failures(baseline: list[str], failed: list[str]) -> list[str]:
    return [c for c in failed if c not in baseline]
