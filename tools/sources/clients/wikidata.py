"""Wikidata – Source-Adapter für strukturierte Entity-Verifizierung via SPARQL.

API-Dokumentation: https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service

Endpunkte:
    SPARQL:  GET https://query.wikidata.org/sparql?query={SPARQL}&format=json

API-Eigenschaften:
    - Keine Authentifizierung erforderlich.
    - User-Agent Header Pflicht (Wikimedia-Policy).
    - Rate-Limits: 60s Verarbeitungszeit pro 60s pro Client.
    - SPARQL-Queries per GET mit URL-encodiertem ``query``-Parameter.
    - Ergebnisse als ``application/sparql-results+json``.

Lizenz:
    CC0 – keine Einschränkungen, auch kommerziell.

Property-Mapping (Kern-Properties für Verifizierung):
    Personen:       P39 (Amt), P569 (Geburt), P570 (Tod), P27 (Staatsbürgerschaft)
    Organisationen: P571 (Gründung), P159 (Sitz), P169 (CEO), P112 (Gründer)
    Orte:           P17 (Land), P36 (Hauptstadt), P1082 (Einwohner)

record_id-Format:
    Wikidata QID, z.B. "Q183" (Deutschland), "Q567" (Angela Merkel).
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

# Strukturiertes Wissen ändert sich selten → lange Halbwertszeit.
_HALF_LIFE_YEARS = 5.0

# ── SPARQL-Templates ─────────────────────────────────────────────────────────

# Entity-Resolution: Finde QID anhand deutschem Label + Typfilter.
_RESOLVE_PERSON_SPARQL = """
SELECT ?item WHERE {{
  ?item wdt:P31 wd:Q5 .
  ?item rdfs:label "{label}"@de .
}} LIMIT 1
"""

_RESOLVE_ORG_SPARQL = """
SELECT ?item WHERE {{
  ?item wdt:P31/wdt:P279* wd:Q43229 .
  ?item rdfs:label "{label}"@de .
}} LIMIT 1
"""

_RESOLVE_PLACE_SPARQL = """
SELECT ?item WHERE {{
  {{ ?item wdt:P31/wdt:P279* wd:Q515 . }}
  UNION
  {{ ?item wdt:P31/wdt:P279* wd:Q6256 . }}
  ?item rdfs:label "{label}"@de .
}} LIMIT 1
"""

# Fuzzy-Suche als Fallback (nutzt MediaWiki-API via SPARQL).
_RESOLVE_FUZZY_SPARQL = """
SELECT ?item ?itemLabel WHERE {{
  SERVICE wikibase:mwapi {{
    bd:serviceParam wikibase:endpoint "www.wikidata.org" ;
                    wikibase:api "EntitySearch" ;
                    mwapi:search "{label}" ;
                    mwapi:language "de" .
    ?item wikibase:apiOutputItem mwapi:item .
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en" . }}
}} LIMIT 1
"""

# Property-Abfrage für eine bekannte Entity.
_PERSON_PROPERTIES = {
    "P39": "Amt/Position",
    "P569": "Geburtsdatum",
    "P570": "Todesdatum",
    "P27": "Staatsbürgerschaft",
    "P106": "Tätigkeit",
    "P19": "Geburtsort",
}

_ORG_PROPERTIES = {
    "P571": "Gründungsdatum",
    "P159": "Hauptsitz",
    "P169": "Geschäftsführer/CEO",
    "P112": "Gründer",
    "P17": "Land",
    "P1128": "Mitarbeiterzahl",
}

_PLACE_PROPERTIES = {
    "P17": "Land",
    "P36": "Hauptstadt",
    "P1082": "Einwohnerzahl",
    "P625": "Koordinaten",
    "P421": "Zeitzone",
}

_PROPERTIES_QUERY = """
SELECT ?prop ?propLabel ?val ?valLabel ?qual ?qualLabel WHERE {{
  VALUES ?prop {{ {prop_list} }}
  wd:{qid} ?prop ?val .
  OPTIONAL {{ ?val ?qual ?qualVal . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en" . }}
}} LIMIT 50
"""

# Einfachere Property-Abfrage ohne Qualifier.
_SIMPLE_PROPERTIES_QUERY = """
SELECT ?propId ?propLabel ?valLabel WHERE {{
  VALUES ?propId {{ {prop_uris} }}
  wd:{qid} ?propId ?statement .
  ?statement ?ps ?valLabel .
  ?propId wikibase:claim ?p .
  ?propId wikibase:statementProperty ?ps .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en" . }}
}} LIMIT 30
"""

# Noch einfacher: Direkte Property-Werte.
_DIRECT_PROPS_QUERY = """
SELECT ?propLabel ?valLabel WHERE {{
  VALUES (?prop) {{ {prop_values} }}
  wd:{qid} ?prop ?val .
  BIND(?prop AS ?propEntity)
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "de,en" .
    ?propEntity rdfs:label ?propLabel .
    ?val rdfs:label ?valLabel .
  }}
}} LIMIT 30
"""

# Pragmatischer Ansatz: Konkrete Properties mit Labels abfragen.
_ENTITY_FACTS_QUERY = """
SELECT ?property ?valueLabel WHERE {{
  {{
    VALUES (?p ?property) {{ {prop_bindings} }}
    wd:{qid} ?p ?value .
    SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en" . }}
  }}
}} LIMIT 30
"""


class WikidataClient(BaseSourceAdapter):
    """Adapter für Wikidata SPARQL (strukturierte Entity-Verifizierung).

    Funktionsweise:
        1. NER extrahiert Entitäten aus dem Claim-Text.
        2. Für jede Entität: SPARQL-Abfrage mit Label-Match → QID.
        3. Für jede aufgelöste QID: Property-Abfrage → NormalizedFacts.

    Besonders geeignet für:
        - Biografische Claims ("X ist Präsident von Y")
        - Geographische Claims ("Z ist Hauptstadt von W")
        - Institutionelle Claims ("Organisation gegründet in Jahr N")

    Verwendung::

        client = WikidataClient()
        items = client.search("Angela Merkel Bundeskanzlerin", max_results=5)
        detail = client.fetch_details("Q567")  # Angela Merkel
    """

    config = SourceRegistry.get("wikidata")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://query.wikidata.org",
            timeout=20.0,
            max_attempts=2,
            headers={"User-Agent": "FakeNewsGuard/1.0 (academic research)"},
        )

    # ── Pflichtmethoden ───────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Entity-basierte Suche in Wikidata.

        Extrahiert Entitäten aus dem Query via NER und löst sie gegen
        Wikidata auf. Für jede aufgelöste Entität werden Properties abgefragt.

        Bei Fehlern wird eine leere Liste zurückgegeben (graceful degradation).
        """
        entities = _extract_search_entities(query)
        if not entities:
            return []

        items: list[OfficialEvidenceItem] = []
        for name, entity_type in entities[:max_results]:
            try:
                qid = self._resolve_entity(name, entity_type)
                if not qid:
                    continue

                facts = self._query_properties(qid, name, entity_type)
                if not facts:
                    continue

                item = self._build_item(qid, name, facts)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("Wikidata search fehlgeschlagen für %r: %s", name, exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe alle Properties einer Wikidata-Entity per QID ab.

        Args:
            record_id: Wikidata QID, z.B. "Q183" oder "Q567".

        Returns:
            OfficialEvidenceItem oder None wenn nicht gefunden.
        """
        qid = record_id.strip().upper()
        if not qid.startswith("Q") or not qid[1:].isdigit():
            logger.warning("Ungültige Wikidata-QID: %r", record_id)
            return None

        # Label der Entity abfragen
        label = self._get_label(qid)
        if not label:
            return None

        # Alle Property-Sets kombiniert abfragen
        all_props = {**_PERSON_PROPERTIES, **_ORG_PROPERTIES, **_PLACE_PROPERTIES}
        facts = self._query_properties_by_dict(qid, label, all_props)

        if not facts:
            return None

        item = self._build_item(qid, label, facts)
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere ein SPARQL-Resultat in ein OfficialEvidenceItem.

        Nicht direkt für SPARQL genutzt – stattdessen wird ``_build_item()`` verwendet.
        Diese Methode existiert zur Interface-Kompatibilität.
        """
        qid = record.get("qid", "")
        label = record.get("label", "")
        facts = record.get("facts", [])
        return self._build_item(qid, label, facts)

    # ── Entity-Resolution ────────────────────────────────────────────────────

    def _resolve_entity(self, name: str, entity_type: str) -> str | None:
        """Löse einen Entity-Namen gegen Wikidata auf → QID.

        Versucht zunächst exakten Label-Match mit Typfilter,
        dann Fuzzy-Suche als Fallback.

        Args:
            name: Entity-Name in Deutsch, z.B. "Angela Merkel".
            entity_type: "person", "organization" oder "location".

        Returns:
            QID-String (z.B. "Q567") oder None wenn nicht gefunden.
        """
        # Exakter Label-Match mit Typfilter
        template = {
            "person": _RESOLVE_PERSON_SPARQL,
            "organization": _RESOLVE_ORG_SPARQL,
            "location": _RESOLVE_PLACE_SPARQL,
        }.get(entity_type, _RESOLVE_FUZZY_SPARQL)

        sparql = template.format(label=_escape_sparql(name))
        results = self._sparql_query(sparql)
        qid = _extract_qid(results)
        if qid:
            return qid

        # Fallback: Fuzzy-Suche
        sparql = _RESOLVE_FUZZY_SPARQL.format(label=_escape_sparql(name))
        results = self._sparql_query(sparql)
        return _extract_qid(results)

    def _get_label(self, qid: str) -> str | None:
        """Hole das deutsche Label einer Entity."""
        sparql = f"""
        SELECT ?label WHERE {{
          wd:{qid} rdfs:label ?label .
          FILTER(LANG(?label) = "de")
        }} LIMIT 1
        """
        results = self._sparql_query(sparql)
        bindings = results.get("results", {}).get("bindings", [])
        if bindings:
            return bindings[0].get("label", {}).get("value", "")
        return None

    # ── Property-Abfragen ────────────────────────────────────────────────────

    def _query_properties(
        self, qid: str, entity_name: str, entity_type: str,
    ) -> list[NormalizedFact]:
        """Frage verifizierungsrelevante Properties für eine Entity ab."""
        prop_dict = {
            "person": _PERSON_PROPERTIES,
            "organization": _ORG_PROPERTIES,
            "location": _PLACE_PROPERTIES,
        }.get(entity_type, _PERSON_PROPERTIES)

        return self._query_properties_by_dict(qid, entity_name, prop_dict)

    def _query_properties_by_dict(
        self, qid: str, entity_name: str, prop_dict: dict[str, str],
    ) -> list[NormalizedFact]:
        """Frage Properties per Wikidata-Direkt-Pfad ab.

        Nutzt wdt: (direct truthy) Properties für einfachen Zugriff.
        """
        if not prop_dict:
            return []

        # SPARQL mit direkten wdt: Properties
        prop_lines = []
        for pid, plabel in prop_dict.items():
            prop_lines.append(
                f'  OPTIONAL {{ wd:{qid} wdt:{pid} ?val_{pid} . }}'
            )

        select_vars = " ".join(f"?val_{pid}" for pid in prop_dict)
        optionals = "\n".join(prop_lines)

        sparql = f"""
        SELECT {select_vars} WHERE {{
        {optionals}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en" . }}
        }} LIMIT 1
        """

        results = self._sparql_query(sparql)
        bindings = results.get("results", {}).get("bindings", [])
        if not bindings:
            return []

        row = bindings[0]
        facts: list[NormalizedFact] = []

        for pid, plabel in prop_dict.items():
            var_name = f"val_{pid}"
            val_binding = row.get(var_name)
            if not val_binding:
                continue

            raw_value = val_binding.get("value", "")
            val_type = val_binding.get("type", "")

            # Werte aufbereiten
            display_value = _format_wikidata_value(raw_value, val_type)
            if not display_value:
                continue

            # Numerische Werte erkennen
            numeric = None
            try:
                numeric = float(raw_value)
            except (ValueError, TypeError):
                pass

            facts.append(
                NormalizedFact(
                    fact_type=FactType.ENTITY_PROPERTY,
                    subject=entity_name[:120],
                    predicate=plabel,
                    value=display_value[:200],
                    numeric_value=numeric,
                    source_snippet=f"{entity_name} → {plabel}: {display_value}"[:400],
                    confidence=0.85,
                )
            )

        return facts

    # ── SPARQL-Kommunikation ─────────────────────────────────────────────────

    def _sparql_query(self, sparql: str) -> dict:
        """Führe eine SPARQL-Query gegen den Wikidata Query Service aus.

        Args:
            sparql: SPARQL-Query-String.

        Returns:
            Geparste SPARQL-JSON-Response oder leeres Dict bei Fehler.
        """
        try:
            return self._http.get("/sparql", {"query": sparql, "format": "json"})
        except AdapterHTTPError as exc:
            logger.warning("Wikidata SPARQL fehlgeschlagen: %s", exc)
            return {}

    # ── Item-Konstruktion ────────────────────────────────────────────────────

    def _build_item(
        self, qid: str, label: str, facts: list[NormalizedFact],
    ) -> OfficialEvidenceItem:
        """Baue ein OfficialEvidenceItem aus QID, Label und Facts."""
        # Abstract aus den Facts zusammenbauen
        abstract_parts = [f"{label} (Wikidata {qid})"]
        for fact in facts[:5]:
            abstract_parts.append(f"{fact.predicate}: {fact.value}")
        abstract = " | ".join(abstract_parts)

        recency = compute_recency_score(None, half_life_years=_HALF_LIFE_YEARS)

        return OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=qid,
            title=label,
            url=f"https://www.wikidata.org/wiki/{qid}",
            abstract=abstract[:1200],
            published_at=None,
            jurisdiction="global",
            entity_mentions=[label],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={"qid": qid, "label": label},
        )


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────


def _extract_search_entities(query: str) -> list[tuple[str, str]]:
    """Extrahiere Entitäten aus dem Query-Text via NER.

    Nutzt den bestehenden NER-Extractor (tools/ner_extractor.py) und
    gibt Paare von (Name, Typ) zurück.

    Falls spaCy nicht verfügbar ist, wird ein einfacher Fallback genutzt,
    der Wörter mit Großbuchstaben als potenzielle Entitäten erkennt.
    """
    try:
        from tools.ner_extractor import extract_entities

        entities = extract_entities(query)
        result: list[tuple[str, str]] = []

        for person in entities.persons[:3]:
            result.append((person, "person"))
        for org in entities.organizations[:3]:
            result.append((org, "organization"))
        for loc in entities.locations[:2]:
            result.append((loc, "location"))

        return result
    except Exception:
        # Fallback: Titel-Case-Wörter als generische Entitäten
        logger.debug("NER nicht verfügbar, nutze Fallback für Entity-Extraktion")
        words = query.split()
        result = []
        for word in words:
            # Wörter mit Großbuchstabe am Anfang (nicht am Satzanfang)
            if len(word) > 2 and word[0].isupper() and word not in {"Der", "Die", "Das", "Ein", "Eine", "The", "A", "An"}:
                result.append((word, "person"))  # Default: person
        return result[:5]


def _extract_qid(sparql_results: dict) -> str | None:
    """Extrahiere die erste QID aus einem SPARQL-Resultat.

    Sucht in den Bindings nach einer Variable, die eine Wikidata-Entity-URI
    enthält (http://www.wikidata.org/entity/Q...) und gibt die QID zurück.
    """
    bindings = sparql_results.get("results", {}).get("bindings", [])
    if not bindings:
        return None

    for binding in bindings:
        for var_name, var_data in binding.items():
            uri = var_data.get("value", "")
            if "wikidata.org/entity/Q" in uri:
                qid = uri.rsplit("/", 1)[-1]
                if qid.startswith("Q") and qid[1:].isdigit():
                    return qid
    return None


def _escape_sparql(text: str) -> str:
    """Escape-Sonderzeichen für SPARQL-String-Literale."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _format_wikidata_value(raw_value: str, val_type: str) -> str:
    """Formatiere einen Wikidata-Wert für die Anzeige.

    Wikidata-URIs werden auf das letzte Segment gekürzt (QID/PID).
    Datumswerte werden in ein lesbares Format gebracht.
    """
    if not raw_value:
        return ""

    # Wikidata-Entity-URI → Label (QID)
    if "wikidata.org/entity/" in raw_value:
        return raw_value.rsplit("/", 1)[-1]

    # ISO-Datum (z.B. "1954-07-17T00:00:00Z") → "1954-07-17"
    if "T00:00:00Z" in raw_value and len(raw_value) >= 10:
        return raw_value[:10]

    # Koordinaten (Point(...)) → kürzen
    if raw_value.startswith("Point("):
        return raw_value

    return raw_value
