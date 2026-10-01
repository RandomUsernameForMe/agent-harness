import shutil
from pathlib import Path

from harness import events
from harness.cli import main

ROOT = Path(__file__).parent.parent


def test_simulate_by_name(tmp_path, capsys):
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    assert main(["--home", str(tmp_path), "simulate", "happy-path", "--delay", "0"]) == 0
    out = capsys.readouterr().out
    assert "done" in out and "[x]" in out
    conn = events.connect(tmp_path / "harness.db")
    [run] = events.runs(conn)
    assert run["simulated"] and run["last_kind"] == "run_end"


def test_simulate_survives_cp1252_console(tmp_path):
    import os
    import subprocess
    import sys
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    p = subprocess.run([sys.executable, "-m", "harness.cli", "--home", str(tmp_path), "simulate", "happy-path", "--delay", "0"],
                       capture_output=True, env=env)
    assert p.returncode == 0, p.stderr.decode(errors="replace")


def test_simulate_unknown(tmp_path):
    (tmp_path / "scenarios").mkdir()
    assert main(["--home", str(tmp_path), "simulate", "nope"]) == 2
