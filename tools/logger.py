"""Structured logging, in-memory request metrics, and log ring buffer.

Provides:
- setup_logging()          – configure root logger (JSON or text, + buffer handler)
- get_logger(name)         – get a named logger
- record_request(...)      – track per-endpoint latency/error counters
- record_auth_attempt(...) – track login/register success/failure
- get_metrics_snapshot()   – return current metrics dict
- get_recent_logs(...)     – return last N log entries from ring buffer
"""

from __future__ import annotations

import collections
import json
import logging
import re
import threading
import time
from typing import Any

# ── Sensitive patterns to redact ──────────────────────────────────
_REDACT_PATTERNS = [
    re.compile(r"(sk-[a-zA-Z0-9\-]{16,})", re.IGNORECASE),
    re.compile(r'("(?:api_key|password|token)"\s*:\s*")[^"]{8,}(")', re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]{20,}", re.IGNORECASE),
]


def _sanitize(msg: str) -> str:
    for pattern in _REDACT_PATTERNS:
        msg = pattern.sub(lambda m: m.group(0)[:8] + "***", msg)
    return msg


# ── In-memory log ring buffer ──────────────────────────────────────
_LOG_BUFFER: collections.deque[dict[str, Any]] = collections.deque(maxlen=500)
_LOG_LOCK = threading.Lock()


class _BufferHandler(logging.Handler):
    """Appends sanitized log records to the in-memory ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": _sanitize(record.getMessage()),
            }
            if record.exc_info:
                entry["exc"] = self.formatException(record.exc_info)
            with _LOG_LOCK:
                _LOG_BUFFER.append(entry)
        except Exception:
            pass


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line for structured log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "name": record.name,
            "msg": _sanitize(record.getMessage()),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


# ── Request metrics store ──────────────────────────────────────────
_metrics_lock = threading.Lock()
_metrics: dict[str, Any] = {
    "requests_total": 0,
    "requests_errors": 0,
    "requests_4xx": 0,
    "requests_5xx": 0,
    "auth_attempts": 0,
    "auth_failures": 0,
    "latencies_ms": collections.deque(maxlen=1000),
    "by_endpoint": {},
    "started_at": time.time(),
}


def record_request(path: str, status_code: int, duration_ms: float) -> None:
    """Track one completed HTTP request."""
    with _metrics_lock:
        _metrics["requests_total"] += 1
        _metrics["latencies_ms"].append(duration_ms)
        if status_code >= 400:
            _metrics["requests_errors"] += 1
        if 400 <= status_code < 500:
            _metrics["requests_4xx"] += 1
        if status_code >= 500:
            _metrics["requests_5xx"] += 1
        ep = _metrics["by_endpoint"].setdefault(
            path, {"count": 0, "errors": 0, "total_ms": 0.0}
        )
        ep["count"] += 1
        ep["total_ms"] += duration_ms
        if status_code >= 400:
            ep["errors"] += 1


def record_auth_attempt(success: bool) -> None:
    """Track one authentication attempt (login or register)."""
    with _metrics_lock:
        _metrics["auth_attempts"] += 1
        if not success:
            _metrics["auth_failures"] += 1


def get_metrics_snapshot() -> dict[str, Any]:
    """Return a point-in-time snapshot of all metrics."""
    with _metrics_lock:
        lats = list(_metrics["latencies_ms"])
        avg_ms = sum(lats) / len(lats) if lats else 0.0
        sorted_lats = sorted(lats)
        p95_ms = sorted_lats[int(len(sorted_lats) * 0.95)] if len(sorted_lats) >= 20 else None

        by_ep: dict[str, dict[str, Any]] = {}
        for path, ep in _metrics["by_endpoint"].items():
            avg = ep["total_ms"] / ep["count"] if ep["count"] else 0.0
            by_ep[path] = {
                "count": ep["count"],
                "errors": ep["errors"],
                "avg_ms": round(avg, 1),
            }

        return {
            "requests_total": _metrics["requests_total"],
            "requests_errors": _metrics["requests_errors"],
            "requests_4xx": _metrics["requests_4xx"],
            "requests_5xx": _metrics["requests_5xx"],
            "auth_attempts": _metrics["auth_attempts"],
            "auth_failures": _metrics["auth_failures"],
            "avg_latency_ms": round(avg_ms, 1),
            "p95_latency_ms": round(p95_ms, 1) if p95_ms is not None else None,
            "uptime_seconds": round(time.time() - _metrics["started_at"]),
            "by_endpoint": by_ep,
        }


def get_recent_logs(
    limit: int = 100, level: str | None = None
) -> list[dict[str, Any]]:
    """Return the most recent log entries (newest first)."""
    with _LOG_LOCK:
        logs = list(_LOG_BUFFER)
    if level:
        logs = [e for e in logs if e["level"] == level.upper()]
    return list(reversed(logs))[:limit]


# ── Setup ──────────────────────────────────────────────────────────

def setup_logging(json_output: bool = False) -> None:
    """Configure the root logger with console + buffer handlers.

    Call once at startup. Set JSON_LOGS=1 env var or json_output=True
    for machine-readable JSON lines (production).
    """
    import os

    use_json = json_output or os.getenv("JSON_LOGS", "").strip() in ("1", "true", "yes")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter: logging.Formatter
    if use_json:
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    root.addHandler(ch)

    bh = _BufferHandler()
    bh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(bh)

    # Silence noisy third-party libraries
    for noisy in ("httpx", "httpcore", "uvicorn.access", "hpack", "h2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
