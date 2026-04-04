"""Erweiterte Unit-Tests für tools/cache.py.

Ergänzt tests/tools/test_cache.py um fehlende Coverage:
- context-Parameter im Key
- use_canonical / canonical_text
- clear_all(), stats() bei disabled, expired-Counting
- Agent-Isolation, Schema-Erstellung, in-memory DB
- Thread-Safety, _cosine_similarity, _encode_text
"""

from __future__ import annotations

import struct
import threading

import pytest


# ── _claim_key – Kontext und canonical_text ───────────────────────────────────


class TestClaimKeyExtended:
    def test_key_with_context_differs_from_no_context(self):
        from tools.cache import _claim_key
        k_no_ctx = _claim_key("Claim text", "FactChecker")
        k_with_ctx = _claim_key("Claim text", "FactChecker", context="extra context")
        assert k_no_ctx != k_with_ctx

    def test_key_context_truncated_at_100_chars(self):
        from tools.cache import _claim_key
        ctx_100 = "x" * 100
        ctx_200 = "x" * 200
        k1 = _claim_key("text", "Agent", context=ctx_100)
        k2 = _claim_key("text", "Agent", context=ctx_200)
        # Beide sollten gleich sein weil context[:100] identisch ist
        assert k1 == k2

    def test_key_use_canonical_true_uses_canonical_text(self):
        from tools.cache import _claim_key
        raw = "Die Kriminalität stieg an"
        canonical = "Kriminalitaet gestiegen"
        k_raw = _claim_key(raw, "Agent", use_canonical=False)
        k_can = _claim_key(raw, "Agent", canonical_text=canonical, use_canonical=True)
        assert k_raw != k_can

    def test_key_use_canonical_false_uses_raw_text(self):
        from tools.cache import _claim_key
        raw = "Roher Text"
        canonical = "Kanonischer Text"
        k1 = _claim_key(raw, "Agent", canonical_text=canonical, use_canonical=False)
        k2 = _claim_key(raw, "Agent", use_canonical=False)
        assert k1 == k2

    def test_key_canonical_none_falls_back_to_raw(self):
        from tools.cache import _claim_key
        raw = "Roher Text"
        k_none = _claim_key(raw, "Agent", canonical_text=None, use_canonical=True)
        k_raw = _claim_key(raw, "Agent", use_canonical=False)
        # canonical_text=None → fällt auf raw zurück
        assert k_none == k_raw


# ── ClaimCache – canonical_text Roundtrip ────────────────────────────────────


class TestClaimCacheCanonical:
    def test_set_and_get_with_canonical_text(self, tmp_path):
        from config import CacheConfig
        from tools.cache import ClaimCache
        cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "can.db"), ttl_hours=1)
        cache = ClaimCache(cfg)
        cache.set(
            "Die Inflation stieg auf 8%",
            "FactChecker",
            {"rating": "TRUE"},
            canonical_text="Inflation stieg",
            use_canonical=True,
        )
        result = cache.get(
            "Die Inflation stieg auf 8%",
            "FactChecker",
            canonical_text="Inflation stieg",
            use_canonical=True,
        )
        assert result == {"rating": "TRUE"}

    def test_canonical_key_matches_regardless_of_raw_variation(self, tmp_path):
        from config import CacheConfig
        from tools.cache import ClaimCache
        cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "can2.db"), ttl_hours=1)
        cache = ClaimCache(cfg)
        canonical = "Inflation gestiegen"
        # set mit raw_text_A, get mit raw_text_B → gleicher Key wenn canonical gleich
        cache.set("raw_text_A", "Agent", {"x": 1}, canonical_text=canonical, use_canonical=True)
        result = cache.get("raw_text_B", "Agent", canonical_text=canonical, use_canonical=True)
        assert result == {"x": 1}


# ── ClaimCache – Verhalten ────────────────────────────────────────────────────


class TestClaimCacheBehavior:
    def test_clear_all_empties_cache(self, tmp_path):
        from config import CacheConfig
        from tools.cache import ClaimCache
        cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "clear.db"), ttl_hours=1)
        cache = ClaimCache(cfg)
        cache.set("a", "Agent", {})
        cache.set("b", "Agent", {})
        cache.clear_all()
        assert cache.stats()["total_entries"] == 0

    def test_stats_disabled_returns_disabled_dict(self, tmp_path):
        from config import CacheConfig
        from tools.cache import ClaimCache
        cfg = CacheConfig(enabled=False, db_path=str(tmp_path / "dis.db"))
        cache = ClaimCache(cfg)
        s = cache.stats()
        assert s == {"enabled": False}

    def test_stats_counts_expired_entries_separately(self, tmp_path):
        from config import CacheConfig
        from tools.cache import ClaimCache
        cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "exp.db"), ttl_hours=0)
        cache = ClaimCache(cfg)
        cache.set("a", "Agent", {})
        cache.set("b", "Agent", {})
        s = cache.stats()
        assert s["total_entries"] == 2
        assert s["expired_entries"] == 2
        assert s["valid_entries"] == 0

    def test_different_agents_get_isolated_entries(self, tmp_path):
        from config import CacheConfig
        from tools.cache import ClaimCache
        cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "iso.db"), ttl_hours=1)
        cache = ClaimCache(cfg)
        cache.set("claim", "AgentA", {"from": "A"})
        cache.set("claim", "AgentB", {"from": "B"})
        assert cache.get("claim", "AgentA") == {"from": "A"}
        assert cache.get("claim", "AgentB") == {"from": "B"}


# ── ClaimCache – Datenbankschema ──────────────────────────────────────────────


class TestClaimCacheSchema:
    def test_both_tables_created_on_init(self, tmp_path):
        from config import CacheConfig
        from tools.cache import ClaimCache
        import sqlite3
        cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "schema.db"), ttl_hours=1)
        ClaimCache(cfg)
        conn = sqlite3.connect(str(tmp_path / "schema.db"))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "claim_cache" in tables
        assert "claim_embeddings" in tables

    def test_in_memory_db_works(self):
        from config import CacheConfig
        from tools.cache import ClaimCache
        cfg = CacheConfig(enabled=True, db_path=":memory:", ttl_hours=1)
        cache = ClaimCache(cfg)
        cache.set("test", "Agent", {"ok": True})
        assert cache.get("test", "Agent") == {"ok": True}


# ── _cosine_similarity und _encode_text ──────────────────────────────────────


class TestCosineAndEncoding:
    def test_cosine_similarity_identical_vectors(self):
        from tools.cache import _cosine_similarity
        vec = [1.0, 0.0, 0.0]
        b = struct.pack(f"{len(vec)}f", *vec)
        assert abs(_cosine_similarity(b, b) - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal_vectors(self):
        from tools.cache import _cosine_similarity
        a = struct.pack("3f", 1.0, 0.0, 0.0)
        b = struct.pack("3f", 0.0, 1.0, 0.0)
        assert abs(_cosine_similarity(a, b) - 0.0) < 1e-6

    def test_encode_text_returns_none_when_model_false(self):
        import tools.cache as cache_module
        old = cache_module._embedding_model
        cache_module._embedding_model = False
        try:
            result = cache_module._encode_text("test text")
            assert result is None
        finally:
            cache_module._embedding_model = old


# ── Thread-Safety ─────────────────────────────────────────────────────────────


class TestClaimCacheThreadSafety:
    def test_concurrent_writes_do_not_raise(self, tmp_path):
        from config import CacheConfig
        from tools.cache import ClaimCache
        cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "thread.db"), ttl_hours=1)
        cache = ClaimCache(cfg)
        errors: list[Exception] = []

        def write(n: int) -> None:
            try:
                for i in range(10):
                    cache.set(f"claim_{n}_{i}", "Agent", {"n": n, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(t,)) for t in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Concurrent writes raised: {errors}"
        # Alle 50 Einträge sollten vorhanden sein
        assert cache.stats()["total_entries"] == 50
