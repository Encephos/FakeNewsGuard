"""Redis-based job state store — replaces the in-memory jobs dict.

Each job is stored as:
  - Hash  ``fng:job:{job_id}``   — scalar fields (status, error, result JSON, …)
  - List  ``fng:job:{job_id}:steps`` — ordered step dicts (RPUSH to append)

Both keys share a TTL that is refreshed on every write.
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis

from config.infrastructure import CeleryConfig, JobConfig

_PREFIX = "fng:job:"

# Fields stored as JSON strings inside the hash (everything else is a plain string/number).
_JSON_FIELDS = frozenset({"result", "extracted_content"})


class JobStore:
    """Thin wrapper around Redis for per-job state management."""

    def __init__(self, client: redis.Redis, ttl: int | None = None) -> None:
        self._r = client
        self._ttl = ttl or JobConfig().ttl_seconds

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _hash_key(job_id: str) -> str:
        return f"{_PREFIX}{job_id}"

    @staticmethod
    def _steps_key(job_id: str) -> str:
        return f"{_PREFIX}{job_id}:steps"

    def _refresh_ttl(self, job_id: str) -> None:
        pipe = self._r.pipeline(transaction=False)
        pipe.expire(self._hash_key(job_id), self._ttl)
        pipe.expire(self._steps_key(job_id), self._ttl)
        pipe.execute()

    # ── public API ────────────────────────────────────────────────

    def create(self, job_id: str, **fields: Any) -> None:
        """Create a new job with initial fields."""
        mapping: dict[str, str] = {}
        for k, v in fields.items():
            if k in _JSON_FIELDS and v is not None:
                mapping[k] = json.dumps(v, ensure_ascii=False)
            elif v is None:
                mapping[k] = ""
            else:
                mapping[k] = str(v)
        hk = self._hash_key(job_id)
        self._r.hset(hk, mapping=mapping)
        # Also initialise the steps list key so TTL applies to both
        sk = self._steps_key(job_id)
        self._r.expire(hk, self._ttl)
        self._r.expire(sk, self._ttl)

    def get(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve all scalar fields for a job.  Returns *None* if the job does not exist."""
        raw: dict[bytes, bytes] = self._r.hgetall(self._hash_key(job_id))
        if not raw:
            return None
        out: dict[str, Any] = {}
        for bk, bv in raw.items():
            k = bk.decode() if isinstance(bk, bytes) else bk
            v = bv.decode() if isinstance(bv, bytes) else bv
            if k in _JSON_FIELDS and v:
                try:
                    out[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    out[k] = v
            elif v == "":
                out[k] = None
            else:
                out[k] = v
        # Cast numeric fields back from strings
        for nf in ("created_at", "last_activity"):
            if nf in out and out[nf] is not None:
                try:
                    out[nf] = float(out[nf])
                except (ValueError, TypeError):
                    pass
        for nf in ("inactivity_timeout",):
            if nf in out and out[nf] is not None:
                try:
                    out[nf] = int(out[nf])
                except (ValueError, TypeError):
                    pass
        # Boolean fields
        if "from_cache" in out:
            out["from_cache"] = out["from_cache"] in ("True", "true", "1")
        return out

    def update(self, job_id: str, **fields: Any) -> None:
        """Update one or more fields on an existing job."""
        mapping: dict[str, str] = {}
        for k, v in fields.items():
            if k in _JSON_FIELDS and v is not None:
                mapping[k] = json.dumps(v, ensure_ascii=False)
            elif v is None:
                mapping[k] = ""
            else:
                mapping[k] = str(v)
        if mapping:
            self._r.hset(self._hash_key(job_id), mapping=mapping)
        self._refresh_ttl(job_id)

    def push_step(self, job_id: str, step: dict[str, Any]) -> None:
        """Append a step to the job's step list and update last_activity."""
        pipe = self._r.pipeline(transaction=False)
        pipe.rpush(self._steps_key(job_id), json.dumps(step, ensure_ascii=False))
        pipe.hset(self._hash_key(job_id), "last_activity", str(time.time()))
        pipe.execute()
        self._refresh_ttl(job_id)

    def get_steps(self, job_id: str) -> list[dict[str, Any]]:
        """Return all steps for a job as a list of dicts."""
        raw_list = self._r.lrange(self._steps_key(job_id), 0, -1)
        steps: list[dict[str, Any]] = []
        for item in raw_list:
            s = item.decode() if isinstance(item, bytes) else item
            try:
                steps.append(json.loads(s))
            except (json.JSONDecodeError, TypeError):
                pass
        return steps

    def set_result(self, job_id: str, result: dict[str, Any]) -> None:
        """Mark the job as done and store the final result."""
        self.update(job_id, status="done", result=result)

    def set_error(self, job_id: str, error: str) -> None:
        """Mark the job as failed with an error message."""
        self.update(job_id, status="error", error=error)

    def exists(self, job_id: str) -> bool:
        return self._r.exists(self._hash_key(job_id)) > 0


# ── Singleton ──────────────────────────────────────────────────────

_store: JobStore | None = None
_store_client: redis.Redis | None = None


def get_job_store(client: redis.Redis | None = None) -> JobStore:
    """Return the singleton JobStore.  Pass *client* to override (useful in tests)."""
    global _store, _store_client
    if client is not None:
        _store_client = client
        _store = JobStore(client)
        return _store
    if _store is None:
        cfg = CeleryConfig()
        _store_client = redis.Redis.from_url(cfg.broker_url, decode_responses=False)
        _store = JobStore(_store_client)
    return _store


def reset_job_store() -> None:
    """Reset the singleton (for tests)."""
    global _store, _store_client
    _store = None
    _store_client = None
