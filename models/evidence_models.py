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
    AGREEING = "agreeing"          # Quellen stützen den Claim überwiegend
    CONTRADICTORY = "contradictory"  # Quellen widerlegen den Claim überwiegend
    MIXED = "mixed"                # Stützende und widerlegende Quellen halten sich die Waage
    INSUFFICIENT = "insufficient"  # Zu wenig verwertbare Richtungssignale


class SourceDirection(str, Enum):
    """Aussagebeziehung einer einzelnen Quelle zum Claim.

    Generisches Signal – wird rein textbasiert und strukturell bestimmt,
    ohne claim-spezifische Regeln oder hartcodierte Behauptungen.

    SUPPORTS  – Quelle stützt den Claim inhaltlich
    REFUTES   – Quelle widerlegt den Claim inhaltlich
    NEUTRAL   – Quelle ist relevant, aber ohne klare Richtung
    OFFTOPIC  – Quelle hat keinen ausreichenden Bezug zum Claim
    """
    SUPPORTS = "supports"
    REFUTES = "refutes"
    NEUTRAL = "neutral"
    OFFTOPIC = "offtopic"


class EvidenceType(str, Enum):
    """Klassifiziert die Evidenz-Stärke eines Treffers.

    DIRECT: Offizieller Beschluss, Protokoll, Behördenseite oder direkter
            journalistischer/faktencheckerischer Bericht mit genauem Claim-Bezug.
    CONTEXTUAL: Allgemeiner Themenkontext (z.B. Seite über 15-Minuten-Stadt-Konzept),
                erklärt Hintergrund, belegt aber nicht den konkreten Claim.
    WEAK: Low-Trust, generische Hilfsseite oder nur entfernt thematisch verwandt.
    """
    DIRECT = "direct"
    CONTEXTUAL = "contextual"
    WEAK = "weak"


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
        description="Deprecated – wird aus source_direction abgeleitet. True=stützt Claim, False=widerspricht, None=neutral/unklar",
    )
    source_direction: SourceDirection = Field(
        default=SourceDirection.NEUTRAL,
        description=(
            "Generisches Richtungssignal: SUPPORTS=stützt Claim, REFUTES=widerlegt, "
            "NEUTRAL=relevant aber unklar, OFFTOPIC=kein ausreichender Claim-Bezug. "
            "Low-Trust- und Off-topic-Quellen werden nie als SUPPORTS/REFUTES klassifiziert."
        ),
    )
    evidence_type: EvidenceType = Field(
        default=EvidenceType.CONTEXTUAL,
        description=(
            "DIRECT=belegt den konkreten Claim direkt, "
            "CONTEXTUAL=thematischer Hintergrund, "
            "WEAK=low-trust oder nur entfernt verwandt"
        ),
    )
    claim_scope_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Wie genau deckt die Quelle die konkreten Claim-Details ab? "
            "0.0=nur allgemeiner Kontext, 1.0=alle spezifischen Details belegt. "
            "Prüft: institution, location, policy_context, action/regulation, "
            "sanction/number_in_context."
        ),
    )


class EvidenceContradiction(BaseModel):
    """Ein erkannter Widerspruch zwischen zwei Quellen."""

    source_url_a: str
    source_url_b: str
    description: str = Field(description="Kurze Beschreibung des Widerspruchs")


class EvidenceQualitySignals(BaseModel):
    """Qualitätssignale für das gesamte Evidence-Set.

    Unterscheidung zwischen Präsenz und direkter Evidenz:
        has_primary_source_any          – mind. eine Tier-1/2-Quelle vorhanden
        has_primary_direct_evidence     – Tier-1/2-Quelle, die den Claim direkt belegt
                                          (evidence_type=DIRECT + claim_scope_score >= 0.50)
        has_fact_check_any              – mind. ein Faktenchecker-Ergebnis vorhanden (GFC oder Org)
        has_fact_check_direct_match     – Faktenchecker-Ergebnis mit direktem Claim-Bezug
                                          (GFC-Match oder Faktenchecker-Org mit evidence_type=DIRECT)

    Die rückwärtskompatiblen Felder has_primary_sources und has_fact_check_org_result
    werden auf has_primary_source_any bzw. has_fact_check_any abgebildet.
    """

    # ── Granulare Primary-Source-Signale ──────────────────────────────────────
    has_primary_source_any: bool = Field(
        default=False,
        description="Mind. eine Tier-1/2-Quelle (Behörde/Statistikamt) vorhanden",
    )
    has_primary_direct_evidence: bool = Field(
        default=False,
        description=(
            "Mind. eine Tier-1/2-Quelle mit direktem Claim-Bezug: "
            "evidence_type=DIRECT und claim_scope_score >= 0.50. "
            "Allgemeine Behördenseiten ohne spezifischen Claim-Bezug zählen hier NICHT."
        ),
    )

    # ── Granulare Faktenchecker-Signale ───────────────────────────────────────
    has_fact_check_any: bool = Field(
        default=False,
        description="Mind. ein Faktenchecker-Ergebnis vorhanden (GFC-Match oder Faktenchecker-Org)",
    )
    has_fact_check_direct_match: bool = Field(
        default=False,
        description=(
            "Faktenchecker-Ergebnis mit direktem Claim-Bezug: GFC-Match "
            "oder Faktenchecker-Org mit evidence_type=DIRECT"
        ),
    )

    # ── Rückwärtskompatible Aliase ────────────────────────────────────────────
    has_primary_sources: bool = Field(
        default=False,
        description="Deprecated – identisch mit has_primary_source_any",
    )
    has_fact_check_org_result: bool = Field(
        default=False,
        description="Deprecated – identisch mit has_fact_check_any",
    )

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
    off_topic_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Anteil der Off-topic-Treffer in den Top-5-Ergebnissen "
            "(relevance_score < 0.2). Hoher Wert → Retrieval-Verschmutzung."
        ),
    )
    avg_top5_relevance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Durchschnittlicher Relevanz-Score der Top-5-Treffer",
    )
    low_trust_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Anteil der Low-Trust-Seiten (Währungsrechner, Grammatik, Juraforen etc.) "
            "in den Top-5-Ergebnissen. Hoher Wert → Evidenz kaum belastbar."
        ),
    )
    direct_evidence_count: int = Field(
        default=0,
        description="Anzahl der Quellen mit evidence_type=DIRECT in Top-5",
    )
    contextual_only_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Anteil der Quellen die nur contextual/weak sind (kein direct evidence) "
            "in den Top-5. Hoher Wert → Support Leakage-Risiko."
        ),
    )

    # ── Widerlegungs-Signale ──────────────────────────────────────────────────
    has_direct_refutation: bool = Field(
        default=False,
        description=(
            "Mind. eine Quelle in Top-5 widerlegt den Claim direkt: "
            "source_direction=REFUTES UND evidence_type=DIRECT. "
            "Unterscheidet aktive Widerlegung von bloßem Fehlen von Belegen."
        ),
    )
    direct_refutation_count: int = Field(
        default=0,
        description="Anzahl der Top-5-Quellen mit REFUTES + DIRECT (aktive direkte Widerlegung)",
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

    # ── Institutionelle API-Quellen (OfficialEvidenceItem) ───────────────────
    official_results: list["OfficialEvidenceItem"] = Field(
        default_factory=list,
        description=(
            "Normalisierte Evidence-Items aus institutionellen API-Quellen "
            "(World Bank, Eurostat, EUR-Lex, GLEIF, openFDA, ClinicalTrials, "
            "DailyMed, USPTO, Companies House, OpenAlex, Crossref, arXiv, PubMed). "
            "Wird vom OfficialSourceAgent befüllt, bevor EvidenceBuilderAgent "
            "die Items via to_evidence_item() in web_results integriert."
        ),
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
                direction_labels = {
                    SourceDirection.SUPPORTS: " [stützt Claim]",
                    SourceDirection.REFUTES: " [widerlegt Claim]",
                    SourceDirection.OFFTOPIC: " [off-topic]",
                    SourceDirection.NEUTRAL: "",
                }
                support = direction_labels.get(item.source_direction, "")
                ev_type_label = {
                    EvidenceType.DIRECT: "DIREKT",
                    EvidenceType.CONTEXTUAL: "KONTEXT",
                    EvidenceType.WEAK: "SCHWACH",
                }.get(item.evidence_type, "KONTEXT")
                parts.append(
                    f"[Quelle {i}] [{tier_label}] [{ev_type_label}] {item.source.title}{support}\n"
                    f"  URL: {item.source.url}\n"
                    f"  Claim-Scope: {item.claim_scope_score:.2f}\n"
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
            # Primärquelle: unterscheide allgemeine Präsenz von direkter Evidenz
            primary_label = (
                "direkte Evidenz" if q.has_primary_direct_evidence
                else "nur Präsenz" if q.has_primary_source_any
                else "keine"
            )
            fc_label = (
                "direkter Match" if q.has_fact_check_direct_match
                else "nur Präsenz" if q.has_fact_check_any
                else "kein"
            )
            parts.append(
                f"\n## Evidenz-Qualität\n"
                f"  Quellen gesamt: {self.source_count}, Tier-1/2: {q.top_tier_count}\n"
                f"  Primärquelle (Behörde/Statistik): {primary_label}\n"
                f"  Faktenchecker-Ergebnis: {fc_label}\n"
                f"  Quellen-Konsens: {q.source_consensus.value}\n"
                f"  Qualitätsscore: {q.overall_quality:.2f}\n"
                f"  Off-topic-Rate (Top-5): {q.off_topic_rate:.0%}\n"
                f"  Ø Relevanz (Top-5): {q.avg_top5_relevance:.2f}\n"
                f"  Direkte Evidenz (Top-5): {q.direct_evidence_count}\n"
                f"  Nur-Kontext-Rate (Top-5): {q.contextual_only_rate:.0%}\n"
                f"  Aktive direkte Widerlegung: {'ja (' + str(q.direct_refutation_count) + ')' if q.has_direct_refutation else 'nein'}\n"
            )

        return "\n".join(parts) if parts else "Keine Evidenz gefunden."


# ── Forward-Reference-Auflösung ──────────────────────────────────────────────
# EvidencePack.official_results referenziert OfficialEvidenceItem als String.
# model_rebuild() muss nach dem Import von source_evidence ausgeführt werden.

def _rebuild_evidence_models() -> None:
    from models.source_evidence import OfficialEvidenceItem  # noqa: F401
    EvidencePack.model_rebuild()


try:
    _rebuild_evidence_models()
except Exception:
    pass
