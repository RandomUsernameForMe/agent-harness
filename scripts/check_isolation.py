"""Ověří, že worker se setting_sources=["project"] nevidí globální pluginy/skills z ~/.claude.
Spouští jeden krátký dotaz (haiku) přes subscription."""
import asyncio
import json
import os
import sys
import tempfile

from claude_agent_sdk import SystemMessage, query

from harness.sdk import worker_options

FORBIDDEN = ("caveman", "ponytail", "superpowers", "impeccable", "claude.ai gmail", "claude.ai google drive")


async def main() -> int:
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is set — unset it to test subscription", file=sys.stderr)
        return 2
    opts = worker_options(cwd=tempfile.mkdtemp(), model="haiku", tools=["Read"], permission_mode="dontAsk", max_turns=1)
    init = None
    async for msg in query(prompt="Reply with the single word: ok", options=opts):
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            init = msg.data
    dump = json.dumps(init, ensure_ascii=False).lower()
    print(json.dumps(init, indent=2, ensure_ascii=False)[:3000])
    leaked = [f for f in FORBIDDEN if f in dump]
    print("LEAKED:" if leaked else "ISOLATED", leaked)
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.exit(asyncio.run(main()))
