from harness.guard import check_diff, new_failures


def test_empty_diff():
    assert check_diff([], 0, (), 800) == "empty diff: worker changed nothing"


def test_no_allowed_means_any_path():
    assert check_diff(["Thoughts/x.md"], 10, (), 800) is None


def test_allowed_nested_ok():
    assert check_diff(["bot/a/b.py", "web/app/page.tsx"], 10, ("bot/**", "web/**"), 800) is None


def test_forbidden_path():
    msg = check_diff(["bot/x.py", "Thoughts/2026-10-01.md"], 10, ("bot/**",), 800)
    assert msg == "forbidden paths: Thoughts/2026-10-01.md"


def test_prefix_lookalike_is_forbidden():
    assert check_diff(["botx/a.py"], 1, ("bot/**",), 800) == "forbidden paths: botx/a.py"


def test_too_large():
    assert check_diff(["a.py"], 801, (), 800) == "diff too large: 801 > 800 lines"


def test_new_failures():
    assert new_failures(["npm test"], ["npm test", "npx tsc --noEmit"]) == ["npx tsc --noEmit"]
    assert new_failures([], []) == []
