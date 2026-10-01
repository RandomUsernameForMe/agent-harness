import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, RateLimitEvent, ResultMessage, TextBlock, query

from .graph import RateLimited

PROMPTS = Path(__file__).parent / "prompts"
READ_TOOLS = ["Read", "Glob", "Grep"]
WRITE_TOOLS = READ_TOOLS + ["Edit", "Write", "Bash"]
NO_PUBLISH = ["Bash(git push*)", "Bash(git commit*)", "Bash(gh *)"]
WORKER_APPEND = "You are a worker in an automated harness. Work only inside the current directory."


@dataclass
class WorkerResult:
    text: str
    usage: dict
    quota: float | None
    rate_limit: dict | None


def check_billing(billing: str, env: Mapping[str, str]) -> str | None:
    has_key = bool(env.get("ANTHROPIC_API_KEY"))
    if billing == "subscription" and has_key:
        return "billing=subscription but ANTHROPIC_API_KEY is set (would bill the API) — unset it"
    if billing == "api" and not has_key:
        return "billing=api but ANTHROPIC_API_KEY is not set"
    return None


def render_prompt(name: str, **fields) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").format(**fields)


def worker_options(*, cwd, model: str, tools: list[str], disallowed=(),
                   permission_mode: str = "acceptEdits", max_turns: int = 60) -> ClaudeAgentOptions:
    # strict_mcp_config drops the account's claude.ai connectors (Gmail, Drive, Notion…) from workers
    return ClaudeAgentOptions(
        cwd=str(cwd), model=model, setting_sources=["project"], strict_mcp_config=True,
        system_prompt={"type": "preset", "preset": "claude_code", "append": WORKER_APPEND},
        allowed_tools=list(tools), disallowed_tools=list(disallowed),
        permission_mode=permission_mode, max_turns=max_turns,
    )


def run_agent(prompt: str, *, cwd, model: str, tools: list[str], disallowed=(),
              permission_mode: str = "acceptEdits", max_turns: int = 60) -> WorkerResult:
    options = worker_options(cwd=cwd, model=model, tools=tools, disallowed=disallowed,
                             permission_mode=permission_mode, max_turns=max_turns)
    return asyncio.run(_collect(prompt, options, model))


def _rate_limit(info) -> dict:
    return {"type": info.rate_limit_type, "status": info.status,
            "utilization": info.utilization, "resets_at": info.resets_at}


async def _collect(prompt: str, options: ClaudeAgentOptions, model: str) -> WorkerResult:
    texts: list[str] = []
    result, rate_limit = None, None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, RateLimitEvent):
            info = msg.rate_limit_info
            if info.rate_limit_type == "five_hour" or rate_limit is None:
                rate_limit = _rate_limit(info)
            if info.status == "rejected":
                raise RateLimited(info.resets_at)
        elif isinstance(msg, AssistantMessage):
            texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
        elif isinstance(msg, ResultMessage):
            result = msg
    if result is None:
        raise RuntimeError("agent produced no result message")
    if result.api_error_status == 429:
        raise RateLimited(rate_limit and rate_limit["resets_at"])
    if result.is_error:
        raise RuntimeError(f"agent error: {result.subtype} {result.errors or ''}")
    u = result.usage or {}
    usage = {"model": model, "input_tokens": u.get("input_tokens", 0),
             "output_tokens": u.get("output_tokens", 0), "cost_usd": result.total_cost_usd}
    text = result.result or (texts[-1] if texts else "")
    return WorkerResult(text, usage, rate_limit and rate_limit["utilization"], rate_limit)
