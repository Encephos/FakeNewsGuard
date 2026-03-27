"""Query Expansion Engine – generates diverse, domain-aware queries for evidence retrieval.

Combines ClaimRouter routing results with SearchProfile fields to create 6–8 diverse
query variants per claim. Each variant includes metadata for provider-specific routing:
- LangSearch: priority-ranked for semantic search
- SearXNG: engine/category hints for strategic routing
- Source clients: best-fit query selection per domain

This replaces the fixed 3–4 query generation with adaptive, domain-aware expansion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents.fact_checker import _bind_number_to_context, _count_strong_anchors
from tools.claim_router import ClaimRouter, RouteResult

if TYPE_CHECKING:
    from models.schemas import ClaimSearchProfile, ProcessedClaim


@dataclass
class QueryVariant:
    """Single query variant with metadata for provider routing.

    Attributes:
        text: The actual query string
        family: Query family type (entity_policy, official_source, factcheck, etc.)
        priority: Priority for semantic engines [0.0–1.0] (1.0 = highest)
        engine_hint: SearXNG engine suggestion ("brave", "duckduckgo", None)
        category_hint: SearXNG category suggestion ("news", "science", "social", etc.)
        time_range_hint: SearXNG time range ("day", "week", "month", None)
        anchors: Strong anchors present (institution, location, policy, etc.)
    """

    text: str
    family: str
    priority: float
    engine_hint: str | None = None
    category_hint: str | None = None
    time_range_hint: str | None = None
    anchors: set[str] | None = None

    def __post_init__(self):
        if self.anchors is None:
            self.anchors = set()


class QueryExpansionEngine:
    """Generates 6–8 diverse queries from ClaimRouter routing + SearchProfile.

    Leverages:
    - Domain detection (STATISTICAL, REGULATORY, PHARMACEUTICAL, etc.)
    - Jurisdiction detection (EU, UK, US, DE, global)
    - SearchProfile fields (core_entities, policy_terms, locations, numbers, sanctions)
    - Official source hints from ClaimRouter

    Reuses existing patterns:
    - _bind_number_to_context() to prevent isolated numbers
    - _count_strong_anchors() to validate query quality
    - Domain keywords from claim_router

    Query Families:
    1. entity_policy: Core claim keywords
    2. official_source: Site:-hints from official sources
    3. factcheck: Debunk/verification queries
    4. sanction: Enforcement/regulatory focus
    5. domain_specific: Domain-tailored queries
    6. jurisdiction_specific: Jurisdiction-aware variants
    """

    def expand(
        self,
        claim: ProcessedClaim,
        route_result: RouteResult,
        search_profile: ClaimSearchProfile,
    ) -> list[QueryVariant]:
        """Generate diverse queries from claim routing + search profile.

        Args:
            claim: The processed claim with text, frame, and context
            route_result: Output from ClaimRouter.route() with domains/jurisdiction
            search_profile: SearchProfile with structured fields (entities, policies, etc.)

        Returns:
            List of QueryVariant objects, sorted by priority (highest first)
        """
        variants: list[QueryVariant] = []

        # Family 1: Core claim + policy terms
        variants.extend(self._entity_policy_family(claim, search_profile))

        # Family 2: Official source + site-hints from routing
        variants.extend(self._official_source_family(claim, route_result, search_profile))

        # Family 3: Fact-check organizations
        variants.extend(self._factcheck_family(claim, search_profile))

        # Family 4: Sanction/enforcement (for regulatory claims)
        if search_profile.sanction_terms:
            variants.extend(self._sanction_family(claim, search_profile))

        # Family 5: Domain-specific queries
        variants.extend(self._domain_specific_family(claim, route_result, search_profile))

        # Family 6: Jurisdiction-specific (EU, UK, US, DE)
        if route_result.jurisdiction != "global":
            variants.extend(
                self._jurisdiction_specific_family(claim, route_result, search_profile)
            )

        # Sort by priority and remove duplicates
        unique_variants = {}
        for v in sorted(variants, key=lambda x: x.priority, reverse=True):
            if v.text not in unique_variants:
                unique_variants[v.text] = v

        return list(unique_variants.values())

    # ── Query Family Implementations ──────────────────────────────────────────────

    def _entity_policy_family(
        self, claim: ProcessedClaim, profile: ClaimSearchProfile
    ) -> list[QueryVariant]:
        """Entity + policy terms – core claim structure.

        Generates:
        1. Location + core_entities + policy_terms
        2. Location + institutions + policy_terms
        3. Location + core_entities (without policy if too generic)
        """
        variants = []

        location = " ".join(profile.locations) if profile.locations else ""
        entities = " ".join(profile.core_entities[:3]) if profile.core_entities else ""
        institutions = " ".join(profile.institutions) if profile.institutions else ""
        policies = " ".join(profile.policy_terms) if profile.policy_terms else ""

        # Query 1: Location + entities + policy (highest priority)
        if location and entities and policies:
            query_text = f"{location} {entities} {policies}".strip()
            if _count_strong_anchors([location, entities, policies], profile) >= 2:
                variants.append(
                    QueryVariant(
                        text=query_text,
                        family="entity_policy",
                        priority=1.0,
                        category_hint="news",
                        anchors={"location", "entity", "policy"},
                    )
                )

        # Query 2: Institution + policy (regulatory focus)
        if institutions and policies:
            query_text = f"{institutions} {policies}".strip()
            if _count_strong_anchors([institutions, policies], profile) >= 1:
                variants.append(
                    QueryVariant(
                        text=query_text,
                        family="entity_policy",
                        priority=0.95,
                        category_hint="news",
                        anchors={"institution", "policy"},
                    )
                )

        # Query 3: Location + core_entities (fallback if policy_terms empty)
        if location and entities and not policies:
            query_text = f"{location} {entities}".strip()
            variants.append(
                QueryVariant(
                    text=query_text,
                    family="entity_policy",
                    priority=0.85,
                    category_hint="general",
                    anchors={"location", "entity"},
                )
            )

        return variants

    def _official_source_family(
        self,
        claim: ProcessedClaim,
        route_result: RouteResult,
        profile: ClaimSearchProfile,
    ) -> list[QueryVariant]:
        """Official source queries via site:-hints from ClaimRouter routing.

        Generates per-source queries:
        - site:eurostat.ec.europa.eu [claim keywords]
        - site:eur-lex.europa.eu [policy terms]
        - etc. (based on detected domain/jurisdiction)
        """
        variants = []

        # Use site-hints from routing, supplemented by official_source_hints in profile
        site_hints = route_result.site_hints + profile.official_source_hints
        if not site_hints:
            return variants

        # Core keywords for site-based searches
        keywords = []
        if profile.core_entities:
            keywords.extend(profile.core_entities[:2])
        if profile.policy_terms:
            keywords.extend(profile.policy_terms[:1])
        if profile.locations:
            keywords.extend(profile.locations[:1])

        keyword_str = " ".join(keywords)

        # Generate one query per site-hint (highest priority)
        for hint in site_hints[:3]:  # Max 3 site-specific queries
            query_text = f"{hint} {keyword_str}".strip()
            variants.append(
                QueryVariant(
                    text=query_text,
                    family="official_source",
                    priority=0.98,
                    engine_hint=None,  # Web engines for official sources
                    category_hint="general",
                    anchors={"official_source"},
                )
            )

        return variants

    def _factcheck_family(
        self, claim: ProcessedClaim, profile: ClaimSearchProfile
    ) -> list[QueryVariant]:
        """Fact-check organization queries – debunk/verification focus.

        Generates:
        1. [claim keywords] + "Faktencheck" / "Falschmeldung"
        2. site:correctiv.org [keywords]
        3. site:snopes.com [keywords] (for global claims)
        """
        variants = []

        # Core keywords
        keywords = []
        if profile.core_entities:
            keywords.extend(profile.core_entities[:2])
        if profile.policy_terms:
            keywords.extend(profile.policy_terms[:1])

        keyword_str = " ".join(keywords) if keywords else claim.text[:50]

        # Query 1: Keywords + "Faktencheck"
        query1 = f'{keyword_str} Faktencheck'
        variants.append(
            QueryVariant(
                text=query1,
                family="factcheck",
                priority=0.92,
                engine_hint="duckduckgo",
                category_hint="news",
                anchors={"factcheck"},
            )
        )

        # Query 2: Keywords + "Falschmeldung"
        query2 = f'{keyword_str} Falschmeldung'
        variants.append(
            QueryVariant(
                text=query2,
                family="factcheck",
                priority=0.90,
                engine_hint="brave",
                category_hint="news",
                anchors={"factcheck"},
            )
        )

        # Query 3: site:correctiv.org
        if len(keywords) > 0:
            query3 = f'site:correctiv.org {keyword_str}'
            variants.append(
                QueryVariant(
                    text=query3,
                    family="factcheck",
                    priority=0.88,
                    category_hint="news",
                    anchors={"factcheck", "official_source"},
                )
            )

        return variants

    def _sanction_family(
        self, claim: ProcessedClaim, profile: ClaimSearchProfile
    ) -> list[QueryVariant]:
        """Sanction/enforcement queries – regulatory focus.

        Generates:
        - Entity + sanction_term + number (e.g., "Hannover 250 Euro Bußgeld")
        """
        variants = []

        if not profile.sanction_terms:
            return variants

        location = profile.locations[0] if profile.locations else ""
        entity = profile.core_entities[0] if profile.core_entities else location
        sanction = " ".join(profile.sanction_terms[:1])
        number = " ".join(profile.number_terms[:1]) if profile.number_terms else ""

        if entity and sanction:
            # Bind number to sanction for context
            if number:
                query_text = f"{entity} {number} {sanction}".strip()
            else:
                query_text = f"{entity} {sanction}".strip()

            variants.append(
                QueryVariant(
                    text=query_text,
                    family="sanction",
                    priority=0.88,
                    category_hint="news",
                    time_range_hint="month",
                    anchors={"entity", "sanction", "number"},
                )
            )

        return variants

    def _domain_specific_family(
        self,
        claim: ProcessedClaim,
        route_result: RouteResult,
        profile: ClaimSearchProfile,
    ) -> list[QueryVariant]:
        """Domain-specific queries tailored to detected claim domains.

        STATISTICAL: datapoint + location + time
        REGULATORY: law/ordinance + location
        PHARMACEUTICAL: drug name + FDA/dosage
        PATENT: invention + USPTO
        SCIENTIFIC: topic + research/study
        LEGAL: statute + jurisdiction
        """
        variants = []

        if not route_result.domains:
            return variants

        location = " ".join(profile.locations[:1]) if profile.locations else ""
        entity = " ".join(profile.core_entities[:1]) if profile.core_entities else ""

        for domain in route_result.domains[:2]:  # Top 2 domains
            domain_value = domain.value if hasattr(domain, "value") else str(domain)

            if domain_value == "STATISTICAL":
                # Statistical query: location + "Statistik" + datapoint
                if location:
                    query = f"{location} Statistik {entity}".strip()
                    variants.append(
                        QueryVariant(
                            text=query,
                            family="domain_specific",
                            priority=0.85,
                            category_hint="science",
                            anchors={"location", "statistical"},
                        )
                    )

            elif domain_value == "REGULATORY":
                # Regulatory query: location + "Verordnung" / "Regelwerk"
                if location:
                    query = f'{location} Verordnung {" ".join(profile.policy_terms[:1]) if profile.policy_terms else ""}'.strip()
                    variants.append(
                        QueryVariant(
                            text=query,
                            family="domain_specific",
                            priority=0.84,
                            category_hint="news",
                            anchors={"location", "regulatory"},
                        )
                    )

            elif domain_value == "PHARMACEUTICAL":
                # Pharmaceutical: drug + FDA/EMA + side effect
                if entity:
                    query = f"{entity} FDA Nebenwirkung".strip()
                    variants.append(
                        QueryVariant(
                            text=query,
                            family="domain_specific",
                            priority=0.83,
                            category_hint="science",
                            anchors={"entity", "pharmaceutical"},
                        )
                    )

            elif domain_value == "PATENT":
                # Patent: invention + USPTO
                if entity:
                    query = f"{entity} USPTO Patent".strip()
                    variants.append(
                        QueryVariant(
                            text=query,
                            family="domain_specific",
                            priority=0.82,
                            category_hint="science",
                            anchors={"entity", "patent"},
                        )
                    )

        return variants

    def _jurisdiction_specific_family(
        self,
        claim: ProcessedClaim,
        route_result: RouteResult,
        profile: ClaimSearchProfile,
    ) -> list[QueryVariant]:
        """Jurisdiction-specific queries – EU, UK, US, DE boosters.

        EU: Eurozone indicators, European Commission
        UK: Companies House, HMRC
        US: Federal agencies (FDA, SEC)
        DE: German state/municipal keywords
        """
        variants = []

        jurisdiction = route_result.jurisdiction
        location = " ".join(profile.locations[:1]) if profile.locations else ""
        entity = " ".join(profile.core_entities[:1]) if profile.core_entities else ""

        if jurisdiction == "eu":
            # EU-specific: European Commission, Eurozone
            if location:
                query = f"{location} Europäische Kommission {entity}".strip()
                variants.append(
                    QueryVariant(
                        text=query,
                        family="jurisdiction_specific",
                        priority=0.80,
                        anchors={"jurisdiction", "location"},
                    )
                )

        elif jurisdiction == "uk":
            # UK-specific: Companies House, HMRC
            if entity:
                query = f'{entity} "Companies House"'
                variants.append(
                    QueryVariant(
                        text=query,
                        family="jurisdiction_specific",
                        priority=0.80,
                        anchors={"jurisdiction", "entity"},
                    )
                )

        elif jurisdiction == "us":
            # US-specific: Federal agencies
            if entity:
                query = f"{entity} FDA SEC FTC".strip()
                variants.append(
                    QueryVariant(
                        text=query,
                        family="jurisdiction_specific",
                        priority=0.80,
                        anchors={"jurisdiction", "entity"},
                    )
                )

        elif jurisdiction == "de":
            # German-specific: state/municipal context
            if location:
                query = f"{location} Bundesregierung Bundestag".strip()
                variants.append(
                    QueryVariant(
                        text=query,
                        family="jurisdiction_specific",
                        priority=0.80,
                        anchors={"jurisdiction", "location"},
                    )
                )

        return variants
