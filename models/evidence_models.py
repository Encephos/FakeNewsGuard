"""Evidence-Modelle für den EvidenceBuilderAgent.

Definiert die Struktur eines EvidencePack – das strukturierte Ergebnis
der Retrieval- und Extraction-Phase.

Trust Boundary:
    Der EvidenceBuilderAgent ist die einzige Stelle, die mit rohem Web-Inhalt
    arbeitet. Er liefert ausschließlich strukturierte EvidencePack-Objekte.
    VerdictAgent und CoVeProcessor sehen niemals ungefilterte Webseiteninhalte.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Quellen-Qualität ──────────────────────────────────────────────────────────


class SourceConsensus(str, Enum):
    AGREEING = "agreeing"          # Quellen stimmen überein
    CONTRADICTORY = "contradictory"  # Quellen widersprechen sich direkt
    MIXED = "mixed"                # Teils übereinstimmend, teils widersprüchlich
    INSUFFICIENT = "insufficient"  # Zu wenig Quellen für Aussage


class EvidenceSource(BaseModel):
    """Metadaten einer einzelnen Quelle (keine Inhalte)."""

    url: str
    title: str = ""
    domain: str = ""
    domain_tier: int = Field(
        default=5,
        ge=1,
        le=5,
        description=(
            "1=Offizielle Statistikämter, 2=Behörden, "
            "3=Qualitätsjournalismus, 4=Faktenchecker, 5=Sonstige"
        ),
    )
    publication_date: str = Field(
        default="",
        description="ISO-Datumsstring oder Freitext, leer wenn unbekannt",
    )
    is_fact_check_org: bool = False
    is_primary_source: bool = False


class EvidenceItem(BaseModel):
    """Ein einzelner Evidenz-Treffer mit Quellenangabe und Textauszug.

    Der excerpt ist auf ~600 Zeichen begrenzt, damit der VerdictAgent
    keine unkontrollierten Webseiteninhalte erhält.
    """

    source: EvidenceSource
    excerpt: str = Field(
        default="",
        max_length=800,
        description="Relevanter Textauszug, max. 800 Zeichen (Trust-Boundary-Limit)",
    )
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supports_claim: Optional[bool] = Field(
        default=None,
        description="True=stützt Claim, False=widerspricht, None=neutral/unklar",
    )


class EvidenceContradiction(BaseModel):
    """Ein erkannter Widerspruch zwischen zwei Quellen."""

    source_url_a: str
    source_url_b: str
    description: str = Field(description="Kurze Beschreibung des Widerspruchs")


class EvidenceQualitySignals(BaseModel):
    """Qualitätssignale für das gesamte Evidence-Set."""

    has_primary_sources: bool = False
    has_fact_check_org_result: bool = False
    source_consensus: SourceConsensus = SourceConsensus.INSUFFICIENT
    freshness_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="1.0 = alle Quellen aktuell, 0.0 = alle veraltet",
    )
    overall_quality: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Gesamtqualitätsscore (Kombination aller Signale)",
    )
    top_tier_count: int = Field(
        default=0,
        description="Anzahl Quellen mit domain_tier <= 2",
    )


# ── Google Fact Check ──────────────────────────────────────────────────────────


class GoogleFactCheckMatch(BaseModel):
    """Treffer aus der Google Fact Check Tools API."""

    claim_reviewed: str
    rating: str = Field(description="Textuelles Urteil der Faktenchecker-Organisation")
    publisher: str = ""
    url: str = ""
    language: str = ""
    title: str = ""


# ── Evidence Pack ──────────────────────────────────────────────────────────────


class EvidencePack(BaseModel):
    """Strukturiertes Ergebnis des EvidenceBuilderAgent.

    Dieses Objekt ist die Trust Boundary zwischen Retrieval und Urteil.
    VerdictAgent und CoVeProcessor arbeiten ausschließlich auf EvidencePack-
    Objekten – niemals direkt mit rohem HTML oder Scraping-Rohtext.
    """

    claim_id: str
    claim_text: str
    canonical_text: str = Field(
        default="",
        description="Kanonisierte Form des Claims (leer = nicht kanonisiert)",
    )

    # ── Retrieval-Metadaten ──────────────────────────────────────────────────
    queries_used: list[str] = Field(
        default_factory=list,
        description="Alle Suchqueries, die für dieses EvidencePack genutzt wurden",
    )
    retrieval_notes: list[str] = Field(
        default_factory=list,
        description="Log-Einträge aus dem Retrieval-Prozess (Retries, Fallbacks, etc.)",
    )

    # ── Faktencheck-Organisation (höchste Priorität) ─────────────────────────
    google_fact_check_matches: list[GoogleFactCheckMatch] = Field(
        default_factory=list,
        description="Treffer aus der Google Fact Check Tools API",
    )

    # ── Web-Evidenz (strukturiert, Trust-Boundary-gefiltert) ─────────────────
    web_results: list[EvidenceItem] = Field(
        default_factory=list,
        description="Alle gefundenen Web-Evidenzen (nach Ranking sortiert)",
    )
    selected_sources: list[EvidenceSource] = Field(
        default_factory=list,
        description="Top-K Quellen, die zur Urteilsbildung verwendet wurden",
    )

    # ── Widersprüche ─────────────────────────────────────────────────────────
    contradictions: list[EvidenceContradiction] = Field(
        default_factory=list,
        description="Erkannte Widersprüche zwischen Quellen",
    )

    # ── Qualitätssignale ─────────────────────────────────────────────────────
    extraction_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Konfidenz der Inhaltsextraktion (0=nichts extrahiert, 1=perfekt)",
    )
    evidence_quality: Optional[EvidenceQualitySignals] = None
    source_count: int = Field(default=0, description="Gesamtanzahl gefundener Quellen")

    def format_for_verdict(self) -> str:
        """Formatiere EvidencePack als strukturierten Text für den VerdictAgent.

        Gibt NUR strukturierte, begrenzte Textauszüge zurück –
        keine rohen HTML-Inhalte oder ungefilterten Webseiteninhalte.
        """
        parts: list[str] = []

        # Google Fact Check zuerst (höchste Glaubwürdigkeit)
        if self.google_fact_check_matches:
            parts.append("## Professionelle Faktenchecks (höchste Priorität)\n")
            for i, fc in enumerate(self.google_fact_check_matches, 1):
                parts.append(
                    f"[FC {i}] {fc.publisher}: \"{fc.claim_reviewed}\"\n"
                    f"  Urteil: {fc.rating}\n"
                    f"  URL: {fc.url}\n"
                )

        # Ausgewählte Quellen mit Auszügen
        if self.web_results:
            parts.append("\n## Evidenz-Quellen\n")
            for i, item in enumerate(self.web_results[:8], 1):
                tier_label = {1: "Statistikamt", 2: "Behörde", 3: "Qualitätsjournalismus",
                              4: "Faktenchecker", 5: "Sonstige"}.get(item.source.domain_tier, "Sonstige")
                support = ""
                if item.supports_claim is True:
                    support = " [stützt Claim]"
                elif item.supports_claim is False:
                    support = " [widerspricht Claim]"
                parts.append(
                    f"[Quelle {i}] [{tier_label}] {item.source.title}{support}\n"
                    f"  URL: {item.source.url}\n"
                    f"  Auszug: {item.excerpt}\n"
                )

        # Widersprüche
        if self.contradictions:
            parts.append("\n## Erkannte Widersprüche\n")
            for c in self.contradictions:
                parts.append(f"- {c.description}\n  ({c.source_url_a} vs. {c.source_url_b})\n")

        # Qualitätssignale
        if self.evidence_quality:
            q = self.evidence_quality
            parts.append(
                f"\n## Evidenz-Qualität\n"
                f"  Quellen gesamt: {self.source_count}, Tier-1/2: {q.top_tier_count}\n"
                f"  Faktenchecker-Ergebnis vorhanden: {q.has_fact_check_org_result}\n"
                f"  Quellen-Konsens: {q.source_consensus.value}\n"
                f"  Qualitätsscore: {q.overall_quality:.2f}\n"
            )

        return "\n".join(parts) if parts else "Keine Evidenz gefunden."
