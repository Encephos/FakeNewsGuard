"""Dataset loading, validation, and ProcessedClaim construction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from eval.models import EvalCase, EvalCategory

# Lazy imports to avoid circular dependencies at module level
_DATA_DIR = Path(__file__).parent / "data"
DEFAULT_CASES_PATH = _DATA_DIR / "cases.jsonl"


def load_cases(path: Optional[Path] = None) -> list[EvalCase]:
    """Load evaluation cases from a JSONL file."""
    p = path or DEFAULT_CASES_PATH
    cases: list[EvalCase] = []
    with open(p) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = json.loads(line)
                cases.append(EvalCase.model_validate(data))
            except Exception as exc:
                raise ValueError(f"Invalid case at line {line_no} in {p}: {exc}") from exc
    return cases


def filter_cases(
    cases: list[EvalCase],
    categories: Optional[list[str]] = None,
    ids: Optional[list[str]] = None,
) -> list[EvalCase]:
    """Filter cases by category and/or IDs."""
    result = cases
    if categories:
        cat_set = {EvalCategory(c) for c in categories}
        result = [c for c in result if c.category in cat_set]
    if ids:
        id_set = set(ids)
        result = [c for c in result if c.id in id_set]
    return result


def build_live_claim(case: EvalCase) -> tuple["ProcessedClaim", "RouteResult | None"]:
    """Build a production-grade ProcessedClaim for live evaluation.

    Starts with the base claim from build_processed_claim(), then runs it
    through ClaimRouter.route_and_apply() to enrich the search_profile
    with institutional source hints — the same enrichment that happens
    in the production pipeline.

    Returns:
        (augmented_claim, route_result) or (base_claim, None) on routing failure.
    """
    claim = build_processed_claim(case)
    try:
        from tools.claim_router import ClaimRouter
        router = ClaimRouter()
        route_result, augmented = router.route_and_apply(claim)
        return augmented, route_result
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Case %s: route_and_apply failed (%s), using base claim", case.id, exc,
        )
        return claim, None


def build_processed_claim(case: EvalCase) -> "ProcessedClaim":
    """Construct a minimal ProcessedClaim from an EvalCase for pipeline use."""
    from models.schemas import ClaimFrame, ClaimSearchProfile, ClaimType, ProcessedClaim

    # Map eval category to claim type
    type_map = {
        EvalCategory.STATISTICAL: ClaimType.STATISTICAL,
        EvalCategory.CURRENT_STATE: ClaimType.FACTUAL,
        EvalCategory.REGULATORY: ClaimType.FACTUAL,
        EvalCategory.CORPORATE: ClaimType.FACTUAL,
        EvalCategory.MEDICAL_PHARMA: ClaimType.FACTUAL,
        EvalCategory.LEGAL_EU: ClaimType.FACTUAL,
        EvalCategory.NOISY_OR_UNDERSPECIFIED: ClaimType.CONTEXTUAL,
        EvalCategory.OFF_TOPIC_TRAPS: ClaimType.OPINION,
        EvalCategory.MULTILINGUAL: ClaimType.FACTUAL,
    }

    claim_type = type_map.get(case.category, ClaimType.FACTUAL)

    # Build search profile from expectations
    exp = case.expectations
    profile = ClaimSearchProfile(
        core_entities=exp.must_have_entities,
        official_source_hints=[
            f"{d}" for d in exp.preferred_domains if d
        ],
    )

    # Build minimal frame
    frame = ClaimFrame(
        raw_text=case.claim_text,
        claim_type=claim_type.value,
    )

    return ProcessedClaim(
        id=case.id,
        text=case.claim_text,
        type=claim_type,
        context=case.context,
        canonical_text=case.claim_text,
        frame=frame,
        search_profile=profile,
        is_checkworthy=True,
        is_valid_claim=case.category != EvalCategory.OFF_TOPIC_TRAPS,
        priority_score=0.8,
        checkworthiness_score=0.8,
    )
