"""Live subscription quota from the endpoint behind Claude Code's /usage.

Undocumented: any failure returns None and callers fall back to the SDK's rate-limit events. The OAuth token
is only read, never refreshed — refreshing rotates the refresh token and would log Claude Code out."""
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
TTL = 60
WINDOWS = ("five_hour", "seven_day")

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "value": None}


def _token(now: float) -> str | None:
    try:
        oauth = json.loads(CREDENTIALS.read_text(encoding="utf-8"))["claudeAiOauth"]
    except (OSError, ValueError, KeyError):
        return None
    return oauth.get("accessToken") if oauth.get("expiresAt", 0) / 1000 > now else None


def _get(token: str) -> dict:
    import urllib.request
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token}",
                                               "anthropic-beta": "oauth-2025-04-20", "User-Agent": "harness"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _window(name: str, raw: dict | None) -> dict | None:
    if not raw or raw.get("utilization") is None:
        return None
    return {"type": name, "status": "live", "utilization": raw["utilization"] / 100,  # endpoint sends percent
            "resets_at": datetime.fromisoformat(raw["resets_at"]).timestamp() if raw.get("resets_at") else None}


def parse(data: dict) -> dict | None:
    windows = {w: _window(w, data.get(w)) for w in WINDOWS}
    return windows if any(windows.values()) else None


def fetch(now=time.time, get=_get) -> dict | None:
    """{"five_hour": {...}, "seven_day": {...}} shaped like the SDK's rate_limit, cached for TTL; None on failure."""
    t = now()
    with _lock:
        if t - _cache["at"] < TTL:
            return _cache["value"]
        _cache["at"] = t  # failures are cached too: a dead endpoint must not be hit on every request
        token = _token(t)
        try:
            _cache["value"] = parse(get(token)) if token else None
        except Exception as e:
            print(f"quota endpoint: {type(e).__name__}: {e}", file=sys.stderr)
            _cache["value"] = None
        return _cache["value"]
