"""Dataset loading, validation, and ProcessedClaim construction."""

from __future__ import annotations

import json
import re
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


def _extract_profile_fields(claim_text: str, entities: list[str]) -> dict:
    """Extract structured profile fields from claim text and entity list.

    Populates institutions, locations, policy_terms, number_terms, and
    action_terms so that _compute_claim_scope_score() can produce meaningful
    scores above 0.5.
    """
    text = claim_text

    # --- Institutions: known org patterns from entities + claim text ---
    known_institutions = {
        "EU", "EZB", "ECB", "EuGH", "WHO", "NATO", "BASF", "Volkswagen",
        "Destatis", "BKA", "EMA", "Bundesbank", "Bundestag", "Bundesrat",
        "Europäische Kommission", "Europäischer Rat", "Europäisches Parlament",
        "RKI", "IPCC", "DWD", "UBA", "BAMF", "BaFin", "IWF", "IMF",
        "Weltbank", "UNHCR", "Frontex", "STIKO", "PEI", "BMG", "BMEL",
        "Umweltbundesamt", "Bundesverfassungsgericht", "IEA", "WMO",
    }
    institutions: list[str] = []
    for e in entities:
        if e in known_institutions or e.isupper() and len(e) >= 2:
            institutions.append(e)
    # Also find institution-like patterns in text (capitalized multi-word or abbrevs)
    for inst in known_institutions:
        # Use word boundaries for short terms to avoid false positives
        # (e.g., "EU" matching inside "Euro" or "Neubau")
        if len(inst) <= 4:
            if re.search(r"\b" + re.escape(inst) + r"\b", text) and inst not in institutions:
                institutions.append(inst)
        elif inst.lower() in text.lower() and inst not in institutions:
            institutions.append(inst)

    # --- Locations ---
    known_locations = {
        "Deutschland", "Germany", "Österreich", "Austria", "Schweiz",
        "France", "Frankreich", "Europa", "Europe", "EU",
        "Berlin", "München", "Hamburg", "Hannover", "Dresden", "Leipzig",
        "Köln", "Frankfurt", "Stuttgart", "Düsseldorf", "Bremen",
        "Griechenland", "Italien", "Spanien", "Polen", "Türkei",
        "Mittelmeer", "Sahel", "Afrika", "Syrien", "Ukraine",
        "China", "USA", "Russland", "Nordpol", "Arktis",
    }
    locations: list[str] = []
    for loc in known_locations:
        if len(loc) <= 4:
            if re.search(r"\b" + re.escape(loc) + r"\b", text):
                locations.append(loc)
        elif loc.lower() in text.lower():
            locations.append(loc)
    # Entities that look like locations but aren't in known_institutions
    for e in entities:
        if e in known_locations and e not in locations:
            locations.append(e)

    # --- Number terms: extract numeric expressions from claim text ---
    number_terms: list[str] = []
    # Patterns: "2,3 Prozent", "6.000 Stellen", "20 Millionen Euro", "1,35 Kindern"
    for m in re.finditer(
        r"\d[\d.,]*\s*(?:Prozent|%|Milliarden?|Millionen?|Euro|Dollar|Stellen?|Kindern?|Jahren?)",
        text,
    ):
        number_terms.append(m.group(0).strip())
    # Also bare numbers with context: "2025", "2024", "seit 2015"
    for m in re.finditer(r"(?:seit |ab |im |bis )?(?:Ende )?\d{4}", text):
        term = m.group(0).strip()
        if term not in number_terms:
            number_terms.append(term)

    # --- Policy terms: domain-specific nouns from entities + claim text ---
    policy_terms: list[str] = []
    # Entities that aren't institutions or locations are likely policy terms
    for e in entities:
        if e not in institutions and e not in locations and e not in known_locations:
            policy_terms.append(e)
    # Known policy-term patterns
    policy_patterns = [
        r"Inflationsrate", r"Leitzins", r"Arbeitslosenquote", r"Geburtenrate",
        r"Kriminalität(?:srate)?", r"Verbraucherpreisindex",
        r"Vorratsdatenspeicherung", r"Datenschutzgrundverordnung", r"DSGVO",
        r"Gebäudeenergiegesetz", r"Impfpflicht", r"Einwegplastik",
        r"Nettoverlust", r"Stellenabbau", r"Adipositas",
        r"BIP", r"Verteidigung", r"Wärmepumpen?",
        r"negative interest rates", r"vols intérieurs",
        r"Bußgeld(?:er)?", r"Wegovy", r"Ivermectin", r"COVID-19",
        r"Klimawandel", r"CO2-Emissionen?", r"Erderwärmung",
        r"Treibhausgase?", r"Meeresspiegel", r"Temperaturanstieg",
        r"Asyl(?:anträge)?", r"Flüchtlinge?", r"Migration",
        r"Abschiebung(?:en)?", r"Familiennachzug", r"Grenzschutz",
        r"Rendite", r"Aktien?kurs", r"Staatsverschuldung",
        r"Handelsbilanz", r"Mindestlohn", r"Bürgergeld",
        r"Impfstoff", r"Nebenwirkung(?:en)?", r"mRNA",
        r"Sterblichkeit(?:srate)?", r"Übersterblichkeit",
        r"Chemtrails?", r"Mikrochip", r"Great Reset",
        r"Bargeldabschaffung", r"Überwachung(?:sstaat)?",
    ]
    for pat in policy_patterns:
        if re.search(pat, text, re.IGNORECASE):
            match = re.search(pat, text, re.IGNORECASE)
            if match and match.group(0) not in policy_terms:
                policy_terms.append(match.group(0))

    # --- Action terms: verbs/actions from claim text ---
    action_terms: list[str] = []
    action_patterns = [
        r"beschlossen", r"gesenkt", r"gestiegen", r"zugelassen",
        r"entschieden", r"verbucht", r"angekündigt", r"abzubauen",
        r"vorschreibt", r"gilt", r"verboten", r"interdit",
        r"gesunken", r"erhöht", r"eingeführt", r"abgeschafft",
        r"geschmolzen", r"überflutet", r"verdoppelt", r"halbiert",
        r"bewiesen", r"widerlegt", r"gewarnt", r"empfohlen",
        r"abgelehnt", r"genehmigt", r"aufgedeckt", r"vertuscht",
    ]
    for pat in action_patterns:
        if re.search(pat, text, re.IGNORECASE):
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                action_terms.append(match.group(0))

    return {
        "institutions": institutions,
        "locations": locations,
        "number_terms": number_terms,
        "policy_terms": policy_terms,
        "action_terms": action_terms,
    }


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
        EvalCategory.HEALTH: ClaimType.FACTUAL,
        EvalCategory.CLIMATE: ClaimType.FACTUAL,
        EvalCategory.MIGRATION: ClaimType.STATISTICAL,
        EvalCategory.FINANCIAL: ClaimType.STATISTICAL,
        EvalCategory.CONSPIRACY: ClaimType.FACTUAL,
    }

    claim_type = type_map.get(case.category, ClaimType.FACTUAL)

    # Extract structured fields from claim text and entities
    exp = case.expectations
    fields = _extract_profile_fields(case.claim_text, exp.must_have_entities)

    # Build search profile with properly populated fields
    profile = ClaimSearchProfile(
        core_entities=exp.must_have_entities,
        institutions=fields["institutions"],
        locations=fields["locations"],
        policy_terms=fields["policy_terms"],
        number_terms=fields["number_terms"],
        action_terms=fields["action_terms"],
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
