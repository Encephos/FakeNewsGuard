"""Tests für tools/cache.py."""

from __future__ import annotations

import time

import pytest

from config import CacheConfig
from tools.cache import ClaimCache, _claim_key


# ── _claim_key ────────────────────────────────────────────────────


def test_claim_key_is_deterministic():
    k1 = _claim_key("Die Erde ist rund.", "FactChecker")
    k2 = _claim_key("Die Erde ist rund.", "FactChecker")
    assert k1 == k2


def test_claim_key_differs_by_agent():
    k1 = _claim_key("Claim text", "FactChecker")
    k2 = _claim_key("Claim text", "NumberAuditor")
    assert k1 != k2


def test_claim_key_normalizes_whitespace():
    k1 = _claim_key("  Die Erde ist rund.  ", "FactChecker")
    k2 = _claim_key("die erde ist rund.", "FactChecker")
    assert k1 == k2


# ── ClaimCache ────────────────────────────────────────────────────


def test_cache_set_and_get(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("test claim", "FactChecker", {"rating": "TRUE"})
    result = cache.get("test claim", "FactChecker")
    assert result == {"rating": "TRUE"}


def test_cache_miss_returns_none(cache_config):
    cache = ClaimCache(cache_config)
    result = cache.get("nonexistent claim", "FactChecker")
    assert result is None


def test_cache_disabled_always_returns_none(tmp_path):
    config = CacheConfig(enabled=False, db_path=str(tmp_path / "disabled.db"))
    cache = ClaimCache(config)
    cache.set("test", "Agent", {"x": 1})
    assert cache.get("test", "Agent") is None


def test_cache_ttl_expiry(tmp_path):
    """Cache-Einträge mit abgelaufener TTL sollen None zurückgeben."""
    config = CacheConfig(enabled=True, db_path=str(tmp_path / "ttl.db"), ttl_hours=0)
    cache = ClaimCache(config)
    cache.set("claim", "Agent", {"data": "value"})
    # TTL = 0 hours = sofort abgelaufen
    result = cache.get("claim", "Agent")
    assert result is None


def test_cache_overwrite(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("claim", "FactChecker", {"rating": "TRUE"})
    cache.set("claim", "FactChecker", {"rating": "FALSE"})
    assert cache.get("claim", "FactChecker") == {"rating": "FALSE"}


def test_cache_clear_expired(tmp_path):
    config = CacheConfig(enabled=True, db_path=str(tmp_path / "expired.db"), ttl_hours=0)
    cache = ClaimCache(config)
    cache.set("a", "Agent", {})
    cache.set("b", "Agent", {})
    removed = cache.clear_expired()
    assert removed == 2


def test_cache_stats(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("c1", "Agent", {})
    cache.set("c2", "Agent", {})
    stats = cache.stats()
    assert stats["enabled"] is True
    assert stats["valid_entries"] == 2
    assert stats["expired_entries"] == 0


def test_cache_delete(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("claim", "Agent", {"x": 1})
    cache.delete("claim", "Agent")
    assert cache.get("claim", "Agent") is None


# ── Semantic Cache ──────────────────────────────────────────────────


def test_semantic_cache_disabled_by_default(cache_config):
    """Semantic cache ist standardmäßig deaktiviert."""
    cache = ClaimCache(cache_config)
    assert cache._semantic_enabled is False


def test_semantic_lookup_returns_none_without_model(tmp_path):
    """Ohne sentence-transformers gibt _semantic_lookup None zurück."""
    import tools.cache as cache_module
    # Sicherstellen, dass das Modell als "nicht verfügbar" markiert ist
    old_model = cache_module._embedding_model
    cache_module._embedding_model = False
    try:
        config = CacheConfig(
            enabled=True,
            db_path=str(tmp_path / "sem.db"),
            semantic_cache=True,
        )
        cache = ClaimCache(config)
        cache.set("original claim", "Agent", {"rating": "TRUE"})
        # Exakter Key-Miss, semantic lookup fällt auf None zurück (kein Modell)
        result = cache.get("paraphrased claim", "Agent")
        assert result is None
    finally:
        cache_module._embedding_model = old_model


def test_semantic_lookup_with_mocked_embeddings(tmp_path):
    """Semantische Suche findet ähnliche Claims über Embedding-Similarity."""
    import struct
    import tools.cache as cache_module

    # Mock: Einfaches Embedding als normalisierte 3D-Vektoren
    class FakeModel:
        def encode(self, text, normalize_embeddings=True):
            # Ähnliche Texte → identische Vektoren (sim=1.0)
            if "inflation" in text.lower():
                return [0.577, 0.577, 0.577]
            return [0.0, 0.0, 1.0]  # Orthogonal → sim ≈ 0.577

    old_model = cache_module._embedding_model
    cache_module._embedding_model = FakeModel()
    try:
        config = CacheConfig(
            enabled=True,
            db_path=str(tmp_path / "sem2.db"),
            semantic_cache=True,
        )
        cache = ClaimCache(config)
        cache.set("Die Inflation stieg", "Agent", {"rating": "MISLEADING"})

        # Paraphrase mit "inflation" → ähnlicher Vektor → Cache-Hit
        result = cache.get("Inflation ist gestiegen", "Agent")
        # Cosine sim von [0.9,0.3,0.1] mit [0.9,0.3,0.1] = 1.0 (identisch)
        assert result == {"rating": "MISLEADING"}

        # Komplett anderer Text → kein Hit (Similarity zu niedrig)
        result2 = cache.get("Das Wetter ist schön", "Agent")
        assert result2 is None
    finally:
        cache_module._embedding_model = old_model
