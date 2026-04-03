"""Analytics aggregation engine for FakeNewsGuard archive data.

Provides time-bucketed aggregations (timeline, topics, sources, accuracy,
platforms) over the analysis_archive table.  All heavy work is Python-side so
the same code runs against SQLite (dev) and Postgres (production) without
dialect differences.

Results are cached in-memory with a 5-minute TTL to avoid repeated full-table
scans on busy instances.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

# ── Rating → numeric score ───────────────────────────────────────────
RATING_SCORE: dict[str, int] = {
    "RELIABLE": 5,
    "MOSTLY_RELIABLE": 4,
    "MIXED": 3,
    "MISLEADING": 2,
    "HIGHLY_MISLEADING": 1,
    "FABRICATED": 0,
}

_ALL_RATINGS = list(RATING_SCORE.keys())

# ── Reverse mapping: localized display strings → canonical enum key ──
# Covers German (de) and English (en) translations so that archived rows
# stored with localized labels are correctly normalised.
_LOCALIZED_TO_ENUM: dict[str, str] = {
    # German
    "Wahr": "RELIABLE",
    "Größtenteils wahr": "MOSTLY_RELIABLE",
    "Irreführend": "MIXED",          # DE maps both MIXED and MISLEADING → same label
    "Größtenteils falsch": "HIGHLY_MISLEADING",
    "Falsch": "FABRICATED",
    # English
    "True": "RELIABLE",
    "Mostly true": "MOSTLY_RELIABLE",
    "Mixed": "MIXED",
    "Misleading": "MISLEADING",
    "Mostly false": "HIGHLY_MISLEADING",
    "False": "FABRICATED",
}


def _confidence_to_p_reliable(rating_str: str, confidence_pct: int) -> float:
    """Wandelt (Rating, Konfidenz) in P(reliable) für Brier-Score um.

    Confidence bedeutet "wie sicher bin ich in mein Rating", nicht P(reliable).
    Bei negativen Ratings muss die Konfidenz invertiert werden.
    """
    score = RATING_SCORE.get(_normalize_rating(rating_str), 3)
    conf = (confidence_pct or 0) / 100
    if score >= 4:    # RELIABLE, MOSTLY_RELIABLE
        return conf
    elif score <= 2:  # MISLEADING, HIGHLY_MISLEADING, FABRICATED
        return 1.0 - conf
    else:             # MIXED
        return 0.5


def _normalize_rating(raw: str | None, fallback: str = "MIXED") -> str:
    """Map a stored overall_rating value to its canonical enum key.

    Handles three cases:
      1. Already an enum key (e.g. "RELIABLE") → returned as-is
      2. Localized label (e.g. "Wahr", "Mostly true") → mapped to enum key
      3. Unknown / None → *fallback*
    """
    if not raw:
        return fallback
    if raw in RATING_SCORE:
        return raw
    return _LOCALIZED_TO_ENUM.get(raw, fallback)


# ── Stopwords (German + English, common short words) ─────────────────
_STOPWORDS: frozenset[str] = frozenset({
    # German
    "dass", "dies", "eine", "einem", "einen", "einer", "eines", "haben",
    "hatte", "haben", "nach", "nicht", "oder", "sein", "sind", "sich",
    "über", "auch", "aber", "noch", "mehr", "beim", "dem", "den", "der",
    "des", "die", "das", "und", "von", "aus", "mit", "wurde", "wurden",
    "wird", "wird", "kann", "kann", "laut", "beim", "sowie", "doch",
    "kein", "keine", "keinen", "ihrer", "ihrem", "durch", "gegen",
    "unter", "beim", "worden", "hatte", "hatten", "dieser", "dieses",
    "diesen", "diesem", "solche", "immer", "schon", "dabei", "damit",
    "dafür", "daran", "darauf", "bereits", "jedoch", "deshalb",
    # English
    "that", "this", "have", "been", "from", "they", "with", "will",
    "would", "could", "should", "about", "which", "their", "there",
    "were", "when", "what", "also", "than", "then", "into", "more",
    "some", "such", "only", "other", "these", "those", "said", "each",
    "most", "over", "even", "both", "after", "being", "very", "just",
})

# ── Bucket format strings ─────────────────────────────────────────────
_BUCKET_FMT: dict[str, str] = {
    "day": "%Y-%m-%d",
    "week": "%G-W%V",   # ISO week
    "month": "%Y-%m",
}

# Default bucket per period
_PERIOD_DEFAULTS: dict[str, tuple[int | None, str]] = {
    "7d":  (7,   "day"),
    "30d": (30,  "day"),
    "90d": (90,  "week"),
    "all": (None, "month"),
}


def _parse_period(period: str, bucket: str | None) -> tuple[float | None, str]:
    """Return (cutoff_unix_ts | None, bucket_key).

    cutoff_unix_ts is None for 'all', otherwise now - N days.
    bucket_key is 'day' | 'week' | 'month'.
    """
    days, default_bucket = _PERIOD_DEFAULTS.get(period, (30, "day"))
    chosen_bucket = bucket if bucket in _BUCKET_FMT else default_bucket
    if days is None:
        return None, chosen_bucket
    cutoff = time.time() - days * 86400
    return cutoff, chosen_bucket


def _bucket_label(ts: float, fmt: str) -> str:
    """Convert a Unix timestamp to a bucket label string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(fmt)


def _extract_domain(url: str) -> str:
    """Return the bare domain (netloc without www.) from a URL string.

    Falls back to the original string if no netloc can be parsed.
    """
    try:
        netloc = urlparse(url).netloc
        if netloc:
            return netloc.removeprefix("www.")
        return url
    except Exception:
        return url


def _tokenize(text: str) -> list[str]:
    """Extract meaningful word tokens from free text."""
    words = re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


class AnalyticsEngine:
    """Aggregates archive data for trend analysis.

    Args:
        archive: An AnalysisArchive (SQLite) or PgAnalysisArchive (Postgres)
                 instance.  Both expose a ``_connect()`` context manager that
                 yields a DB connection with dict-row support.
    """

    _TTL = 300  # 5-minute result cache

    def __init__(self, archive: Any) -> None:
        self._archive = archive
        self._cache: dict[str, tuple[Any, float]] = {}

    # ── Internal helpers ─────────────────────────────────────────────

    def _cached(self, key: str, fn) -> Any:
        if key in self._cache:
            result, ts = self._cache[key]
            if time.time() - ts < self._TTL:
                return result
        result = fn()
        self._cache[key] = (result, time.time())
        return result

    def _fetch_rows(self, cutoff: float | None) -> list[dict]:
        """Fetch all archive rows within the time window.

        Returns lightweight dicts (no full result_json parsing yet).
        """
        if not self._archive.config.enabled:
            return []
        ph = getattr(self._archive, "_placeholder", "?")
        with self._archive._connect() as conn:
            if cutoff is not None:
                rows = conn.execute(
                    f"""
                    SELECT id, created_at, overall_rating, confidence,
                           claims_count, techniques_count, platform, result_json
                    FROM analysis_archive
                    WHERE created_at >= {ph}
                    ORDER BY created_at ASC
                    """,
                    (cutoff,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, created_at, overall_rating, confidence,
                           claims_count, techniques_count, platform, result_json
                    FROM analysis_archive
                    ORDER BY created_at ASC
                    """
                ).fetchall()
        # Normalise to plain dicts
        return [dict(r) for r in rows]

    # ── Public aggregation methods ────────────────────────────────────

    def timeline(self, period: str = "30d", bucket: str | None = None) -> dict:
        """Time-bucketed analysis counts + confidence + rating distribution."""
        key = f"timeline:{period}:{bucket}"
        return self._cached(key, lambda: self._compute_timeline(period, bucket))

    def _compute_timeline(self, period: str, bucket: str | None) -> dict:
        cutoff, bkt = _parse_period(period, bucket)
        rows = self._fetch_rows(cutoff)

        buckets: dict[str, dict] = {}
        for row in rows:
            label = _bucket_label(row["created_at"], _BUCKET_FMT[bkt])
            if label not in buckets:
                buckets[label] = {
                    "date": label,
                    "count": 0,
                    "confidence_sum": 0,
                    "claims_sum": 0,
                    "rating_distribution": {r: 0 for r in _ALL_RATINGS},
                }
            b = buckets[label]
            b["count"] += 1
            b["confidence_sum"] += row["confidence"] or 0
            b["claims_sum"] += row["claims_count"] or 0
            rating = _normalize_rating(row["overall_rating"])
            if rating in b["rating_distribution"]:
                b["rating_distribution"][rating] += 1

        result_buckets = []
        for b in buckets.values():
            n = b["count"]
            result_buckets.append({
                "date": b["date"],
                "count": n,
                "avg_confidence": round(b["confidence_sum"] / n / 100, 3) if n else 0.0,
                "rating_distribution": b["rating_distribution"],
                "avg_claims_per_analysis": round(b["claims_sum"] / n, 2) if n else 0.0,
            })

        return {
            "buckets": result_buckets,
            "period": period,
            "bucket": bkt,
            "total_analyses": len(rows),
        }

    def topics(self, period: str = "30d") -> dict:
        """Most frequent topics/keywords extracted from claim texts."""
        key = f"topics:{period}"
        return self._cached(key, lambda: self._compute_topics(period))

    def _compute_topics(self, period: str) -> dict:
        cutoff, _ = _parse_period(period, None)
        rows = self._fetch_rows(cutoff)

        if not rows:
            return {"topics": [], "period": period}

        # Split into two halves to compute trend
        mid = len(rows) // 2
        first_half = rows[:mid]
        second_half = rows[mid:]

        def count_words(subset: list[dict]) -> dict[str, int]:
            freq: dict[str, int] = defaultdict(int)
            for row in subset:
                try:
                    data = json.loads(row["result_json"])
                    for claim in data.get("claims", []):
                        for tok in _tokenize(claim.get("text", "")):
                            freq[tok] += 1
                except (json.JSONDecodeError, KeyError):
                    pass
            return dict(freq)

        full_freq = count_words(rows)
        first_freq = count_words(first_half)
        second_freq = count_words(second_half)

        # Also aggregate rating scores per topic word
        topic_ratings: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            score = RATING_SCORE.get(_normalize_rating(row["overall_rating"]), 3)
            try:
                data = json.loads(row["result_json"])
                for claim in data.get("claims", []):
                    for tok in _tokenize(claim.get("text", "")):
                        topic_ratings[tok].append(score)
            except (json.JSONDecodeError, KeyError):
                pass

        # Top 20 by total frequency
        top = sorted(full_freq.items(), key=lambda x: x[1], reverse=True)[:20]

        result_topics = []
        for word, count in top:
            f1 = first_freq.get(word, 0)
            f2 = second_freq.get(word, 0)
            if f2 > f1 * 1.2:
                trend = "rising"
            elif f2 < f1 * 0.8:
                trend = "declining"
            else:
                trend = "stable"
            ratings = topic_ratings.get(word, [])
            avg_score = round(sum(ratings) / len(ratings), 2) if ratings else 3.0
            result_topics.append({
                "topic": word,
                "count": count,
                "avg_rating_score": avg_score,
                "trend": trend,
            })

        return {"topics": result_topics, "period": period}

    def sources(self, period: str = "30d") -> dict:
        """Top cited sources and their reliability metrics."""
        key = f"sources:{period}"
        return self._cached(key, lambda: self._compute_sources(period))

    def _compute_sources(self, period: str) -> dict:
        cutoff, _ = _parse_period(period, None)
        rows = self._fetch_rows(cutoff)

        domain_data: dict[str, dict] = {}
        for row in rows:
            try:
                data = json.loads(row["result_json"])
                for url in data.get("sources", []):
                    domain = _extract_domain(url)
                    if not domain:
                        continue
                    if domain not in domain_data:
                        domain_data[domain] = {
                            "domain": domain,
                            "citation_count": 0,
                            "first_seen_ts": row["created_at"],
                            "last_seen_ts": row["created_at"],
                        }
                    d = domain_data[domain]
                    d["citation_count"] += 1
                    d["first_seen_ts"] = min(d["first_seen_ts"], row["created_at"])
                    d["last_seen_ts"] = max(d["last_seen_ts"], row["created_at"])
            except (json.JSONDecodeError, KeyError):
                pass

        sources_list = sorted(
            domain_data.values(), key=lambda x: x["citation_count"], reverse=True
        )[:50]

        def ts_to_date(ts: float) -> str:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

        result_sources = [
            {
                "domain": d["domain"],
                "citation_count": d["citation_count"],
                "first_seen": ts_to_date(d["first_seen_ts"]),
                "last_seen": ts_to_date(d["last_seen_ts"]),
            }
            for d in sources_list
        ]

        return {
            "sources": result_sources,
            "total_unique_sources": len(domain_data),
            "period": period,
        }

    def accuracy(self, period: str = "30d", bucket: str | None = None) -> dict:
        """Confidence calibration and rating distribution over time."""
        key = f"accuracy:{period}:{bucket}"
        return self._cached(key, lambda: self._compute_accuracy(period, bucket))

    def _compute_accuracy(self, period: str, bucket: str | None) -> dict:
        cutoff, bkt = _parse_period(period, bucket)
        rows = self._fetch_rows(cutoff)

        # Time-bucketed accuracy
        time_buckets: dict[str, dict] = {}
        for row in rows:
            label = _bucket_label(row["created_at"], _BUCKET_FMT[bkt])
            if label not in time_buckets:
                time_buckets[label] = {
                    "date": label,
                    "confidence_sum": 0,
                    "count": 0,
                    "high_conf_count": 0,   # confidence >= 75
                    "fabricated_count": 0,
                }
            b = time_buckets[label]
            conf = row["confidence"] or 0
            b["confidence_sum"] += conf
            b["count"] += 1
            if conf >= 75:
                b["high_conf_count"] += 1
            if _normalize_rating(row["overall_rating"]) == "FABRICATED":
                b["fabricated_count"] += 1

        accuracy_over_time = []
        for b in time_buckets.values():
            n = b["count"]
            accuracy_over_time.append({
                "date": b["date"],
                "avg_confidence": round(b["confidence_sum"] / n / 100, 3) if n else 0.0,
                "high_confidence_ratio": round(b["high_conf_count"] / n, 3) if n else 0.0,
                "fabricated_ratio": round(b["fabricated_count"] / n, 3) if n else 0.0,
            })

        # Confidence bands (0-20, 20-40, 40-60, 60-80, 80-100)
        band_labels = ["0-20", "20-40", "40-60", "60-80", "80-100"]
        bands: list[dict] = [
            {"range": lbl, "count": 0, "score_sum": 0.0} for lbl in band_labels
        ]
        for row in rows:
            conf = row["confidence"] or 0
            idx = min(conf // 20, 4)
            score = RATING_SCORE.get(_normalize_rating(row["overall_rating"]), 3)
            bands[idx]["count"] += 1
            bands[idx]["score_sum"] += score

        confidence_bands = [
            {
                "range": b["range"],
                "count": b["count"],
                "avg_rating_score": round(b["score_sum"] / b["count"], 2) if b["count"] else 0.0,
            }
            for b in bands
        ]

        # Brier-score: MSE between P(reliable) and binary outcome.
        # P(reliable) is derived from both rating AND confidence:
        #   - RELIABLE/MOSTLY_RELIABLE: P(reliable) = confidence
        #   - MISLEADING/HIGHLY_MISLEADING/FABRICATED: P(reliable) = 1 - confidence
        #   - MIXED: P(reliable) = 0.5
        brier = 0.0
        if rows:
            for row in rows:
                p_rel = _confidence_to_p_reliable(row["overall_rating"], row["confidence"] or 0)
                outcome = 1.0 if RATING_SCORE.get(_normalize_rating(row["overall_rating"]), 0) >= 4 else 0.0
                brier += (p_rel - outcome) ** 2
            brier = round(brier / len(rows), 4)

        return {
            "accuracy_over_time": accuracy_over_time,
            "overall_brier_score": brier,
            "confidence_bands": confidence_bands,
            "period": period,
            "bucket": bkt,
        }

    def platforms(self, period: str = "30d") -> dict:
        """Breakdown of analyses by platform."""
        key = f"platforms:{period}"
        return self._cached(key, lambda: self._compute_platforms(period))

    def _compute_platforms(self, period: str) -> dict:
        cutoff, _ = _parse_period(period, None)
        rows = self._fetch_rows(cutoff)

        plat: dict[str, dict] = {}
        for row in rows:
            p = row["platform"] or "unknown"
            if p not in plat:
                plat[p] = {"platform": p, "count": 0, "confidence_sum": 0, "score_sum": 0}
            d = plat[p]
            d["count"] += 1
            d["confidence_sum"] += row["confidence"] or 0
            d["score_sum"] += RATING_SCORE.get(_normalize_rating(row["overall_rating"]), 3)

        result = sorted(plat.values(), key=lambda x: x["count"], reverse=True)
        platforms_list = [
            {
                "platform": d["platform"],
                "count": d["count"],
                "avg_rating_score": round(d["score_sum"] / d["count"], 2) if d["count"] else 0.0,
                "avg_confidence": round(d["confidence_sum"] / d["count"] / 100, 3) if d["count"] else 0.0,
            }
            for d in result
        ]

        return {"platforms": platforms_list, "period": period}
