"""ClinicalTrials.gov – Source-Adapter für klinische Studien.

API-Dokumentation: https://clinicaltrials.gov/data-api/api

Endpunkte (API v2, stabil seit 2023):
    Studien-Suche:   GET https://clinicaltrials.gov/api/v2/studies
                         ?query.term={query}&pageSize={n}&pageToken={token}
    Studien-Detail:  GET https://clinicaltrials.gov/api/v2/studies/{nct_id}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (US Public Domain).
    - Pagination: cursor-basiert über nextPageToken (kein offset/page).
    - Maximale pageSize: 1000 (Standard: 10).
    - NCT-ID-Format: "NCT" + 8 Ziffern, z.B. "NCT04280705".

record_id-Format (gemäß models/source_evidence.py):
    NCT-ID: "NCT04280705"

Datenstruktur:
    Jede Studie ist ein geschachteltes Objekt unter protocolSection:
        identificationModule  → NCT-ID, Titel
        statusModule          → Status, Daten
        sponsorCollaboratorsModule → Sponsor
        descriptionModule     → Summary
        conditionsModule      → Indikationen
        armsInterventionsModule → Interventionen
        outcomesModule        → Endpunkte
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from models.source_evidence import (
    FactType,
    NormalizedFact,
    OfficialEvidenceItem,
    compute_recency_score,
)
from tools.sources.clients.base import AdapterHTTPClient, AdapterHTTPError, BaseSourceAdapter
from tools.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

_CT_BASE = "https://clinicaltrials.gov/api/v2"

# Halbwertszeit für klinische Studien: länger als Statistiken, kürzer als Recht.
_HALF_LIFE_YEARS = 5.0

# Mapping: ClinicalTrials-Status → lesbarer String
_STATUS_MAP = {
    "COMPLETED": "Abgeschlossen",
    "RECRUITING": "Rekrutierend",
    "NOT_YET_RECRUITING": "Noch nicht gestartet",
    "ACTIVE_NOT_RECRUITING": "Aktiv (keine Rekrutierung)",
    "TERMINATED": "Vorzeitig beendet",
    "SUSPENDED": "Ausgesetzt",
    "WITHDRAWN": "Zurückgezogen",
    "ENROLLING_BY_INVITATION": "Einschreibung auf Einladung",
    "AVAILABLE": "Verfügbar (Expanded Access)",
    "UNKNOWN": "Unbekannt",
}


class ClinicalTrialsClient(BaseSourceAdapter):
    """Adapter für die ClinicalTrials.gov API v2 (klinische Studien, US Public Domain).

    Liefert normalisierte Studien-Items für den EvidenceBuilderAgent.
    Besonders geeignet für medizinische und pharmazeutische Fakten-Checks,
    z.B. zur Überprüfung von Aussagen über klinische Studienendpunkte,
    Zulassungsindikationen und Studienergebnisse.

    Verwendung::

        client = ClinicalTrialsClient()
        items = client.search("semaglutide obesity weight reduction", max_results=5)
        detail = client.fetch_details("NCT04788511")
    """

    config = SourceRegistry.get("clinicaltrials")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            _CT_BASE,
            timeout=20.0,  # ClinicalTrials.gov kann etwas langsam sein
            max_attempts=3,
        )

    # ── Pflichtmethoden ───────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Suche klinische Studien per Keyword.

        Nutzt query.term für Volltext-Suche über NCT-ID, Titel, Interventionen,
        Indikationen und Abstract.

        Pagination: ClinicalTrials.gov v2 ist cursor-basiert. Bei page > 1 wird
        page × max_results als pageSize und ein einfacher offset-Ansatz genutzt
        (API unterstützt pageToken-basiertes Cursor-Paging; für einfache
        Nutzung hier pageToken ignoriert).

        Bei API-Fehlern wird eine leere Liste zurückgegeben (graceful degradation).
        """
        params: dict[str, Any] = {
            "query.term": query,
            "pageSize": min(max_results, 100),
            "format": "json",
            # Nur benötigte Felder abrufen (spart Bandbreite).
            "fields": (
                "NCTId,BriefTitle,OfficialTitle,OverallStatus,"
                "StartDate,PrimaryCompletionDate,BriefSummary,"
                "Condition,InterventionName,InterventionType,"
                "LeadSponsorName,PrimaryOutcomeMeasure,"
                "StudyType,Phase,EnrollmentCount"
            ),
        }

        try:
            raw = self._http.get("/studies", params)
        except AdapterHTTPError as exc:
            logger.warning("ClinicalTrials Suche fehlgeschlagen: %s", exc)
            return []

        studies = raw.get("studies", [])
        items = []
        for study in studies[:max_results]:
            try:
                item = self.normalize(study)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("ClinicalTrials normalize() fehlgeschlagen: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe eine einzelne Studie per NCT-ID ab.

        Args:
            record_id: NCT-ID im Format "NCT########" (z.B. "NCT04280705").

        Returns:
            OfficialEvidenceItem oder None wenn nicht gefunden.
        """
        nct_id = record_id.strip().upper()
        if not nct_id.startswith("NCT"):
            logger.warning("Ungültige ClinicalTrials NCT-ID: %r", record_id)
            return None

        try:
            raw = self._http.get(f"/studies/{nct_id}")
        except AdapterHTTPError as exc:
            logger.warning("ClinicalTrials fetch_details fehlgeschlagen für %r: %s", record_id, exc)
            return None

        if not raw:
            return None

        item = self.normalize(raw)
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere ein ClinicalTrials.gov v2 Study-Objekt in ein OfficialEvidenceItem.

        Verarbeitet sowohl das verschachtelte protocolSection-Format (v2 API)
        als auch das flache Feldformat (wenn fields-Parameter genutzt wird).
        """
        # ClinicalTrials v2 hat zwei mögliche Strukturen:
        # 1. Vollständig: record["protocolSection"]["identificationModule"]["nctId"]
        # 2. Flach (fields-Parameter): record["protocolSection"] direkt
        proto = record.get("protocolSection", record)

        # ── Identifikation ────────────────────────────────────────────────────
        id_mod = proto.get("identificationModule", {})
        nct_id: str = id_mod.get("nctId") or proto.get("NCTId", "")
        brief_title: str = id_mod.get("briefTitle") or proto.get("BriefTitle", "")
        official_title: str = id_mod.get("officialTitle") or proto.get("OfficialTitle", brief_title)

        # ── Status & Daten ────────────────────────────────────────────────────
        status_mod = proto.get("statusModule", {})
        raw_status: str = status_mod.get("overallStatus") or proto.get("OverallStatus", "UNKNOWN")
        status_label = _STATUS_MAP.get(raw_status, raw_status)

        start_date_struct = status_mod.get("startDateStruct") or {}
        start_date_str: str = start_date_struct.get("date") or proto.get("StartDate", "")
        comp_date_struct = status_mod.get("primaryCompletionDateStruct") or {}
        comp_date_str: str = comp_date_struct.get("date") or proto.get("PrimaryCompletionDate", "")

        published_at = _parse_ct_date(comp_date_str) or _parse_ct_date(start_date_str)

        # ── Sponsor ───────────────────────────────────────────────────────────
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        lead_sponsor = sponsor_mod.get("leadSponsor", {})
        sponsor_name: str = lead_sponsor.get("name") or proto.get("LeadSponsorName", "")

        # ── Beschreibung ──────────────────────────────────────────────────────
        desc_mod = proto.get("descriptionModule", {})
        brief_summary: str = (
            desc_mod.get("briefSummary") or proto.get("BriefSummary", "")
        ).strip()

        # ── Indikationen ──────────────────────────────────────────────────────
        cond_mod = proto.get("conditionsModule", {})
        conditions: list[str] = cond_mod.get("conditions") or proto.get("Condition", [])
        if isinstance(conditions, str):
            conditions = [conditions]

        # ── Interventionen ────────────────────────────────────────────────────
        arms_mod = proto.get("armsInterventionsModule", {})
        interventions_raw = arms_mod.get("interventions") or []
        intervention_names: list[str] = [
            iv.get("name", "") for iv in interventions_raw if iv.get("name")
        ]
        if not intervention_names:
            # Fallback auf Flachformat
            flat_interventions = proto.get("InterventionName", [])
            if isinstance(flat_interventions, str):
                flat_interventions = [flat_interventions]
            intervention_names = flat_interventions

        # ── Primäre Endpunkte ─────────────────────────────────────────────────
        outcomes_mod = proto.get("outcomesModule", {})
        primary_outcomes = outcomes_mod.get("primaryOutcomes") or []
        primary_measures: list[str] = [
            o.get("measure", "") for o in primary_outcomes[:3] if o.get("measure")
        ]
        if not primary_measures:
            flat_pm = proto.get("PrimaryOutcomeMeasure", [])
            if isinstance(flat_pm, str):
                flat_pm = [flat_pm]
            primary_measures = flat_pm[:3]

        # ── Design / Phase ────────────────────────────────────────────────────
        design_mod = proto.get("designModule", {})
        study_type: str = design_mod.get("studyType") or proto.get("StudyType", "")
        phases: list[str] = design_mod.get("phases") or []
        phase_str = ", ".join(phases) if phases else proto.get("Phase", "")
        enrollment = (
            design_mod.get("enrollmentInfo", {}).get("count")
            or proto.get("EnrollmentCount")
        )

        # ── URLs / Title / Abstract ───────────────────────────────────────────
        url = f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else ""
        title = brief_title or official_title or nct_id

        abstract_parts = [brief_summary] if brief_summary else []
        if conditions:
            abstract_parts.append(f"Indikationen: {', '.join(conditions[:3])}")
        if intervention_names:
            abstract_parts.append(f"Interventionen: {', '.join(intervention_names[:3])}")
        if primary_measures:
            abstract_parts.append(f"Primäre Endpunkte: {', '.join(primary_measures[:2])}")
        if sponsor_name:
            abstract_parts.append(f"Sponsor: {sponsor_name}")
        abstract_parts.append(f"Status: {status_label}")
        abstract = " | ".join(abstract_parts)[:1200]

        # ── entity_mentions ───────────────────────────────────────────────────
        entity_mentions = list(filter(None, [
            *conditions[:3],
            *intervention_names[:3],
            sponsor_name,
        ]))

        # ── NormalizedFacts ───────────────────────────────────────────────────
        facts: list[NormalizedFact] = []

        # Studie-Status als Fakt
        facts.append(
            NormalizedFact(
                fact_type=FactType.TRIAL_STATUS,
                subject=nct_id,
                predicate="Studienstatus",
                value=status_label,
                reference_period=comp_date_str or start_date_str,
                qualifier=f"{study_type}, {phase_str}".strip(", ") if study_type or phase_str else "",
                source_snippet=f"{nct_id}: {title} – Status: {status_label}."[:400],
                confidence=1.0,
            )
        )

        # Enrollmentanzahl als Fakt (wenn vorhanden)
        if enrollment is not None:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.CLINICAL_OUTCOME,
                    subject=nct_id,
                    predicate="Enrollmentanzahl (geplant/tatsächlich)",
                    value=str(enrollment),
                    numeric_value=float(enrollment),
                    unit="Teilnehmer",
                    reference_period=start_date_str,
                    qualifier=f"Sponsor: {sponsor_name}" if sponsor_name else "",
                    source_snippet=f"{nct_id}: n={enrollment} Teilnehmer, {phase_str}."[:400],
                    confidence=0.95,
                )
            )

        # Primäre Endpunkte als Fakten
        for measure in primary_measures[:2]:
            if measure:
                facts.append(
                    NormalizedFact(
                        fact_type=FactType.CLINICAL_OUTCOME,
                        subject=nct_id,
                        predicate="Primärer Endpunkt",
                        value=measure,
                        qualifier=", ".join(intervention_names[:2]),
                        source_snippet=f"Primärer Endpunkt: {measure}."[:400],
                        confidence=0.9,
                    )
                )

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=nct_id,
            title=title,
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction="US",  # ClinicalTrials.gov ist US-FDA-Registry
            entity_mentions=entity_mentions,
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "nct_id": nct_id,
                "status": raw_status,
                "start_date": start_date_str,
                "completion_date": comp_date_str,
                "sponsor": sponsor_name,
                "conditions": conditions[:5],
                "interventions": intervention_names[:5],
                "phase": phase_str,
                "enrollment": enrollment,
            },
        )
        return item


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _parse_ct_date(date_str: str) -> date | None:
    """Parse ClinicalTrials.gov Datumsformat zu date-Objekt.

    ClinicalTrials liefert Daten in verschiedenen Formaten:
        "2023-06-15"  → vollständiges Datum
        "2023-06"     → Monat (Tag = 1 als Proxy)
        "2023"        → Jahr (Dezember als Proxy)
        ""            → None

    Args:
        date_str: Datumsstring aus der ClinicalTrials API.

    Returns:
        date-Objekt oder None wenn nicht parsebar.
    """
    if not date_str:
        return None
    parts = date_str.split("-")
    try:
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        elif len(parts) == 2:
            return date(int(parts[0]), int(parts[1]), 1)
        elif len(parts) == 1 and parts[0].isdigit():
            return date(int(parts[0]), 12, 31)
    except (ValueError, IndexError):
        pass
    return None
