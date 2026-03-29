"""Retrieval snapshot capture, serialization, and deserialization.

A snapshot captures all intermediate pipeline stages for a single case,
enabling deterministic replay without network access.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class RetrievalSnapshot(BaseModel):
    """Complete retrieval pipeline state for a single evaluation case."""

    case_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Stage 1: Queries
    generated_queries: list[str] = Field(default_factory=list)
    deduped_queries: list[str] = Field(default_factory=list)
    searxng_queries: list[dict[str, Any]] = Field(default_factory=list)

    # Stage 2: Raw results per backend
    searxng_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    langsearch_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    gfc_results: list[dict[str, Any]] = Field(default_factory=list)
    source_client_results: list[dict[str, Any]] = Field(default_factory=list)

    # Stage 3: Merged & deduped
    merged_results: list[dict[str, Any]] = Field(default_factory=list)

    # Stage 4: Ranked
    ranked_sources: list[dict[str, Any]] = Field(default_factory=list)

    # Stage 5: Evidence items (after scraping + scoring)
    evidence_items: list[dict[str, Any]] = Field(default_factory=list)

    # Stage 6: Quality signals
    quality_signals: dict[str, Any] = Field(default_factory=dict)

    # Router output
    route_result: Optional[dict[str, Any]] = None

    # Metadata
    backends_used: list[str] = Field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0

    # Debug / provenance notes (query sources, recency overrides, etc.)
    debug_notes: list[str] = Field(default_factory=list)


def save_snapshot(snapshot: RetrievalSnapshot, snapshots_dir: Path) -> Path:
    """Save a snapshot to disk as JSON."""
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshots_dir / f"{snapshot.case_id}.json"
    path.write_text(
        snapshot.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return path


def load_snapshot(case_id: str, snapshots_dir: Path) -> RetrievalSnapshot:
    """Load a snapshot from disk."""
    path = snapshots_dir / f"{case_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No snapshot for case '{case_id}' at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return RetrievalSnapshot.model_validate(data)


def snapshot_exists(case_id: str, snapshots_dir: Path) -> bool:
    """Check whether a snapshot file exists for a case."""
    return (snapshots_dir / f"{case_id}.json").exists()


def search_result_to_dict(sr: Any) -> dict[str, Any]:
    """Convert a SearchResult dataclass to a plain dict for snapshot storage."""
    return {
        "title": sr.title,
        "url": sr.url,
        "snippet": sr.snippet,
        "content": getattr(sr, "content", ""),
    }


def dict_to_search_result(d: dict[str, Any]) -> Any:
    """Reconstruct a SearchResult from a snapshot dict."""
    from tools.web_search import SearchResult
    return SearchResult(
        title=d.get("title", ""),
        url=d.get("url", ""),
        snippet=d.get("snippet", ""),
        content=d.get("content", ""),
    )


def ranked_source_to_dict(rs: Any) -> dict[str, Any]:
    """Convert a RankedSource dataclass to a plain dict for snapshot storage."""
    return {
        "result": search_result_to_dict(rs.result),
        "tier": rs.tier.value if hasattr(rs.tier, "value") else str(rs.tier),
        "relevance_score": rs.relevance_score,
        "should_scrape": rs.should_scrape,
        "skip_reason": rs.skip_reason,
        "hybrid_score": rs.hybrid_score,
    }


def evidence_item_to_dict(item: Any) -> dict[str, Any]:
    """Convert an EvidenceItem Pydantic model to dict for snapshot storage."""
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return dict(item)
