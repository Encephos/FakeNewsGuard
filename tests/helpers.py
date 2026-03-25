"""Test-Hilfsfunktionen für Evidence-Builder-Tests."""
from __future__ import annotations

from models.evidence_models import EvidenceItem, EvidenceSource, EvidenceType


def make_evidence_item_with_date(
    date: str,
    relevance: float,
    tier: int,
) -> EvidenceItem:
    """Erstelle ein EvidenceItem mit gegebenem Publikationsdatum, Relevanz und Tier."""
    source = EvidenceSource(
        url=f"https://example.com/{date.replace('-', '')}",
        title=f"Quelle {date}",
        domain="example.com",
        domain_tier=tier,
        publication_date=date,
        is_fact_check_org=False,
    )
    return EvidenceItem(
        source=source,
        excerpt="",
        relevance_score=relevance,
        evidence_type=EvidenceType.CONTEXTUAL,
        supports_claim=None,
        claim_scope_score=0.5,
    )
