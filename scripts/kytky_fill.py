"""Idle job of the kytky project: Claude fills in plants that still lack species or care intervals.

Runs from the kytky checkout (projects.yaml → kytky.idle.cmd). Talks to the production app through its MCP
endpoint, the same way the owner's Claude does; MCP_SECRET comes from kytky's .env.local and never leaves this machine.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

URL = "https://kytky-xi.vercel.app/api/mcp/{secret}"
TOOLS = ("list_plants", "get_plant", "update_plant")
PROMPT = """Zavolej list_plants. Vezmi jen kytky s needsSetup=true (chybí druh nebo interval zálivky).
Když žádná taková není, napiš „Nic k doplnění.“ a skonči.
Pro každou z nich: get_plant, z fotky (a popisu, pokud je) urči druh a jedním voláním update_plant vyplň
species, description, waterEveryDays, fertilizeEveryDays, light, humidity, difficulty, tempMin, tempMax a petToxic
pro pokojové podmínky v aktuální sezóně. Pole, která už vyplněná jsou, nepřepisuj. name ani location neměň.
Když kytka nemá fotku nebo druh nepoznáš s jistotou, nic neměň a kytku přeskoč.
Na konci vypiš jeden řádek na kytku: id, jméno, co jsi nastavil, nebo proč jsi ji přeskočil."""


def env_value(path: Path, key: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def claude_bin() -> str:
    # serve runs from Task Scheduler, whose PATH may lack the user's ~/.local/bin
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")


def main() -> int:
    secret = env_value(Path(".env.local"), "MCP_SECRET")
    if not secret:
        print("MCP_SECRET chybí v .env.local (npx vercel env pull .env.local)", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"kytky": {"type": "http", "url": URL.format(secret=secret)}}}),
                          encoding="utf-8")
        out = subprocess.run([claude_bin(), "-p", PROMPT, "--mcp-config", str(config), "--strict-mcp-config",
                              "--allowedTools", ",".join(f"mcp__kytky__{t}" for t in TOOLS),
                              "--permission-mode", "dontAsk", "--model", "sonnet"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    print(out.stdout.replace(secret, "***"))
    print(out.stderr.replace(secret, "***"), file=sys.stderr)
    return out.returncode


if __name__ == "__main__":
    sys.exit(main())
