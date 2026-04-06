"""Claim Router – regelbasierte Source-Selektion für institutionelle Primärquellen.

Mappt Claim-Signale (Typ, Frame, Kontext) auf eine priorisierte Liste
kommerziell sicherer, strukturierter Datenquellen aus der SourceRegistry.

Design-Prinzipien:
    - Keine NLP-Pipeline: heuristische Keyword-Muster + Frame-Felder
    - Kein neuer Agent: reine Datentransformation (Claim → RouteResult)
    - Erweiterbar: neue Domänen via _DOMAIN_KEYWORDS ergänzen
    - Jurisdiktion-aware: EU/UK/US/DE beeinflusst Quellenpriorität
    - Structured-first: offizielle strukturierte Quellen vor Web-Suche

Verwendung::

    from tools.claim_router import ClaimRouter

    router = ClaimRouter()

    # Nur Route berechnen
    result = router.route(claim)
    print(result.rationale)  # "Domänen: legal, regulatory | Jurisdiktion: eu | ..."

    # Route berechnen + SearchProfile-Hints augmentieren
    route_result, routed_claim = router.route_and_apply(claim)
    # routed_claim.search_profile.official_source_hints enthält jetzt site:-Hints
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools.data_loader import claim_routing_config
from tools.sources.registry import SourceRegistry
from tools.sources.types import ClaimDomain, SourceConfig

# Claim-Routing-Konfiguration (aus data/claim_routing.yaml)
_CRC = claim_routing_config()


def _kw_in_text(kw: str, text: str) -> bool:
    """Keyword-Match mit Wortgrenze für kurze Schlüsselwörter (≤ 4 Zeichen).

    Kurze Keywords wie 'ip', 'ag', 'sa' würden als Substring in anderen Wörtern
    (z. B. 'bip', 'lag', 'sanktion') falsch-positiv matchen.
    Regex ``\\b`` verhindert das für ≤ 4-Zeichen-Keywords.
    Längere Keywords werden per einfachem ``in``-Operator geprüft (schneller).
    """
    if len(kw) <= 4:
        return bool(re.search(r"\b" + re.escape(kw) + r"\b", text))
    return kw in text


# ── RouteResult ───────────────────────────────────────────────────────────────


@dataclass
class RouteResult:
    """Ergebnis der Claim-Routing-Entscheidung.

    Attributes:
        sources:      Priorisierte Quellliste (höchste Priorität zuerst, max. 6).
        domains:      Erkannte thematische Domänen des Claims.
        jurisdiction: Erkannte Jurisdiktion: 'eu' | 'uk' | 'us' | 'de' | 'global'.
        site_hints:   site:-Hints für SearXNG (z.B. 'site:eurostat.ec.europa.eu').
        rationale:    Menschenlesbare Begründung der Routing-Entscheidung.
        confidence:   Konfidenz der Routing-Entscheidung [0.0 – 1.0].
    """

    sources: list[SourceConfig]
    domains: list[ClaimDomain]
    jurisdiction: str
    site_hints: list[str]
    rationale: str
    confidence: float


# ── Keyword-Signale pro Domäne ────────────────────────────────────────────────
#
# Format: domain → frozenset[keyword_lower]
# Matching erfolgt auf lowercase(claim_text + alle frame_fields).

_DOMAIN_KEYWORDS: dict[ClaimDomain, frozenset[str]] = {
    ClaimDomain.ECONOMIC: frozenset({
        "bip", "gdp", "bnp", "pib",
        "bruttoinlandsprodukt", "wirtschaftswachstum", "economic growth",
        "inflation", "deflation", "rezession", "konjunktur",
        "arbeitslosigkeit", "arbeitslosenquote", "unemployment",
        "handelsbilanz", "leistungsbilanz", "trade balance", "current account",
        "außenhandel", "foreign trade",
        "exportquote", "importquote", "handelsüberschuss", "handelsdefizit",
        "wohlstand", "armut", "poverty", "armutsrate",
        "entwicklungsland", "entwicklungsindikator", "development indicator",
        "gini", "kaufkraft", "purchasing power",
        "makroökonomisch", "wirtschaftsleistung",
    }),
    ClaimDomain.STATISTICAL: frozenset({
        "statistik", "statistic", "statistisch",
        "erhebung", "census", "volkszählung",
        "bevölkerung", "population", "demographie", "demographisch",
        "sterblichkeit", "geburtenrate", "fertilitätsrate", "lebenserwartung",
        "indikator", "kennzahl", "messwert", "messzahl",
        "anteil", "rate", "quote", "verhältnis",
        "durchschnitt", "median", "mittelwert", "average",
        "survey", "umfrage", "stichprobe",
        "statistisches amt", "destatis", "eurostat",
    }),
    ClaimDomain.LEGAL: frozenset({
        "verordnung", "richtlinie", "gesetz", "gesetzbuch", "rechtsnorm",
        "grundgesetz", "verfassung", "verfassungsrecht",
        "regulation", "directive", "law", "statute", "legislation",
        "constitution", "constitutional",
        "artikel", "paragraph", "§", "abs.",
        "eu-verordnung", "eu-richtlinie",
        "treaty", "vertrag", "konvention", "übereinkommen",
        "rechtsprechung", "urteil", "beschluss", "entscheidung",
        "dsgvo", "gdpr", "ai act", "digital markets act", "dma", "dsa",
        "gesetzlich", "rechtlich", "vorschrift", "rechtsvorschrift",
        "neutralität", "neutralitätspflicht", "parteipolitisch",
    }),
    ClaimDomain.REGULATORY: frozenset({
        "regulierung", "regulatorisch", "compliance", "aufsicht",
        "sanktion", "sanction", "bußgeld", "fine", "strafe", "penalty",
        "enforcement", "durchsetzung", "vollzug",
        "zulassung", "genehmigung", "erlaubnis",
        "meldepflicht", "berichtspflicht", "reporting obligation",
        "auflagen", "behördlich", "behördenanordnung",
        "marktaufsicht", "verbot", "prohibition",
    }),
    ClaimDomain.CORPORATE: frozenset({
        "unternehmen", "firma", "gesellschaft", "konzern",
        "company", "corporation", "enterprise", "business",
        "gmbh", "ag", "ltd", "plc", "inc", "corp", "llc", "bv", "sa", "srl",
        "geschäftsführer", "vorstand", "ceo", "cfo", "coo",
        "eigentümer", "anteilseigner", "shareholder", "aktionär",
        "hauptsitz", "sitz", "niederlassung", "tochtergesellschaft", "muttergesellschaft",
        "handelsregister", "unternehmensregister", "companies house",
        "lei", "legal entity identifier", "gleif",
        "fusion", "übernahme", "merger", "acquisition", "m&a",
        "insolvenz", "konkurs", "liquidation", "bankruptcy",
    }),
    ClaimDomain.MEDICAL: frozenset({
        "krankheit", "erkrankung", "diagnose", "therapie", "behandlung",
        "disease", "illness", "diagnosis", "therapy", "treatment",
        "patient", "arzt", "krankenhaus", "klinik", "hospital",
        "symptom", "syndrom", "pathologie",
        "mortalität", "morbidität", "prävalenz", "inzidenz",
        "gesundheit", "health", "medizin", "medicine",
        "impfung", "vaccine", "vakzin", "immunisierung",
        "epidemie", "pandemie", "pandemic", "outbreak",
        "therapeutisch", "diagnostisch",
    }),
    ClaimDomain.PHARMACEUTICAL: frozenset({
        "arzneimittel", "medikament", "drug", "pharmazeutisch",
        "wirkstoff", "active ingredient", "active substance",
        "nebenwirkung", "adverse effect", "side effect", "unerwünschte wirkung",
        "dosierung", "dosage", "dose", "dosis",
        "marktzulassung", "marketing authorization", "fda-zulassung", "ema-zulassung",
        "indikation", "kontraindikation",
        "packungsbeilage", "beipackzettel", "label", "fachinformation",
        "antibiotika", "antibiotics", "impfstoff", "vaccine",
        "generikum", "generic", "biosimilar",
    }),
    ClaimDomain.CLINICAL: frozenset({
        "klinische studie", "clinical trial", "clinical study",
        "randomisiert", "randomized", "rct", "controlled trial",
        "placebo", "kontrollgruppe", "control group",
        "phase i", "phase ii", "phase iii", "phase iv",
        "nct", "eudract", "studienprotokoll",
        "primärer endpunkt", "primary endpoint", "primary outcome",
        "studienteilnehmer", "participant", "probanden",
    }),
    ClaimDomain.SCIENTIFIC: frozenset({
        "studie", "study", "forschung", "research",
        "publication", "publikation", "paper", "artikel",
        "peer-reviewed", "peer review", "begutachtet",
        "wissenschaftler", "forscher", "scientist", "researcher",
        "metaanalyse", "meta-analysis", "systematic review",
        "doi", "arxiv", "preprint",
        "experiment", "laborversuch", "laboruntersuchung",
        "forschungsergebnis", "finding", "ergebnis",
        "journal", "zeitschrift",
    }),
    ClaimDomain.PATENT: frozenset({
        "patent", "patentanmeldung", "patent application",
        "patentanspruch", "patent claim",
        "erfindung", "invention",
        "uspto", "epo", "europäisches patentamt",
        "patentinhaber", "assignee",
        "patentrecht", "geistiges eigentum", "intellectual property", "ip",
        "angemeldet", "patent erteilt", "patent granted",
        "patentnummer", "patent number",
    }),
    ClaimDomain.FINANCIAL: frozenset({
        "aktie", "stock", "share", "wertpapier",
        "börse", "stock exchange", "kapitalmarkt",
        "investition", "investment", "rendite", "return",
        "eigenkapital", "fremdkapital", "kapital",
        "umsatz", "revenue", "gewinn", "profit", "verlust", "loss",
        "bilanz", "balance sheet", "cashflow",
        "kredit", "credit", "schulden", "debt", "anleihe",
        "zinsen", "interest rate", "zinsrate",
    }),
    ClaimDomain.TRADE: frozenset({
        "export", "import", "außenhandel", "foreign trade",
        "zoll", "customs", "tariff", "zollgebühr",
        "handelsabkommen", "trade agreement", "freihandel", "free trade",
        "wto", "ceta", "ttip", "rcep",
        "lieferkette", "supply chain",
        "handelsbeschränkung", "trade restriction", "embargo", "sanktion",
    }),

    # ── Wissens- und Nachrichtendomänen (GDELT, Wikidata, Wikipedia) ──

    ClaimDomain.BIOGRAPHICAL: frozenset({
        "geboren", "gestorben", "geburtsdatum", "todesdatum",
        "born", "died", "date of birth", "date of death",
        "präsident", "kanzler", "minister", "amtsträger", "amtszeit",
        "president", "chancellor", "minister", "office holder",
        "bundeskanzler", "bundespräsident", "bundesminister",
        "ministerpräsident", "premierminister", "prime minister",
        "staatsoberhaupt", "head of state", "regierungschef",
        "abgeordneter", "senator", "gouverneur", "governor",
        "bürgermeister", "mayor", "oberbürgermeister",
        "gewählt", "elected", "ernannt", "appointed", "vereidigt", "sworn in",
        "amtsantritt", "amtsübernahme", "inauguration",
        "gründer", "founder", "ceo", "vorsitzender", "chairman",
        "ehepartner", "spouse", "staatsbürgerschaft", "citizenship",
        "biografie", "lebenslauf", "biography",
        "alter", "nationalität", "nationality",
    }),
    ClaimDomain.GEOGRAPHIC: frozenset({
        "hauptstadt", "capital", "einwohner", "einwohnerzahl",
        "population", "fläche", "area", "quadratkilometer",
        "liegt in", "located in", "befindet sich",
        "kontinent", "continent", "küste", "grenze", "border",
        "bundesland", "kanton", "province", "state",
        "koordinaten", "coordinates", "zeitzone", "timezone",
    }),
    ClaimDomain.INSTITUTIONAL: frozenset({
        "gegründet", "gründung", "gründungsjahr", "founded",
        "founded in", "established",
        "hauptsitz", "headquarter", "sitz",
        "mitglied", "member", "mitgliedschaft", "membership",
        "organisation", "organization", "institution",
        "stiftung", "foundation", "verband", "verein", "association",
        "mitarbeiterzahl", "employees", "beschäftigte",
    }),
    ClaimDomain.GENERAL: frozenset({
        "nachricht", "meldung", "bericht", "berichterstattung",
        "news", "report", "coverage", "headline",
        "laut medienberichten", "according to reports",
        "medien", "media", "presse", "press",
        "veröffentlicht", "published", "gemeldet", "reported",
    }),
}


# ── ClaimType → Default-Domänen ───────────────────────────────────────────────
#
# Gibt Domänen mit Basis-Score vor, wenn ClaimType eindeutig auf eine Domäne
# hinweist. Keyword-Matching verfeinert und überschreibt ggf.

_CLAIMTYPE_DOMAIN_DEFAULTS: dict[str, list[tuple[ClaimDomain, float]]] = {
    "STATISTICAL": [
        (ClaimDomain.STATISTICAL, 0.35),
        (ClaimDomain.ECONOMIC, 0.20),
    ],
    "CAUSAL": [
        (ClaimDomain.SCIENTIFIC, 0.25),
    ],
    "FACTUAL": [],   # Kein Default – Keyword-Matching entscheidet
    "CONTEXTUAL": [],
    "OPINION": [],
}


# ── Jurisdiktion-Signale ──────────────────────────────────────────────────────

_JURISDICTION_KEYWORDS: dict[str, frozenset[str]] = {
    "eu": frozenset({
        "eu", "europa", "europäisch", "european", "eu-weit",
        "europäische union", "european union",
        "eu-kommission", "european commission",
        "eurozone", "eu-mitgliedstaat", "schengen",
        "brüssel", "brussels", "luxembourg", "straßburg",
        "amtsblatt", "official journal", "eur-lex",
        "celex", "eu-verordnung", "eu-richtlinie",
    }),
    "uk": frozenset({
        "uk", "vereinigtes königreich", "united kingdom", "großbritannien",
        "england", "scotland", "wales", "northern ireland",
        "companies house", "hmrc", "ofcom", "fca",
        "british", "britisch", "london",
        "pfund", "gbp", "sterling",
    }),
    "us": frozenset({
        "usa", "vereinigte staaten", "united states", "american",
        "fda", "sec", "ftc", "uspto", "epa", "nih", "cdc",
        "washington", "federal register",
        "us-amerikanisch", "us-dollar", "usd",
    }),
    "de": frozenset({
        "deutschland", "germany", "deutsch", "german",
        "bundesregierung", "bundestag", "bundesrat",
        "bafin", "destatis", "bundesbank", "bka", "rki",
        "berlin", "münchen", "hamburg", "köln", "frankfurt",
        "bgb", "stgb", "hgb",
    }),
}

# Jurisdiktion-Keyword „us " (mit Leerzeichen) ist ein Sonderfall um False
# Positives wie "aus", "plus" zu vermeiden – wird gesondert behandelt.
_US_STANDALONE = frozenset({"us", "u.s.", "u.s.a."})


# ── Quellenboost nach Jurisdiktion ────────────────────────────────────────────
#
# Addiert auf authority_weight für die Prioritätssortierung.

_JURISDICTION_BOOST: dict[str, dict[str, float]] = {
    "eu": {"eurostat": 0.10, "eur_lex": 0.10},
    "uk": {"companies_house": 0.15},
    "us": {"openfda": 0.08, "uspto": 0.08},
    "de": {"eurostat": 0.05},  # Deutschland nutzt Eurostat als primäre stat. Quelle
    "global": {},
}

_MAX_SOURCES = _CRC.get("scoring", {}).get("max_sources", 6)


# ── ClaimRouter ───────────────────────────────────────────────────────────────


class ClaimRouter:
    """Regelbasierter Claim-Router für institutionelle Primärquellen.

    Kein LLM, keine schwere NLP-Pipeline – reine Heuristik basierend auf:
    - ``ClaimType``: STATISTICAL → statistische Quellen, CAUSAL → Wissenschaft
    - ``ClaimFrame``-Feldern: sanction/enforcement → REGULATORY,
      institution (FDA/EMA) → PHARMACEUTICAL, etc.
    - Keyword-Matching im Claim-Text und allen Frame-Feldern
    - Jurisdiktion (EU/UK/US/DE → Quellenpriorität via Boost)

    Die Routing-Entscheidung bevorzugt immer strukturierte offizielle Quellen
    (Tier 1–2) vor allgemeinen Webquellen. Das Ergebnis wird als priorisierte
    ``list[SourceConfig]`` zurückgegeben.

    Beispiel::

        router = ClaimRouter()

        # Nur RouteResult (für Logging, externe Verarbeitung)
        result = router.route(claim)

        # RouteResult + Claim mit augmentierten site:-Hints
        result, routed_claim = router.route_and_apply(claim)
    """

    def route(self, claim: "Claim") -> RouteResult:  # type: ignore[name-defined]  # noqa: F821
        """Berechne die optimalen Quellen für einen Claim.

        Args:
            claim: ``Claim`` oder ``ProcessedClaim`` (mit Frame + SearchProfile).

        Returns:
            ``RouteResult`` mit priorisierten Quellen, Domänen und Metadaten.
        """
        search_text = self._collect_text(claim)
        detected_domains, domain_scores = self._detect_domains(claim, search_text)
        jurisdiction = self._detect_jurisdiction(claim, search_text)
        sources = self._select_sources(detected_domains, jurisdiction, domain_scores)
        site_hints = self._build_site_hints(sources)
        site_hints = self._enrich_site_hints(site_hints, detected_domains, jurisdiction)
        confidence = self._compute_confidence(detected_domains, domain_scores)
        rationale = self._build_rationale(detected_domains, jurisdiction, sources, confidence)

        return RouteResult(
            sources=sources,
            domains=detected_domains,
            jurisdiction=jurisdiction,
            site_hints=site_hints,
            rationale=rationale,
            confidence=confidence,
        )

    def route_and_apply(
        self, claim: "Claim"  # type: ignore[name-defined]  # noqa: F821
    ) -> "tuple[RouteResult, Claim]":  # type: ignore[name-defined]  # noqa: F821
        """Berechne Route und gib Claim mit augmentierten Hints zurück.

        Gibt ein Tupel ``(RouteResult, augmented_claim)`` zurück.
        ``augmented_claim`` ist ein Klon des Originals (Pydantic ``model_copy``)
        mit erweiterten ``search_profile.official_source_hints``.
        Für einfache ``Claim``-Objekte ohne SearchProfile wird das Original
        unverändert zurückgegeben.

        Ergebnisse werden pro Claim-Text gecacht, um redundante Regex-Arbeit
        bei wiederholten Aufrufen mit demselben Claim zu vermeiden.

        Args:
            claim: Originaler ``Claim`` oder ``ProcessedClaim``.

        Returns:
            Tupel aus ``RouteResult`` und (ggf. augmentiertem) Claim.
        """
        # Route-Cache: canonical_text bevorzugt, sonst claim.text
        cache_key = (
            getattr(claim, "canonical_text", None) or getattr(claim, "text", "")
        ).strip().lower()
        if not hasattr(self, "_route_cache"):
            self._route_cache: dict[str, RouteResult] = {}
        if cache_key in self._route_cache:
            route_result = self._route_cache[cache_key]
        else:
            route_result = self.route(claim)
            self._route_cache[cache_key] = route_result
        augmented = self._apply_hints(claim, route_result)
        return route_result, augmented

    # ── Interne Methoden ──────────────────────────────────────────────────────

    def _collect_text(self, claim: object) -> str:
        """Sammle alle relevanten Textfelder für Keyword-Matching (lowercase)."""
        from models.schemas import ProcessedClaim

        parts: list[str] = []

        # Claim-Text
        text = getattr(claim, "text", "") or ""
        if text:
            parts.append(text.lower())

        # Claim-Context
        context = getattr(claim, "context", "") or ""
        if context:
            parts.append(context.lower())

        if isinstance(claim, ProcessedClaim):
            # Frame-Felder
            if claim.frame:
                f = claim.frame
                for val in [
                    f.subject, f.predicate, getattr(f, "object", ""),
                    f.institution, f.location,
                    f.policy_context, f.sanction, f.enforcement,
                ]:
                    if val:
                        parts.append(val.lower())

            # SearchProfile-Terme
            if claim.search_profile:
                sp = claim.search_profile
                for terms in [
                    sp.core_entities,
                    sp.institutions,
                    sp.locations,
                    sp.policy_terms,
                    sp.action_terms,
                ]:
                    parts.extend(t.lower() for t in terms if t)

        return " ".join(parts)

    def _detect_domains(
        self,
        claim: object,
        search_text: str,
    ) -> tuple[list[ClaimDomain], dict[ClaimDomain, float]]:
        """Erkenne Claim-Domänen anhand von Keywords, ClaimType und Frame.

        Returns:
            Tuple aus (geordneter Domänenliste, Score-Dict).
        """
        from models.schemas import ProcessedClaim

        scores: dict[ClaimDomain, float] = {}

        def add(domain: ClaimDomain, delta: float) -> None:
            scores[domain] = scores.get(domain, 0.0) + delta

        # 1. ClaimType-basierte Basis-Scores
        claim_type = getattr(claim, "type", None)
        type_str = claim_type.value if hasattr(claim_type, "value") else str(claim_type)
        for domain, base_score in _CLAIMTYPE_DOMAIN_DEFAULTS.get(type_str, []):
            add(domain, base_score)

        # 2. Frame-basierte Signale (stärker gewichtet, da strukturiert)
        if isinstance(claim, ProcessedClaim) and claim.frame:
            f = claim.frame

            if f.sanction:
                add(ClaimDomain.REGULATORY, 0.5)
            if f.enforcement:
                add(ClaimDomain.REGULATORY, 0.4)
            if f.policy_context and f.institution:
                add(ClaimDomain.REGULATORY, 0.3)
                add(ClaimDomain.LEGAL, 0.2)
            elif f.policy_context:
                add(ClaimDomain.LEGAL, 0.25)

            # Frame-Subject/Predicate-basierte Signale für politische Ämter
            _subj_pred = ((f.subject or "") + " " + (f.predicate or "")).lower()
            _POLITICAL_OFFICE = [
                "kanzler", "bundeskanzler", "präsident", "bundespräsident",
                "minister", "chancellor", "president", "prime minister",
                "premier", "governor", "senator", "mayor", "bürgermeister",
            ]
            if any(w in _subj_pred for w in _POLITICAL_OFFICE):
                add(ClaimDomain.BIOGRAPHICAL, 0.50)

            inst_lower = (f.institution or "").lower()
            if inst_lower:
                if any(w in inst_lower for w in ["eu", "europäisch", "european", "eurostat", "eur-lex"]):
                    add(ClaimDomain.LEGAL, 0.25)
                    add(ClaimDomain.REGULATORY, 0.15)
                if any(w in inst_lower for w in ["fda", "ema", "bfarm", "efsa"]):
                    add(ClaimDomain.PHARMACEUTICAL, 0.55)
                    add(ClaimDomain.REGULATORY, 0.20)
                if any(w in inst_lower for w in ["patent", "uspto", "epo"]):
                    add(ClaimDomain.PATENT, 0.65)
                if any(w in inst_lower for w in ["clinicaltrial", "ncbi", "nih", "pubmed"]):
                    add(ClaimDomain.CLINICAL, 0.50)
                    add(ClaimDomain.SCIENTIFIC, 0.25)
                if any(w in inst_lower for w in ["companies house", "gleif", "handelsregister"]):
                    add(ClaimDomain.CORPORATE, 0.65)
                if any(w in inst_lower for w in ["worldbank", "world bank", "imf", "iwf"]):
                    add(ClaimDomain.ECONOMIC, 0.50)
                    add(ClaimDomain.STATISTICAL, 0.30)

        # 3. Keyword-Matching (moderater Beitrag)
        _kw_match_score = _CRC.get("detection", {}).get("keyword_match_score", 0.15)
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            hits = sum(1 for kw in keywords if _kw_in_text(kw, search_text))
            if hits > 0:
                # Logarithmische Dämpfung: 1 Treffer → _kw_match_score, 4 Treffer → ~0.50
                score = min(0.70, hits * _kw_match_score)
                add(domain, score)

        # Nur Domänen mit Score >= Schwellenwert
        threshold = _CRC.get("detection", {}).get("domain_detection_threshold", 0.15)
        active = sorted(
            [(d, s) for d, s in scores.items() if s >= threshold],
            key=lambda x: x[1],
            reverse=True,
        )[:4]  # max. 4 Domänen

        detected = [d for d, _ in active]
        filtered_scores = {d: s for d, s in active}
        return detected, filtered_scores

    def _detect_jurisdiction(self, claim: object, search_text: str) -> str:
        """Erkenne die relevante Jurisdiktion anhand von Frame und Keywords."""
        from models.schemas import ProcessedClaim

        jur_scores: dict[str, int] = {}

        # Frame.location hat höchste Priorität
        if isinstance(claim, ProcessedClaim) and claim.frame and claim.frame.location:
            loc_lower = claim.frame.location.lower()
            for jur, keywords in _JURISDICTION_KEYWORDS.items():
                hits = sum(1 for kw in keywords if kw in loc_lower)
                if hits:
                    jur_scores[jur] = jur_scores.get(jur, 0) + hits * 3

        # Keyword-Matching im gesamten Text
        for jur, keywords in _JURISDICTION_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in search_text)
            jur_scores[jur] = jur_scores.get(jur, 0) + hits

        # Sonderfall "us" – nur als Wortgrenze matchen
        if any(f" {kw} " in f" {search_text} " for kw in _US_STANDALONE):
            jur_scores["us"] = jur_scores.get("us", 0) + 2

        if not jur_scores or max(jur_scores.values()) == 0:
            return "global"

        return max(jur_scores, key=lambda j: jur_scores[j])

    def _select_sources(
        self,
        domains: list[ClaimDomain],
        jurisdiction: str,
        domain_scores: dict[ClaimDomain, float],
    ) -> list[SourceConfig]:
        """Wähle und priorisiere Quellen aus der Registry.

        Kombiniert authority_weight mit Jurisdiktion-Boost und Domain-Score-
        Gewichtung. Filtert auf kommerziell sichere Quellen.
        """
        if not domains:
            # Kein Domain erkannt → hochrangige allgemeine Primärquellen (nur kommerziell sichere)
            return [s for s in SourceRegistry.by_domain_tier(max_tier=1)
                    if s.is_runtime_allowed()][:3]

        boost = _JURISDICTION_BOOST.get(jurisdiction, {})
        seen_ids: set[str] = set()
        candidates: list[tuple[SourceConfig, float]] = []

        for domain in domains:
            domain_weight = domain_scores.get(domain, 0.0)
            for src in SourceRegistry.by_domain_safe(domain):
                if src.source_id in seen_ids:
                    continue
                seen_ids.add(src.source_id)

                effective = (
                    src.authority_weight
                    + boost.get(src.source_id, 0.0)
                    + domain_weight * _CRC.get("scoring", {}).get("domain_score_weight", 0.08)
                )
                candidates.append((src, effective))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [src for src, _ in candidates[:_MAX_SOURCES]]

    def _build_site_hints(self, sources: list[SourceConfig]) -> list[str]:
        """Generiere ``site:``-Hints für SearXNG aus den primären Classifier-Domains."""
        hints: list[str] = []
        for src in sources:
            if src.classifier_domains:
                hint = f"site:{src.classifier_domains[0]}"
                if hint not in hints:
                    hints.append(hint)
        return hints

    @staticmethod
    def _enrich_site_hints(
        hints: list[str],
        domains: list[ClaimDomain],
        jurisdiction: str,
    ) -> list[str]:
        """Ergänze jurisdiktions- und domainspezifische Site-Hints.

        Die Source-Registry enthält nicht alle relevanten Web-Portale
        (z.B. gesetze-im-internet.de für deutsches Recht). Diese Methode
        fügt fehlende Hints basierend auf Domain + Jurisdiktion hinzu.
        """
        _EXTRA_HINTS: dict[tuple[ClaimDomain, str], list[str]] = {
            (ClaimDomain.LEGAL, "de"): [
                "site:gesetze-im-internet.de",
                "site:dejure.org",
                "site:bundestag.de",
            ],
            (ClaimDomain.LEGAL, "eu"): [
                "site:eur-lex.europa.eu",
            ],
            (ClaimDomain.REGULATORY, "de"): [
                "site:gesetze-im-internet.de",
                "site:bafin.de",
            ],
        }

        for domain in domains:
            extras = _EXTRA_HINTS.get((domain, jurisdiction), [])
            for hint in extras:
                if hint not in hints:
                    hints.append(hint)

        return hints

    def _compute_confidence(
        self,
        domains: list[ClaimDomain],
        domain_scores: dict[ClaimDomain, float],
    ) -> float:
        """Berechne Routing-Konfidenz [0.0 – 1.0]."""
        if not domains:
            return 0.10

        _conf = _CRC.get("confidence", {})
        _conf_min = _conf.get("min", 0.20)
        _conf_max = _conf.get("max", 0.95)
        _multi_boost = _conf.get("multi_domain_boost", 0.05)

        top_score = domain_scores.get(domains[0], 0.0)
        # Skaliere Score auf [_conf_min, _conf_max] (nie 1.0 – heuristische Unsicherheit)
        confidence = min(_conf_max, max(_conf_min, top_score / 1.0))

        # Mehrere konsistente Domänen → leicht mehr Konfidenz
        if len(domains) > 1 and domain_scores.get(domains[1], 0.0) > 0.30:
            confidence = min(_conf_max, confidence + _multi_boost)

        return round(confidence, 2)

    def _build_rationale(
        self,
        domains: list[ClaimDomain],
        jurisdiction: str,
        sources: list[SourceConfig],
        confidence: float,
    ) -> str:
        """Erstelle menschenlesbare Routing-Begründung."""
        if not domains:
            return f"Keine Domäne erkannt → allgemeine Tier-1-Quellen (Konfidenz: {confidence:.0%})"

        domain_str = ", ".join(d.value for d in domains)
        source_str = ", ".join(s.display_name.split("–")[0].strip() for s in sources[:3])
        if len(sources) > 3:
            source_str += f" (+{len(sources) - 3})"

        return (
            f"Domänen: {domain_str} | "
            f"Jurisdiktion: {jurisdiction} | "
            f"Quellen: {source_str} | "
            f"Konfidenz: {confidence:.0%}"
        )

    def _apply_hints(
        self,
        claim: object,
        route_result: RouteResult,
    ) -> object:
        """Gib Claim mit augmentierten official_source_hints zurück (intern)."""
        from models.schemas import ClaimSearchProfile, ProcessedClaim

        if not isinstance(claim, ProcessedClaim) or not route_result.site_hints:
            return claim

        if claim.search_profile is not None:
            existing = list(claim.search_profile.official_source_hints)
            new_hints = [h for h in route_result.site_hints if h not in existing]
            if not new_hints:
                return claim
            updated_profile = claim.search_profile.model_copy(
                update={"official_source_hints": existing + new_hints}
            )
        else:
            updated_profile = ClaimSearchProfile(
                official_source_hints=route_result.site_hints
            )

        return claim.model_copy(update={"search_profile": updated_profile})
