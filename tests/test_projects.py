from pathlib import Path

import pytest

from harness.projects import load_config


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "projects.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_and_relative_path(tmp_path):
    cfg = load_config(write(tmp_path, """
defaults: {max_attempts: 5}
projects:
  demo:
    path: ../demo
    base: main
    verify: ["npm test"]
"""))
    p = cfg.projects["demo"]
    assert p.path == (tmp_path / ".." / "demo").resolve()
    assert p.max_attempts == 5
    assert p.max_diff_lines == 800
    assert p.setup == () and p.allowed == ()
    assert cfg.billing == "subscription"
    assert cfg.max_tasks_per_run == 3
    assert cfg.quota_threshold == 0.7


def test_project_overrides_defaults(tmp_path):
    cfg = load_config(write(tmp_path, """
defaults: {max_attempts: 5}
projects:
  demo: {path: ., base: dev, verify: [x], max_attempts: 1, allowed: ["bot/**"]}
"""))
    assert cfg.projects["demo"].max_attempts == 1
    assert cfg.projects["demo"].allowed == ("bot/**",)


def test_missing_verify_raises(tmp_path):
    with pytest.raises(ValueError, match="demo: missing verify"):
        load_config(write(tmp_path, "projects:\n  demo: {path: ., base: main}\n"))


def test_bad_billing_raises(tmp_path):
    with pytest.raises(ValueError, match="billing"):
        load_config(write(tmp_path, "billing: free\nprojects: {}\n"))


def test_phase2_fields_default(tmp_path):
    cfg = load_config(write(tmp_path, "projects:\n  demo: {path: ., base: main, verify: [x]}\n"))
    assert cfg.max_parallel == 2
    assert cfg.secondbrain is None
    assert cfg.projects["demo"].weight == 0


def test_phase2_fields_set(tmp_path):
    cfg = load_config(write(tmp_path, """
max_parallel: 3
secondbrain: {path: ../brain, days: 7}
projects:
  demo: {path: ., base: main, verify: [x], weight: 2}
"""))
    assert cfg.max_parallel == 3
    assert cfg.secondbrain == ((tmp_path / ".." / "brain").resolve(), 7)
    assert cfg.projects["demo"].weight == 2


def test_max_parallel_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="max_parallel"):
        load_config(write(tmp_path, "max_parallel: 0\nprojects: {}\n"))
