"""Outcome logger – records retrieval-to-verdict outcomes for offline analysis.

Stores structured outcome records in a SQLite database after each verdict.
Records are append-only and designed for offline batch analysis, not
real-time querying during the pipeline.

Schema:
    outcomes(
        id            INTEGER PRIMARY KEY,
        claim_id      TEXT,
        claim_text    TEXT,
        claim_type    TEXT,
        route_domains TEXT,      -- JSON list of detected domains
        route_sources TEXT,      -- JSON list of source names
        route_jurisdiction TEXT,
        queries_used  TEXT,      -- JSON list of search queries
        n_evidence_items INTEGER,
        evidence_quality REAL,   -- overall_quality from EvidenceQualitySignals
        consensus     TEXT,      -- SourceConsensus enum value
        consensus_score REAL,    -- numeric consensus score
        off_topic_rate REAL,
        low_trust_rate REAL,
        direct_evidence_count INTEGER,
        verdict_rating TEXT,     -- FactRating enum value
        verdict_confidence REAL,
        calibration_reasons TEXT, -- JSON list of calibration reason strings
        timestamp     TEXT       -- ISO-8601
    )
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path(".outcome_log.db")
_lock = threading.Lock()


def _get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the outcome database."""
    path = db_path or _DB_PATH
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outcomes (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id             TEXT,
            claim_text           TEXT,
            claim_type           TEXT,
            route_domains        TEXT,
            route_sources        TEXT,
            route_jurisdiction   TEXT,
            queries_used         TEXT,
            n_evidence_items     INTEGER,
            evidence_quality     REAL,
            consensus            TEXT,
            consensus_score      REAL,
            off_topic_rate       REAL,
            low_trust_rate       REAL,
            direct_evidence_count INTEGER,
            verdict_rating       TEXT,
            verdict_confidence   REAL,
            calibration_reasons  TEXT,
            timestamp            TEXT
        )
    """)
    conn.commit()
    return conn


def log_outcome(
    claim_id: str,
    claim_text: str,
    claim_type: str = "",
    route_domains: list[str] | None = None,
    route_sources: list[str] | None = None,
    route_jurisdiction: str = "",
    queries_used: list[str] | None = None,
    n_evidence_items: int = 0,
    evidence_quality: float = 0.0,
    consensus: str = "",
    consensus_score: float = 0.0,
    off_topic_rate: float = 0.0,
    low_trust_rate: float = 0.0,
    direct_evidence_count: int = 0,
    verdict_rating: str = "",
    verdict_confidence: float = 0.0,
    calibration_reasons: list[str] | None = None,
    db_path: Path | None = None,
) -> None:
    """Record a single outcome. Thread-safe, non-blocking best-effort."""
    try:
        with _lock:
            conn = _get_db(db_path)
            conn.execute(
                """
                INSERT INTO outcomes (
                    claim_id, claim_text, claim_type,
                    route_domains, route_sources, route_jurisdiction,
                    queries_used, n_evidence_items, evidence_quality,
                    consensus, consensus_score, off_topic_rate, low_trust_rate,
                    direct_evidence_count,
                    verdict_rating, verdict_confidence, calibration_reasons,
                    timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    claim_text[:500],  # truncate for storage
                    claim_type,
                    json.dumps(route_domains or []),
                    json.dumps(route_sources or []),
                    route_jurisdiction,
                    json.dumps(queries_used or []),
                    n_evidence_items,
                    evidence_quality,
                    consensus,
                    consensus_score,
                    off_topic_rate,
                    low_trust_rate,
                    direct_evidence_count,
                    verdict_rating,
                    verdict_confidence,
                    json.dumps(calibration_reasons or []),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
    except Exception as exc:
        logger.warning("Failed to log outcome for %s: %s", claim_id, exc)


def log_outcome_from_result(
    claim: Any,
    fact_check_result: Any,
    route_result: Any = None,
    queries: list[str] | None = None,
    db_path: Path | None = None,
) -> None:
    """Convenience: extract fields from pipeline objects and log."""
    # Extract evidence quality signals
    evidence_quality = 0.0
    consensus = ""
    consensus_score = 0.0
    off_topic_rate = 0.0
    low_trust_rate = 0.0
    direct_evidence_count = 0
    n_evidence = 0

    ep = getattr(fact_check_result, "evidence_pack", None)
    if ep:
        n_evidence = len(getattr(ep, "web_results", []))
        eq = getattr(ep, "evidence_quality", None)
        if eq:
            evidence_quality = getattr(eq, "overall_quality", 0.0)
            consensus = getattr(eq, "source_consensus", "").value if hasattr(getattr(eq, "source_consensus", ""), "value") else str(getattr(eq, "source_consensus", ""))
            consensus_score = getattr(eq, "consensus_score", 0.0)
            off_topic_rate = getattr(eq, "off_topic_rate", 0.0)
            low_trust_rate = getattr(eq, "low_trust_rate", 0.0)
            direct_evidence_count = getattr(eq, "direct_evidence_count", 0)

    # Extract route info
    route_domains = []
    route_sources = []
    route_jurisdiction = ""
    if route_result:
        route_domains = [d.value if hasattr(d, "value") else str(d) for d in getattr(route_result, "domains", [])]
        route_sources = [s.name if hasattr(s, "name") else str(s) for s in getattr(route_result, "sources", [])]
        route_jurisdiction = getattr(route_result, "jurisdiction", "")

    # Extract calibration reasons
    calibration_reasons = []
    vm = getattr(fact_check_result, "verdict_meta", None)
    if vm:
        calibration_reasons = getattr(vm, "calibration_reasons", [])

    log_outcome(
        claim_id=getattr(claim, "id", ""),
        claim_text=getattr(claim, "text", ""),
        claim_type=getattr(claim, "type", "").value if hasattr(getattr(claim, "type", ""), "value") else "",
        route_domains=route_domains,
        route_sources=route_sources,
        route_jurisdiction=route_jurisdiction,
        queries_used=queries,
        n_evidence_items=n_evidence,
        evidence_quality=evidence_quality,
        consensus=consensus,
        consensus_score=consensus_score,
        off_topic_rate=off_topic_rate,
        low_trust_rate=low_trust_rate,
        direct_evidence_count=direct_evidence_count,
        verdict_rating=fact_check_result.rating.value if hasattr(fact_check_result.rating, "value") else str(fact_check_result.rating),
        verdict_confidence=getattr(fact_check_result, "confidence", 0.0),
        calibration_reasons=calibration_reasons,
        db_path=db_path,
    )
