"""Which run/node/worker an agent call belongs to, and where its record goes.

The graph sets the context from LangGraph's `config` around every node; `run_agent` reads it and hands the call
to the sink. Steps never enter the graph state, so checkpoints stay small."""
import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

TOOL_CHARS = 300
TEXT_CHARS = 1000


@dataclass(frozen=True)
class Trace:
    run_id: str | None
    node: str | None
    worker: str | None


_current: contextvars.ContextVar[Trace] = contextvars.ContextVar("trace", default=Trace(None, None, None))
_sink: Callable[[dict, list[dict]], None] | None = None


def set_sink(fn: Callable[[dict, list[dict]], None] | None) -> None:
    global _sink
    _sink = fn


def current() -> Trace:
    return _current.get()


@contextmanager
def context(t: Trace):
    token = _current.set(t)
    try:
        yield
    finally:
        _current.reset(token)


def from_config(state: dict, config: dict | None) -> Trace:
    """LangGraph passes the thread id in `configurable` and the running node in `metadata`."""
    config = config or {}
    worker = (state.get("task") or {}).get("id") or state.get("project")
    return Trace((config.get("configurable") or {}).get("thread_id"),
                 (config.get("metadata") or {}).get("langgraph_node"), worker)


def emit(call: dict, steps: list[dict]) -> None:
    if _sink is not None:
        _sink(call, steps)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _result_text(content) -> str:
    if isinstance(content, list):
        return " ".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
    return str(content or "")


def text_step(text: str) -> dict:
    return {"kind": "text", "tool": None, "summary": _clip(text, TEXT_CHARS), "is_error": False}


def tool_use_step(name: str, tool_input) -> dict:
    return {"kind": "tool_use", "tool": name,
            "summary": _clip(json.dumps(tool_input, ensure_ascii=False, default=str), TOOL_CHARS), "is_error": False}


def tool_result_step(name: str | None, content, is_error) -> dict:
    return {"kind": "tool_result", "tool": name, "summary": _clip(_result_text(content), TOOL_CHARS),
            "is_error": bool(is_error)}
