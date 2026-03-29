"""Quellübergreifendes Evidence-Schema für institutionelle API-Quellen.

Dieses Modul definiert das einheitliche interne Datenmodell, in das alle
offiziellen und wissenschaftlichen Quellen normalisiert werden:

    Statistik:       World Bank, Eurostat
    Recht:           EUR-Lex
    Unternehmens-:   GLEIF, Companies House
    Pharma/Med.:     openFDA, DailyMed, ClinicalTrials.gov
    Wissenschaft:    OpenAlex, Crossref, arXiv, PubMed
    Patent:          USPTO

Designprinzipien:
    - Keine quellspezifischen Sonderfälle im Kernschema.
      Quelleigene Felder fließen optional in ``raw_fields`` (Debugging).
    - ``NormalizedFact`` ist der atomare Faktenträger.
      Ein ``OfficialEvidenceItem`` enthält 0–N Fakten.
    - Alle Policy-Felder (storage_policy, display_policy, license_status)
      spiegeln direkt ``SourceConfig``-Werte – kein Wissen über Quellen nötig.
    - ``to_evidence_item()`` erzeugt ein ``EvidenceItem`` für Abwärtskompatibilität
      mit dem bestehenden EvidenceBuilderAgent / VerdictAgent.

Neue Quellen einbinden:
    1. Adapter-Klasse unter ``tools/sources/clients/<source>.py`` anlegen.
    2. ``SourceConfig``-Eintrag in ``tools/sources/registry.py`` ergänzen.
    3. Adapter gibt ``OfficialEvidenceItem``-Objekte zurück – kein Schema-Edit nötig.
    4. ``FactType`` ggf. um domänenspezifische Werte erweitern (letzte Resort).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from tools.sources.types import (
    AllowedDisplay,
    AllowedStorage,
    ClaimDomain,
    CommercialUsePolicy,
)

if TYPE_CHECKING:
    # Nur für Type-Checker sichtbar – kein Runtime-Import → kein Zirkelimport.
    from models.evidence_models import EvidenceItem


# ── Faktentypologie ──────────────────────────────────────────────────────────


class FactType(str, Enum):
    """Klassifikation eines normalisierten Fakts nach Wissensdomäne.

    Bewusst generisch gehalten – kein Bezug zu einzelnen Quell-APIs.
    Neue Domänen werden hier ergänzt; bestehende Adapter müssen nicht
    angepasst werden solange sie auf ``FACT_STATEMENT`` zurückfallen können.

    Statistik / Wirtschaft:
        INDICATOR_VALUE     – Einzelwert eines Indikators (z.B. BIP 2023)
        TIME_SERIES_POINT   – Datenpunkt in einer Zeitreihe

    Recht / Regulierung:
        LEGAL_PROVISION     – Rechtsartikel / Absatz / Norm
        LEGAL_STATUS        – Rechtsstatus (in Kraft, aufgehoben, geändert)
        REGULATORY_DECISION – Behördliche Entscheidung (Zulassung, Ablehnung)

    Medizin / Klinik / Pharma:
        CLINICAL_OUTCOME        – Studienergebnis / Endpunkt
        DRUG_INDICATION         – Zugelassene Indikation eines Arzneimittels
        DRUG_CONTRAINDICATION   – Kontraindikation
        ADVERSE_EVENT           – Unerwünschtes Ereignis / Nebenwirkung
        TRIAL_STATUS            – Status einer klinischen Studie

    Wissenschaft / Literatur:
        RESEARCH_FINDING    – Kernaussage einer Publikation
        CITATION_COUNT      – Bibliometrischer Wert (Zitationen, h-Index)

    Unternehmen / Identität:
        ENTITY_REGISTRATION – Unternehmens- oder Entitätsregistrierung
        ENTITY_STATUS       – Aktueller Status (aktiv, aufgelöst, insolvent)
        OWNERSHIP_RELATION  – Konzernstruktur / Beteiligungsverhältnis

    Patent:
        PATENT_CLAIM        – Schutzanspruch eines Patents
        PATENT_STATUS       – Patentstatus (erteilt, ausstehend, abgelaufen)

    Generisch:
        FACT_STATEMENT      – Fallback für nicht kategorisierbare Aussagen
    """

    # Statistik / Wirtschaft
    INDICATOR_VALUE = "indicator_value"
    TIME_SERIES_POINT = "time_series_point"

    # Recht / Regulierung
    LEGAL_PROVISION = "legal_provision"
    LEGAL_STATUS = "legal_status"
    REGULATORY_DECISION = "regulatory_decision"

    # Medizin / Klinik / Pharma
    CLINICAL_OUTCOME = "clinical_outcome"
    DRUG_INDICATION = "drug_indication"
    DRUG_CONTRAINDICATION = "drug_contraindication"
    ADVERSE_EVENT = "adverse_event"
    TRIAL_STATUS = "trial_status"

    # Wissenschaft / Literatur
    RESEARCH_FINDING = "research_finding"
    CITATION_COUNT = "citation_count"

    # Unternehmen / Identität
    ENTITY_REGISTRATION = "entity_registration"
    ENTITY_STATUS = "entity_status"
    OWNERSHIP_RELATION = "ownership_relation"

    # Patent
    PATENT_CLAIM = "patent_claim"
    PATENT_STATUS = "patent_status"

    # Generisch
    FACT_STATEMENT = "fact_statement"


# ── NormalizedFact ───────────────────────────────────────────────────────────


class NormalizedFact(BaseModel):
    """Atomare, normalisierte Fakteneinheit.

    Ein ``NormalizedFact`` repräsentiert genau eine überprüfbare Aussage
    aus einem Quelldatensatz. Durch den generischen Aufbau funktioniert
    das Schema für alle Domänen:

        Statistik:   subject="Deutschland", predicate="BIP (nominal)",
                     value="4.08", unit="Billionen USD", reference_period="2023"

        Recht:       subject="DSGVO Art. 83 Abs. 4",
                     predicate="Bußgeldrahmen",
                     value="bis 10.000.000 EUR oder 2% Jahresumsatz"

        Klinik:      subject="Semaglutid 2.4 mg/Woche",
                     predicate="Gewichtsreduktion vs. Placebo",
                     value="-12.4", unit="%", qualifier="p < 0.001, RCT"

        Unternehmen: subject="Volkswagen AG",
                     predicate="LEI",
                     value="529900HNOAA1KXQJUQ27"
    """

    fact_id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:12],
        description="Kurzkennung innerhalb des EvidenceItem (12 Hex-Zeichen)",
    )
    fact_type: FactType = Field(
        default=FactType.FACT_STATEMENT,
        description="Domänenklassifikation des Fakts",
    )

    # Semantische Felder (Subjekt-Prädikat-Objekt-Tripel)
    subject: str = Field(
        default="",
        description="Worum es geht: Entität, Land, Institution, Arzneimittel, ...",
    )
    predicate: str = Field(
        default="",
        description="Die Eigenschaft oder Beziehung, z.B. 'BIP nominal', 'Zulassungsstatus'",
    )
    value: str = Field(
        default="",
        description="Wert als String – universell verwendbar, auch für kategorische Werte",
    )
    numeric_value: Optional[float] = Field(
        default=None,
        description="Numerischer Wert wenn vorhanden (maschinenlesbar für NumberAuditor)",
    )
    unit: str = Field(
        default="",
        description="Einheit: '%', 'USD', 'mg', 'Jahre', 'Millionen', 'per 100k', ...",
    )

    # Zeitlicher / geografischer Kontext
    reference_period: str = Field(
        default="",
        description="Bezugszeitraum: ISO-Datum, Jahres-Range '2020-2023', 'Q3 2022', ...",
    )
    reference_entity: str = Field(
        default="",
        description="Geografische oder institutionelle Bezugsgröße: 'EU-27', 'US', 'DE', ...",
    )
    qualifier: str = Field(
        default="",
        description=(
            "Einschränkungen, Konfidenzintervalle, Signifikanzniveaus, Methodenhinweise. "
            "Beispiel: 'p < 0.001, n=2000, RCT, Fase III'"
        ),
    )

    # Trust-Boundary-konformer Auszug (kein unkontrollierter HTML-Inhalt)
    source_snippet: str = Field(
        default="",
        max_length=400,
        description=(
            "Wörtlicher Auszug aus der Quelle (max. 400 Zeichen). "
            "Trust-Boundary: Adapter kürzen selbst; kein roher HTML-Inhalt."
        ),
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Konfidenz in diesen einzelnen Fakt (1.0 = direkt aus Primärquelle)",
    )


# ── OfficialEvidenceItem ─────────────────────────────────────────────────────


class OfficialEvidenceItem(BaseModel):
    """Einheitliches Evidence-Item für alle institutionellen API-Quellen.

    Dieses Schema normalisiert Ergebnisse aus 13+ Datenquellen in eine
    einheitliche Struktur. Quelladapter implementieren die Befüllung;
    das Schema selbst enthält keine quellspezifische Logik.

    Felder-Rationale:

        evidence_id       – Stabile UUID für Deduplizierung über Retrieval-Läufe hinweg.

        source_id         – Link zum SourceRegistry-Eintrag; enthält alle Policy-
                            Metadaten ohne Wiederholung im Item selbst.

        source_class      – Adapter-Klasse als String; ermöglicht Tracing ohne
                            zirkuläre Imports.

        record_id         – Nativer Primärschlüssel der Datenbank:
                            DOI (Crossref/OpenAlex/arXiv), PMID (PubMed),
                            NCT ID (ClinicalTrials), LEI (GLEIF),
                            Patentnummer (USPTO), Company Number (Companies House),
                            SPL-SetID (DailyMed), Indikatorcode (World Bank/Eurostat),
                            CELEX-Nummer (EUR-Lex).

        abstract          – Kurzbeschreibung / Abstract (max. 1200 Zeichen).
                            Trust-Boundary: kein unkontrollierter HTML-Inhalt.

        jurisdiction      – ISO 3166-1 Alpha-2 (DE, US, GB, ...), 'EU', 'global'
                            oder Freitext für supranationale Einheiten.

        domains           – Mehrfachdomänen möglich (z.B. GLEIF: CORPORATE + LEGAL).

        entity_mentions   – Normalisierte Entitätsliste für claim_scope_score-
                            Berechnung durch EvidenceBuilderAgent.

        claim_relevance   – Wie direkt belegt dieses Item den geprüften Claim?
                            Wird vom Adapter basierend auf Query-Match gesetzt.

        authority_score   – Direkt aus SourceConfig.authority_weight kopiert.
                            Nicht selbst berechnen – Registry ist kanonisch.

        recency_score     – compute_recency_score() als Hilfsfunktion verfügbar.

        license_status    – CommercialUsePolicy aus SourceConfig.
        storage_policy    – AllowedStorage aus SourceConfig.
        display_policy    – AllowedDisplay aus SourceConfig.

        normalized_facts  – Liste von NormalizedFact (0–N).
                            Statistiken: je Datenpunkt ein Fakt.
                            Rechtsdokumente: je Artikel ein Fakt.
                            Studien: je Endpunkt ein Fakt.

        confidence        – Gesamtbewertung: compute_confidence() empfohlen.

        raw_fields        – Originale API-Felder für Debugging/Rückverfolgung.
                            Downstream-Agenten ignorieren dieses Feld.
    """

    # ── Identifikation ────────────────────────────────────────────────────────
    evidence_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID4 – eindeutige Item-ID (stabil über Retrieval-Läufe wenn record_id gesetzt)",
    )
    source_id: str = Field(
        description="Registry-ID der Quelle (z.B. 'world_bank', 'pubmed', 'eur_lex')",
    )
    source_class: str = Field(
        default="",
        description=(
            "Vollständiger Python-Pfad des Adapters aus SourceConfig.source_class. "
            "Beispiel: 'tools.sources.clients.pubmed.PubMedClient'"
        ),
    )
    record_id: str = Field(
        default="",
        description=(
            "Nativer Primärschlüssel der Quelldatenbank. Quellenspezifisch:\n"
            "  arXiv/Crossref/OpenAlex: DOI\n"
            "  PubMed: PMID\n"
            "  ClinicalTrials: NCT ID\n"
            "  GLEIF: LEI (ISO 17442)\n"
            "  USPTO: Patentnummer\n"
            "  Companies House: Company Number\n"
            "  DailyMed: SPL Set-ID\n"
            "  World Bank: '<iso3>/<indicator_code>/<year>'\n"
            "  Eurostat: '<dataset_code>/<geo>/<time>'\n"
            "  EUR-Lex: CELEX-Nummer\n"
            "  openFDA: Application Number / Report ID"
        ),
    )

    # ── Inhalt ────────────────────────────────────────────────────────────────
    title: str = Field(default="")
    url: str = Field(default="")
    abstract: str = Field(
        default="",
        max_length=1200,
        description=(
            "Abstract oder Kurzbeschreibung (max. 1200 Zeichen). "
            "Trust-Boundary: Adapter kürzen – kein ungefilteter HTML-Inhalt."
        ),
    )

    # ── Zeitstempel ───────────────────────────────────────────────────────────
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Abrufzeitpunkt (UTC, timezone-aware)",
    )
    published_at: Optional[date] = Field(
        default=None,
        description=(
            "Veröffentlichungs-/Erhebungsdatum. Semantik je Quelle:\n"
            "  Eurostat/World Bank: Bezugsjahr des Datenpunkts\n"
            "  EUR-Lex: Datum des Inkrafttretens\n"
            "  PubMed/arXiv: Publikationsdatum\n"
            "  ClinicalTrials: Studienbeginn\n"
            "  Leer wenn unbekannt."
        ),
    )

    # ── Kontext ───────────────────────────────────────────────────────────────
    jurisdiction: str = Field(
        default="",
        description=(
            "Rechtlicher / geografischer Geltungsbereich. Konventionen:\n"
            "  ISO 3166-1 Alpha-2: 'DE', 'US', 'GB', 'FR', ...\n"
            "  Supranationale Einheiten: 'EU', 'UN', 'WTO', ...\n"
            "  Globaler Geltungsbereich: 'global'\n"
            "  Leer wenn nicht anwendbar (z.B. reine Metadaten-Quellen)"
        ),
    )
    domains: list[ClaimDomain] = Field(
        default_factory=list,
        description=(
            "Thematische Domänen aus ClaimDomain. Mehrfachwerte möglich: "
            "GLEIF → [CORPORATE, LEGAL, FINANCIAL]. "
            "Adapter übernehmen aus SourceConfig.claim_domains, "
            "können aber einschränken wenn das konkrete Dokument enger ist."
        ),
    )
    entity_mentions: list[str] = Field(
        default_factory=list,
        description=(
            "Normalisierte Entitäten im Dokument. Adapter befüllen nach Möglichkeit:\n"
            "  Unternehmen (GLEIF/Companies House): legal entity name\n"
            "  Arzneimittel (openFDA/DailyMed): INN / Handelsname\n"
            "  Studien (ClinicalTrials): Interventionsname, Condition\n"
            "  Statistiken: Land-/Regionscode\n"
            "  Recht: Gesetz/Verordnungsbezeichnung"
        ),
    )

    # ── Relevanz & Autorität ──────────────────────────────────────────────────
    claim_relevance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Relevanz für den geprüften Claim (0=irrelevant, 1=belegt direkt). "
            "Adapter setzen diesen Wert basierend auf Query-Match-Güte. "
            "Wird vom EvidenceBuilderAgent ggf. überschrieben."
        ),
    )
    authority_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Autoritätsgewicht der Quelle – direkt aus SourceConfig.authority_weight. "
            "Adapter sollen diesen Wert 1:1 aus der Registry übernehmen."
        ),
    )
    recency_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Aktualitätsscore (1.0=aktuell, 0.0=sehr alt). "
            "Adapter rufen compute_recency_score() auf. "
            "Halbwertszeit je Domäne unterschiedlich – "
            "Statistiken altern schneller als Rechtsdokumente."
        ),
    )

    # ── Richtlinien (aus SourceConfig) ────────────────────────────────────────
    license_status: CommercialUsePolicy = Field(
        default=CommercialUsePolicy.UNKNOWN,
        description="Kommerzielle Nutzungsrechte – direkt aus SourceConfig.commercial_reuse_ok",
    )
    storage_policy: AllowedStorage = Field(
        default=AllowedStorage.SESSION_ONLY,
        description="Zulässige Speicherung – direkt aus SourceConfig.allowed_storage",
    )
    display_policy: AllowedDisplay = Field(
        default=AllowedDisplay.METADATA_ONLY,
        description="Zulässige Anzeige – direkt aus SourceConfig.allowed_display",
    )

    # ── Strukturierte Fakten ──────────────────────────────────────────────────
    normalized_facts: list[NormalizedFact] = Field(
        default_factory=list,
        description=(
            "Strukturierte, atomare Fakten aus diesem Quelldatensatz. "
            "Dichte je Quellentyp: Statistiken → 1–3 Fakten (je Datenpunkt); "
            "Rechtsdokumente → 1–N (je Artikel); "
            "Studien → 1–N (je Endpunkt); "
            "Unternehmensregister → 1–3 (Status, LEI, Struktur)."
        ),
    )

    # ── Gesamtbewertung ───────────────────────────────────────────────────────
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Gesamtbewertung dieses Evidence-Items. "
            "Adapter rufen compute_confidence() auf nach dem Befüllen aller Teilscores."
        ),
    )

    # ── Debugging ─────────────────────────────────────────────────────────────
    raw_fields: dict = Field(
        default_factory=dict,
        description=(
            "Originale Felder aus der API-Antwort (nur für Debugging und Tracing). "
            "Downstream-Agenten (VerdictAgent, CoVeProcessor) ignorieren dieses Feld. "
            "Adapter sollten nur scalar-serialisierbare Werte eintragen."
        ),
    )

    # ── Berechnungsmethoden ───────────────────────────────────────────────────

    def compute_confidence(self) -> float:
        """Berechne kombinierten Konfidenzwert aus den drei Teilscores.

        Gewichtung:
            authority_score  × 0.40  (Quellautorität dominiert)
            claim_relevance  × 0.40  (Relevanz für den konkreten Claim)
            recency_score    × 0.20  (Aktualität)

        Adapter rufen diese Methode auf und weisen das Ergebnis ``confidence`` zu::

            item.confidence = item.compute_confidence()
        """
        return round(
            self.authority_score * 0.40
            + self.claim_relevance * 0.40
            + self.recency_score * 0.20,
            4,
        )

    def to_evidence_item(self) -> "EvidenceItem":
        """Konvertiere zu ``EvidenceItem`` für Abwärtskompatibilität.

        Ermöglicht die nahtlose Integration von ``OfficialEvidenceItem``
        in die bestehende Retrieval-Pipeline (EvidenceBuilderAgent,
        VerdictAgent, format_for_verdict) ohne Schema-Änderungen.

        Mapping:
            authority_score ≥ 0.90 → domain_tier = 1
            authority_score ≥ 0.75 → domain_tier = 2
            authority_score ≥ 0.55 → domain_tier = 3
            authority_score ≥ 0.35 → domain_tier = 4
            else                   → domain_tier = 5

            claim_relevance ≥ 0.60 → EvidenceType.DIRECT
            sonst                  → EvidenceType.CONTEXTUAL

        Der excerpt wird aus dem ersten NormalizedFact-Snippet (≤ 800 Zeichen)
        oder dem abstract befüllt.
        """
        # Import innerhalb der Methode vermeidet Zirkelimport
        from models.evidence_models import (
            EvidenceItem,
            EvidenceSource,
            EvidenceType,
            SourceDirection,
        )

        if self.authority_score >= 0.90:
            tier = 1
        elif self.authority_score >= 0.75:
            tier = 2
        elif self.authority_score >= 0.55:
            tier = 3
        elif self.authority_score >= 0.35:
            tier = 4
        else:
            tier = 5

        snippets = [f.source_snippet for f in self.normalized_facts if f.source_snippet]
        excerpt = (snippets[0] if snippets else self.abstract)[:800]

        # Policy-Durchsetzung: Excerpt gemäß display_policy begrenzen
        if self.display_policy == AllowedDisplay.METADATA_ONLY:
            excerpt = ""
        else:
            max_len = 400 if self.display_policy == AllowedDisplay.EXCERPT else 800
            excerpt = excerpt[:max_len]

        return EvidenceItem(
            source=EvidenceSource(
                url=self.url,
                title=self.title,
                domain=self.source_id,
                domain_tier=tier,
                publication_date=self.published_at.isoformat() if self.published_at else "",
                is_primary_source=tier <= 2,
                is_fact_check_org=False,
            ),
            excerpt=excerpt,
            relevance_score=self.claim_relevance,
            extraction_confidence=self.confidence,
            source_direction=SourceDirection.NEUTRAL,
            evidence_type=(
                EvidenceType.DIRECT
                if self.claim_relevance >= 0.60
                else EvidenceType.CONTEXTUAL
            ),
            claim_scope_score=self.claim_relevance,
        )


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────


def compute_recency_score(
    published_at: Optional[date],
    half_life_years: float = 3.0,
) -> float:
    """Berechne exponentiellen Aktualitätsscore basierend auf Veröffentlichungsdatum.

    Verwendet ein exponentielles Zerfall-Modell:
        score = 0.5 ^ (age_years / half_life_years)

    Empfohlene Halbwertszeiten je Domäne::

        Statistiken (World Bank, Eurostat):  half_life_years=2.0
        Unternehmensregister (GLEIF, CH):    half_life_years=1.5
        Klinische Studien:                   half_life_years=5.0
        Pharmadaten (FDA, DailyMed):         half_life_years=3.0
        Wissenschaftliche Literatur:         half_life_years=7.0
        Rechtsdokumente (EUR-Lex):           half_life_years=10.0
        Preprints (arXiv):                   half_life_years=2.0

    Args:
        published_at:     Veröffentlichungsdatum. None → 0.0 (unbekannt = veraltet).
        half_life_years:  Halbwertszeit in Jahren. Standard: 3.0.

    Returns:
        Score in [0.0, 1.0]. 1.0 = heute veröffentlicht.
    """
    if published_at is None:
        return 0.0
    today = datetime.now(timezone.utc).date()
    age_days = (today - published_at).days
    if age_days < 0:
        return 1.0
    age_years = age_days / 365.25
    return round(0.5 ** (age_years / half_life_years), 4)


def items_from_source(
    items: list[OfficialEvidenceItem],
    source_id: str,
) -> list[OfficialEvidenceItem]:
    """Filtert Items nach source_id. Hilfsfunktion für Adapter-Tests."""
    return [i for i in items if i.source_id == source_id]
