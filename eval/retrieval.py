"""Production retrieval wrappers for evaluation.

Wraps the productive query-building and evidence-scoring functions
so the eval runner can use the same retrieval path as production
without instantiating the full EvidenceBuilderAgent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Production query building
# ---------------------------------------------------------------------------


def build_production_queries(
    claim: "ProcessedClaim",  # noqa: F821
    route_result: Optional[Any] = None,
    config: Optional[Any] = None,
    *,
    force_current_state: Optional[bool] = None,
) -> tuple[list[str], list["SearXNGQuery"]]:  # noqa: F821
    """Build search queries using the production pipeline logic.

    Args:
        force_current_state: If set, overrides the heuristic detection.
            Used by eval to honour the case category (the heuristic only
            detects office-holder claims, not all time-sensitive ones).

    Returns:
        (plain_queries, searxng_queries) where plain_queries is the
        deduplicated list of query strings and searxng_queries is the
        list of SearXNGQuery objects with per-query engine/category
        selection and multi-page support.
    """
    from agents.evidence_builder import _dedup_queries, _dedup_searxng_queries
    from agents.query_builder import (
        _build_search_queries,
        _categories_for_claim,
        _is_current_state_claim,
    )
    from tools.data_loader import searxng_engines
    from tools.web_search import SearXNGQuery

    # --- Phase 1: Generate queries from claim profile / type ---------------
    queries = _build_search_queries(claim, original_text="")
    if not queries:
        # Absolute fallback: raw claim text
        queries = [claim.text]

    # Ensure claim text is always included as a query (production does this
    # in the fallback path; eval claims may have thin profiles that produce
    # only hint-based queries like "destatis.de" without the actual claim).
    claim_text_norm = claim.text.strip().lower()
    if not any(claim_text_norm in q.lower() for q in queries):
        queries.insert(0, claim.text)

    # --- Phase 2: Category selection ---------------------------------------
    categories = _categories_for_claim(claim)

    # --- Phase 3: Current-state recency override ---------------------------
    is_current_state = (
        force_current_state
        if force_current_state is not None
        else _is_current_state_claim(claim.text)
    )
    if is_current_state:
        current_year = str(datetime.now(timezone.utc).year)
        categories = "general,news"
        queries = [
            f"{q} {current_year}" if current_year not in q else q
            for q in queries
        ]
        if queries and "aktuell" not in queries[0].lower():
            queries[0] = f"{queries[0]} aktuell"

    # --- Phase 4: Deduplication --------------------------------------------
    queries = _dedup_queries(queries)

    # --- Phase 5: Build SearXNGQuery objects -------------------------------
    engines_map = searxng_engines()
    news_engines = engines_map.get("news", ["duckduckgo", "brave", "tagesschau"])
    web_engines = engines_map.get("web", ["duckduckgo", "brave", "qwant"])

    searxng_queries: list[SearXNGQuery] = []
    for i, q in enumerate(queries):
        sq = SearXNGQuery(query=q, categories=categories)

        # Per-query engine selection
        if "Faktencheck" in q or "Falschmeldung" in q:
            sq.engines = news_engines
        elif is_current_state:
            sq.engines = news_engines
            sq.time_range = "month"
        else:
            sq.engines = web_engines
        searxng_queries.append(sq)

        # Multi-page for top-2 queries
        if i < 2:
            sq2 = SearXNGQuery(
                query=q,
                categories=categories,
                engines=sq.engines,
                time_range=sq.time_range if hasattr(sq, "time_range") and sq.time_range else None,
                pageno=2,
            )
            searxng_queries.append(sq2)

    # --- Phase 6: Site hints from router -----------------------------------
    if route_result and getattr(route_result, "site_hints", None) and queries:
        for hint in route_result.site_hints[:2]:
            searxng_queries.append(SearXNGQuery(
                query=f"{queries[0]} {hint}",
                categories=categories,
            ))

    searxng_queries = _dedup_searxng_queries(searxng_queries)
    return queries, searxng_queries


# ---------------------------------------------------------------------------
# 2. Lite evidence item building (snippet-based, no scraping)
# ---------------------------------------------------------------------------


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _extract_date_from_snippet(snippet: str, url: str) -> str:
    """Try to extract a publication date from snippet text or URL.

    Returns ISO date string or empty string.  This is best-effort for
    lite eval mode where we don't scrape actual pages.
    """
    import re

    _GERMAN_MONTHS = {
        "jan": "01", "januar": "01", "feb": "02", "februar": "02",
        "mär": "03", "märz": "03", "mar": "03", "apr": "04", "april": "04",
        "mai": "05", "jun": "06", "juni": "06", "jul": "07", "juli": "07",
        "aug": "08", "august": "08", "sep": "09", "september": "09",
        "okt": "10", "oktober": "10", "oct": "10", "nov": "11", "november": "11",
        "dez": "12", "dezember": "12", "dec": "12",
    }

    # Pattern 1: ISO-like dates in URL (e.g. /2024/03/15/ or /2024-03-15)
    m = re.search(r"/(20[12]\d)[/-](\d{2})[/-](\d{2})", url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Pattern 2: German numeric date in snippet (e.g. "15.03.2024")
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(20[12]\d)", snippet)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"

    # Pattern 3: German/English named-month date in snippet
    # e.g. "28. Feb. 2025", "14. März 2025", "11 Mar 2026", "Feb 28, 2025"
    m = re.search(
        r"(\d{1,2})\.?\s+([A-Za-zÄäÖöÜü]+)\.?\s+(20[12]\d)", snippet
    )
    if m:
        month_key = m.group(2).lower().rstrip(".")
        month_num = _GERMAN_MONTHS.get(month_key)
        if month_num:
            return f"{m.group(3)}-{month_num}-{m.group(1).zfill(2)}"

    # Pattern 3b: English "Month DD, YYYY" (e.g. "Feb 28, 2025", "March 11, 2026")
    m = re.search(
        r"([A-Za-z]+)\s+(\d{1,2}),?\s+(20[12]\d)", snippet
    )
    if m:
        month_key = m.group(1).lower().rstrip(".")
        month_num = _GERMAN_MONTHS.get(month_key)
        if month_num:
            return f"{m.group(3)}-{month_num}-{m.group(2).zfill(2)}"

    # Pattern 4: Year-only in URL path (e.g. /2024/ or /2025/)
    m = re.search(r"/(20[12]\d)/", url)
    if m:
        return f"{m.group(1)}-06-15"  # mid-year estimate

    return ""


def build_lite_evidence_items(
    ranked_sources_dicts: list[dict[str, Any]],
    claim_text: str,
    profile: Optional["ClaimSearchProfile"] = None,  # noqa: F821
) -> list[dict[str, Any]]:
    """Build evidence items from ranked sources using snippets only.

    This is the 'lite eval mode': no HTTP scraping, but structurally
    identical evidence items that feed all retrieval metrics.

    Each item follows the same dict schema as production EvidenceItem.model_dump().
    """
    from agents.evidence_scoring import (
        _classify_evidence_type,
        _classify_source_direction,
        _compute_claim_scope_score,
        _domain_tier,
        _is_fact_check_org,
        _is_low_trust_site,
        _relevance_score,
    )
    from tools.web_search import SearchResult

    items: list[dict[str, Any]] = []

    for rs_dict in ranked_sources_dicts:
        result_dict = rs_dict.get("result", {})
        url = result_dict.get("url", "")
        title = result_dict.get("title", "")
        snippet = result_dict.get("snippet", "") or result_dict.get("content", "")

        if not url:
            continue

        # Reconstruct SearchResult for _relevance_score
        sr = SearchResult(
            title=title,
            url=url,
            snippet=snippet,
            content=result_dict.get("content", ""),
        )

        # Compute scores using production functions
        relevance = _relevance_score(sr, claim_text, profile)
        domain = _extract_domain(url)
        tier = _domain_tier(url)
        is_low_trust = _is_low_trust_site(url, title, snippet)
        is_fact_check = _is_fact_check_org(url)
        scope = _compute_claim_scope_score(snippet, profile)

        # Classify evidence
        evidence_type = _classify_evidence_type(
            item_relevance=relevance,
            claim_scope=scope,
            domain_tier=tier,
            is_fact_check=is_fact_check,
            is_low_trust=is_low_trust,
        )
        direction = _classify_source_direction(
            excerpt=snippet,
            relevance_score=relevance,
            evidence_type=evidence_type,
            is_low_trust=is_low_trust,
        )

        # Best-effort date extraction from snippet/URL
        pub_date = _extract_date_from_snippet(snippet, url)

        item = {
            "source": {
                "url": url,
                "title": title,
                "domain": domain,
                "domain_tier": tier,
                "publication_date": pub_date,
                "is_fact_check_org": is_fact_check,
                "is_primary_source": tier <= 2,
            },
            "excerpt": snippet[:800] if snippet else "",
            "relevance_score": round(relevance, 4),
            "extraction_confidence": 0.3,  # snippet-level
            "source_direction": direction.value,
            "evidence_type": evidence_type.value,
            "claim_scope_score": round(scope, 4),
        }
        items.append(item)

    return items


# ---------------------------------------------------------------------------
# 3. Quality signal computation
# ---------------------------------------------------------------------------


def compute_quality_signals(
    item_dicts: list[dict[str, Any]],
    gfc_dicts: list[dict[str, Any]],
    is_current_state: bool = False,
) -> dict[str, Any]:
    """Compute quality signals from lite evidence items.

    Converts dicts back to Pydantic models and delegates to production
    _compute_quality_signals().
    """
    from agents.evidence_scoring import _compute_quality_signals
    from models.evidence_models import (
        EvidenceItem,
        EvidenceSource,
        EvidenceType,
        GoogleFactCheckMatch,
        SourceDirection,
    )

    # Build EvidenceItem objects from dicts
    evidence_items: list[EvidenceItem] = []
    for d in item_dicts:
        try:
            src = d.get("source", {})
            item = EvidenceItem(
                source=EvidenceSource(
                    url=src.get("url", ""),
                    title=src.get("title", ""),
                    domain=src.get("domain", ""),
                    domain_tier=src.get("domain_tier", 5),
                    publication_date=src.get("publication_date", ""),
                    is_fact_check_org=src.get("is_fact_check_org", False),
                    is_primary_source=src.get("is_primary_source", False),
                ),
                excerpt=d.get("excerpt", ""),
                relevance_score=d.get("relevance_score", 0.0),
                extraction_confidence=d.get("extraction_confidence", 0.3),
                source_direction=SourceDirection(d.get("source_direction", "neutral")),
                evidence_type=EvidenceType(d.get("evidence_type", "weak")),
                claim_scope_score=d.get("claim_scope_score", 0.0),
            )
            evidence_items.append(item)
        except Exception as exc:
            logger.debug("Skipping invalid evidence item dict: %s", exc)

    # Build GoogleFactCheckMatch objects from dicts
    gfc_matches: list[GoogleFactCheckMatch] = []
    for g in gfc_dicts:
        try:
            gfc_matches.append(GoogleFactCheckMatch(
                claim_reviewed=g.get("claim_reviewed", ""),
                rating=g.get("rating", ""),
                publisher=g.get("publisher", ""),
                url=g.get("url", ""),
                language=g.get("language", ""),
                title=g.get("title", ""),
            ))
        except Exception as exc:
            logger.debug("Skipping invalid GFC dict: %s", exc)

    signals = _compute_quality_signals(
        evidence_items,
        gfc_matches,
        is_current_state=is_current_state,
    )
    return signals.model_dump()
