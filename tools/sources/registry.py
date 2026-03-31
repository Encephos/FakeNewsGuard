"""Source Registry – zentrale Konfiguration aller institutionellen Datenquellen.

Definiert und verwaltet 14 kommerziell sichere Primärquellen für die
strukturierte Evidence-Retrieval-Schicht des FakeNewsGuard-Systems.

Aufnahmekriterien:
    - Öffentliche API oder zumindest maschinenlesbare Daten
    - Institutionelle oder behördliche Herkunft
    - Lizenz mit mindestens einem klar definierten Nutzungsrecht
    - Keine Pay-Wall für Metadaten / Suchanfragen

Quellen:
    World Bank        – Wirtschafts- und Entwicklungsdaten (CC BY 4.0)
    GLEIF             – Globales LEI-Register (CC BY 4.0)
    openFDA           – FDA-Regulierungsdaten (US Public Domain)
    OpenAlex          – Akademische Literaturdatenbank (CC0)
    arXiv             – Preprint-Metadaten (CC0-Metadaten, varies für Volltext)
    Crossref          – DOI-Metadaten-Registry (CC0)
    CERN Open Data    – Physik-Forschungsdaten (CC0 / CC BY)
    Eurostat          – EU-Statistiken (CC BY 4.0)
    EUR-Lex           – EU-Rechtstexte (Public Domain)
    USPTO PatentsView – US-Patentdaten (US Public Domain)
    Companies House   – UK-Unternehmensregister (OGL v3)
    ClinicalTrials    – Klinische Studien-Datenbank (US Public Domain)
    DailyMed          – FDA-Arzneimittel-Etikettierungen (US Public Domain)
    PubMed            – Biomedizinische Literaturdatenbank (Metadaten Public Domain)

Verwendung::

    from tools.sources import SourceRegistry, ClaimDomain

    all_sources = SourceRegistry.all()
    medical = SourceRegistry.by_domain(ClaimDomain.MEDICAL)
    high_auth = SourceRegistry.by_authority_weight(min_weight=0.90)
    commercial = SourceRegistry.commercial_safe()
    eu_law = SourceRegistry.get("eur_lex")
"""

from __future__ import annotations

from tools.data_loader import source_authority_weights
from tools.sources.types import (
    AllowedDisplay,
    AllowedStorage,
    AuthMode,
    ClaimDomain,
    CommercialUsePolicy,
    SourceConfig,
)

# Authority-Weights aus data/source_authority.yaml (überschreibt Hardcoded-Defaults)
_SAW = source_authority_weights()

# ── Source-Definitionen ───────────────────────────────────────────────────────

_WORLD_BANK = SourceConfig(
    source_id="world_bank",
    display_name="World Bank Open Data",
    source_class="tools.sources.clients.world_bank.WorldBankClient",
    base_url="https://api.worldbank.org/v2",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.FULL,
    fulltext_allowed=True,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.ECONOMIC,
        ClaimDomain.STATISTICAL,
        ClaimDomain.FINANCIAL,
        ClaimDomain.TRADE,
    ),
    authority_weight=_SAW.get("world_bank", 0.88),
    classifier_domains=("worldbank.org", "data.worldbank.org"),
    jurisdictions=("global",),
    rate_limit_rps=10.0,
    requires_registration=False,
    notes=(
        "CC BY 4.0. Über 16.000 Entwicklungsindikatoren. "
        "Indicator-API: /country/{iso}/indicator/{code}. "
        "Volltextabruf ist möglich, Citation-Policy beachten."
    ),
)

_GLEIF = SourceConfig(
    source_id="gleif",
    display_name="GLEIF – Global Legal Entity Identifier Foundation",
    source_class="tools.sources.clients.gleif.GLEIFClient",
    base_url="https://api.gleif.org/api/v1",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.FULL,
    fulltext_allowed=True,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.CORPORATE,
        ClaimDomain.LEGAL,
        ClaimDomain.FINANCIAL,
    ),
    authority_weight=_SAW.get("gleif", 0.92),
    classifier_domains=("gleif.org",),
    jurisdictions=("global",),
    rate_limit_rps=3.0,
    requires_registration=False,
    notes=(
        "CC BY 4.0. Einzige offizielle globale Quelle für LEI-Daten "
        "(Legal Entity Identifiers – ISO 17442). "
        "Suche über /lei-records, Detail via /lei-records/{lei}. "
        "Deckt auch übergeordnete Konzernstrukturen ab (/ultimate-parent-lei)."
    ),
)

_OPENFDA = SourceConfig(
    source_id="openfda",
    display_name="openFDA – U.S. Food and Drug Administration Open Data",
    source_class="tools.sources.clients.openfda.OpenFDAClient",
    base_url="https://api.fda.gov",
    auth_mode=AuthMode.API_KEY_OPTIONAL,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.FULL,
    fulltext_allowed=True,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.PHARMACEUTICAL,
        ClaimDomain.REGULATORY,
        ClaimDomain.MEDICAL,
    ),
    authority_weight=_SAW.get("openfda", 0.95),
    classifier_domains=("api.fda.gov", "fda.gov", "open.fda.gov"),
    jurisdictions=("us",),
    rate_limit_rps=1.0,  # 240/min mit API-Key; 40/min ohne Key → 0.67 req/s
    requires_registration=False,
    notes=(
        "US Public Domain (Regierungswerk). API-Key optional – erhöht Rate-Limit "
        "auf 240 req/min. Endpunkte: /drug/label, /drug/event (Adverse Events), "
        "/device/recall, /food/enforcement. ENV: OPENFDA_API_KEY."
    ),
)

_OPENALEX = SourceConfig(
    source_id="openalex",
    display_name="OpenAlex – Open Scholarly Infrastructure",
    source_class="tools.sources.clients.openalex.OpenAlexClient",
    base_url="https://api.openalex.org",
    auth_mode=AuthMode.EMAIL_POLITE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=False,
    claim_domains=(
        ClaimDomain.SCIENTIFIC,
        ClaimDomain.MEDICAL,
    ),
    authority_weight=_SAW.get("openalex", 0.78),
    classifier_domains=("openalex.org",),
    jurisdictions=("global",),
    rate_limit_rps=10.0,  # Polite Pool: 10 req/s; ohne Email: ~1 req/s
    requires_registration=False,
    notes=(
        "CC0. Über 250 Mio. wissenschaftliche Arbeiten. "
        "Polite Pool via mailto-Parameter (ENV: POLITE_POOL_EMAIL) → 10 req/s. "
        "Liefert Abstracts (OA-abstracts), keine Volltexte. "
        "Suche: /works?filter=title.search:{query}. "
        "fulltext_allowed=False: nur Metadaten + Abstracts als Excerpt."
    ),
)

_ARXIV = SourceConfig(
    source_id="arxiv",
    display_name="arXiv – Open-Access Preprint Server",
    source_class="tools.sources.clients.arxiv.ArXivClient",
    base_url="https://export.arxiv.org/api/v0",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.CHECK_TERMS,
    citation_required=True,
    claim_domains=(
        ClaimDomain.SCIENTIFIC,
        ClaimDomain.MEDICAL,
        ClaimDomain.STATISTICAL,
    ),
    authority_weight=_SAW.get("arxiv", 0.70),
    classifier_domains=("arxiv.org", "export.arxiv.org"),
    jurisdictions=("global",),
    rate_limit_rps=0.33,  # Offiziell: max 1 req/3s für API-Calls
    requires_registration=False,
    notes=(
        "Metadaten CC0; Volltexte unterliegen den Lizenzen der Autoren (variiert). "
        "Nur Metadaten abrufen (Titel, Autoren, Abstract, arXiv-ID). "
        "fulltext_allowed=False: Volltext-PDFs dürfen nicht programmatisch "
        "abgerufen und gecacht werden. Rate-Limit: 1 req/3s. "
        "commercial_reuse_ok=CHECK_TERMS: Abstracts sind CC0, PDFs nicht."
    ),
)

_CROSSREF = SourceConfig(
    source_id="crossref",
    display_name="Crossref – DOI Metadata Registry",
    source_class="tools.sources.clients.crossref.CrossrefClient",
    base_url="https://api.crossref.org",
    auth_mode=AuthMode.EMAIL_POLITE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=False,
    claim_domains=(
        ClaimDomain.SCIENTIFIC,
        ClaimDomain.MEDICAL,
        ClaimDomain.STATISTICAL,
    ),
    authority_weight=_SAW.get("crossref", 0.82),
    classifier_domains=("api.crossref.org", "crossref.org", "doi.org"),
    jurisdictions=("global",),
    rate_limit_rps=5.0,  # Polite Pool: bis 50 req/s; ohne Email: niedrig
    requires_registration=False,
    notes=(
        "CC0. DOI-Metadaten-Registry mit über 150 Mio. Einträgen. "
        "Polite Pool via mailto-Parameter (ENV: POLITE_POOL_EMAIL). "
        "Suche: /works?query={text}. Detailabfrage: /works/{doi}. "
        "Nur Metadaten (kein Volltext). "
        "fulltext_allowed=False: Crossref liefert Metadaten, keine Paper."
    ),
)

_CERN_OPEN_DATA = SourceConfig(
    source_id="cern_open_data",
    display_name="CERN Open Data Portal",
    source_class="tools.sources.clients.cern_open_data.CERNOpenDataClient",
    base_url="https://opendata.cern.ch/api",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.SCIENTIFIC,
    ),
    authority_weight=_SAW.get("cern_open_data", 0.87),
    classifier_domains=("opendata.cern.ch", "cern.ch"),
    jurisdictions=("global",),
    rate_limit_rps=2.0,
    requires_registration=False,
    notes=(
        "CC0 / CC BY 4.0. Physik-Forschungsdaten und -publikationen des CERN. "
        "Invenio-basiertes REST-API. Suche: /records?q={query}. "
        "Datei-Downloads nicht über API abwickeln (Größe). "
        "fulltext_allowed=False: Nur Datensatz-Metadaten und Beschreibungen."
    ),
)

_EUROSTAT = SourceConfig(
    source_id="eurostat",
    display_name="Eurostat – Statistical Office of the European Union",
    source_class="tools.sources.clients.eurostat.EurostatClient",
    base_url="https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.FULL,
    fulltext_allowed=True,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.STATISTICAL,
        ClaimDomain.ECONOMIC,
        ClaimDomain.TRADE,
        ClaimDomain.REGULATORY,
    ),
    authority_weight=_SAW.get("eurostat", 0.95),
    classifier_domains=("eurostat.ec.europa.eu", "ec.europa.eu/eurostat"),
    jurisdictions=("eu", "de"),
    rate_limit_rps=2.0,
    requires_registration=False,
    notes=(
        "CC BY 4.0. Offizielle EU-Statistikbehörde. "
        "JSON:Statistical Data and Metadata eXchange (SDMX). "
        "Datensatz-Tabellen über /data/{dataset_code}?format=JSON. "
        "Datensatz-Suche über Eurostat Data Browser (kein Such-API; "
        "Mapping via bekannte Dataset-Codes nötig). "
        "Bereits in source_classifier._TIER_PATTERNS (SourceTier.OFFICIAL)."
    ),
)

_EUR_LEX = SourceConfig(
    source_id="eur_lex",
    display_name="EUR-Lex – EU Law and Official Journal",
    source_class="tools.sources.clients.eur_lex.EURLexClient",
    base_url="https://publications.europa.eu/webapi/rdf/sparql",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.FULL,
    fulltext_allowed=True,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.LEGAL,
        ClaimDomain.REGULATORY,
    ),
    authority_weight=_SAW.get("eur_lex", 0.97),
    classifier_domains=("eur-lex.europa.eu", "publications.europa.eu"),
    jurisdictions=("eu",),
    rate_limit_rps=1.0,  # SPARQL-Endpoint ist ressourcenintensiv
    requires_registration=False,
    notes=(
        "EU Public Domain. Autoritativste Quelle für EU-Recht. "
        "Zugang via CELLAR SPARQL-Endpoint (SPARQL 1.1). "
        "Alternativ: EUR-Lex FRBR-REST-API für strukturierte Dokument-Metadaten. "
        "Volltexte abrufbar. SPARQL-Queries können komplex sein – "
        "Client sollte Templates für gängige Anfragen (Verordnung nach Nummer, "
        "Richtlinie nach Datum) bereitstellen."
    ),
)

_USPTO = SourceConfig(
    source_id="uspto",
    display_name="USPTO PatentsView – U.S. Patent and Trademark Office",
    source_class="tools.sources.clients.uspto.USPTOClient",
    base_url="https://api.patentsview.org",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.FULL,
    fulltext_allowed=True,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=False,
    claim_domains=(
        ClaimDomain.PATENT,
        ClaimDomain.LEGAL,
        ClaimDomain.SCIENTIFIC,
    ),
    authority_weight=_SAW.get("uspto", 0.93),
    classifier_domains=("patentsview.org", "patents.google.com", "usptopatents.org", "patent.gov"),
    jurisdictions=("us",),
    rate_limit_rps=10.0,
    requires_registration=False,
    notes=(
        "US Public Domain. PatentsView REST-API für US-Patente. "
        "Suche: /patents/query mit JSON-Body. "
        "Felder: patent_number, patent_title, patent_abstract, assignee_*, inventor_*. "
        "Kein API-Key nötig. Volltexte (Ansprüche) verfügbar."
    ),
)

_COMPANIES_HOUSE = SourceConfig(
    source_id="companies_house",
    display_name="Companies House – UK Company Register",
    source_class="tools.sources.clients.companies_house.CompaniesHouseClient",
    base_url="https://api.company-information.service.gov.uk",
    auth_mode=AuthMode.API_KEY,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.FULL,
    fulltext_allowed=True,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.CORPORATE,
        ClaimDomain.LEGAL,
        ClaimDomain.FINANCIAL,
    ),
    authority_weight=_SAW.get("companies_house", 0.93),
    classifier_domains=(
        "api.company-information.service.gov.uk",
        "find-and-update.company-information.service.gov.uk",
        "companieshouse.gov.uk",
    ),
    jurisdictions=("uk",),
    rate_limit_rps=2.0,  # 600 req/5 min = 2 req/s
    requires_registration=True,
    notes=(
        "Open Government Licence v3. Offizielles UK-Unternehmensregister. "
        "API-Key (Basic-Auth) über developer.company-information.service.gov.uk. "
        "Suche: /search/companies?q={name}. "
        "Detail: /company/{company_number}. "
        "Personen: /company/{number}/officers. "
        "Rate-Limit: 600 req/5 min (ohne erhöhtes Limit). "
        "ENV: COMPANIES_HOUSE_API_KEY."
    ),
)

_CLINICALTRIALS = SourceConfig(
    source_id="clinicaltrials",
    display_name="ClinicalTrials.gov – U.S. National Library of Medicine",
    source_class="tools.sources.clients.clinicaltrials.ClinicalTrialsClient",
    base_url="https://clinicaltrials.gov/api/v2",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.CLINICAL,
        ClaimDomain.MEDICAL,
        ClaimDomain.PHARMACEUTICAL,
    ),
    authority_weight=_SAW.get("clinicaltrials", 0.91),
    classifier_domains=("clinicaltrials.gov",),
    jurisdictions=("us", "global"),
    rate_limit_rps=10.0,
    requires_registration=False,
    notes=(
        "US Public Domain. Über 500.000 klinische Studien weltweit. "
        "API v2 (2023): /studies?query.term={term}&fields=…. "
        "Kernfelder: NCTId, BriefTitle, OfficialTitle, OverallStatus, "
        "StartDate, PrimaryCompletionDate, Condition, InterventionName. "
        "fulltext_allowed=False: Studienprotokolle sind komplex – "
        "nur strukturierte Metadaten und Kurzfassungen."
    ),
)

_DAILYMED = SourceConfig(
    source_id="dailymed",
    display_name="DailyMed – FDA Drug Label Information",
    source_class="tools.sources.clients.dailymed.DailyMedClient",
    base_url="https://dailymed.nlm.nih.gov/dailymed/services/v2",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(
        ClaimDomain.PHARMACEUTICAL,
        ClaimDomain.MEDICAL,
        ClaimDomain.REGULATORY,
    ),
    authority_weight=_SAW.get("dailymed", 0.94),
    classifier_domains=("dailymed.nlm.nih.gov",),
    jurisdictions=("us",),
    rate_limit_rps=5.0,
    requires_registration=False,
    notes=(
        "US Public Domain. Offizielle FDA-Arzneimittel-Etikettierungsdatenbank. "
        "Suche: /drugnames.json?drug_name={name}. "
        "Detail via SPL-SetID: /spls/{setid}.json. "
        "Enthält: Indikationen, Kontraindikationen, Nebenwirkungen, Dosierung. "
        "fulltext_allowed=False: SPL-XML-Volltexte zu groß / zu komplex; "
        "strukturierte JSON-Felder als Excerpt."
    ),
)

_PUBMED = SourceConfig(
    source_id="pubmed",
    display_name="PubMed – NCBI Biomedical Literature Database",
    source_class="tools.sources.clients.pubmed.PubMedClient",
    base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
    auth_mode=AuthMode.API_KEY_OPTIONAL,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.CHECK_TERMS,
    citation_required=True,
    claim_domains=(
        ClaimDomain.MEDICAL,
        ClaimDomain.SCIENTIFIC,
        ClaimDomain.PHARMACEUTICAL,
        ClaimDomain.CLINICAL,
    ),
    authority_weight=_SAW.get("pubmed", 0.85),
    classifier_domains=("pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"),
    jurisdictions=("global",),
    rate_limit_rps=0.33,  # 3 req/s ohne Key, 10 req/s mit NCBI_API_KEY
    requires_registration=False,
    notes=(
        "Metadaten und Abstracts: Public Domain (NLM). "
        "Volltexte: Lizenzen variieren (Open Access PMC vs. paywalled Journals). "
        "fulltext_allowed=False: Nur Metadaten + Abstracts über ESearch + EFetch. "
        "commercial_reuse_ok=CHECK_TERMS: Abstracts OK, Volltexte je nach Zeitschrift. "
        "API-Key (ENV: NCBI_API_KEY) erhöht Rate-Limit von 3 auf 10 req/s. "
        "ESearch: /esearch.fcgi?db=pubmed&term={query}&retmax=10. "
        "EFetch: /efetch.fcgi?db=pubmed&id={pmid}&rettype=abstract."
    ),
)

# ── GDELT (Globale Medienbeobachtung, Cross-Source-Corroboration) ────────────

_GDELT = SourceConfig(
    source_id="gdelt",
    display_name="GDELT Project",
    source_class="tools.sources.clients.gdelt.GDELTClient",
    base_url="https://api.gdeltproject.org/api/v2/doc",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=False,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=True,
    claim_domains=(ClaimDomain.GENERAL,),
    authority_weight=_SAW.get("gdelt", 0.55),
    classifier_domains=("gdeltproject.org",),
    jurisdictions=("global",),
    rate_limit_rps=1.0,
    requires_registration=False,
    notes=(
        "Kein Auth, kein Volltext – nur Metadaten, Titel, URL. "
        "Zitierung + Link auf GDELT-Website Pflicht. "
        "Rate-Limits nicht formal dokumentiert – Backoff bei 429. "
        "Updates alle 15 Minuten. 100+ Sprachen, 65 live-übersetzt. "
        "DOC API: /doc?query={q}&mode=artlist&format=json&maxrecords=250."
    ),
)

# ── Wikidata (Strukturierte Entity-Verifizierung, SPARQL) ───────────────────

_WIKIDATA = SourceConfig(
    source_id="wikidata",
    display_name="Wikidata",
    source_class="tools.sources.clients.wikidata.WikidataClient",
    base_url="https://query.wikidata.org",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.FULL,
    fulltext_allowed=True,
    commercial_reuse_ok=CommercialUsePolicy.ALLOWED,
    citation_required=False,
    claim_domains=(
        ClaimDomain.BIOGRAPHICAL,
        ClaimDomain.GEOGRAPHIC,
        ClaimDomain.INSTITUTIONAL,
    ),
    authority_weight=_SAW.get("wikidata", 0.80),
    classifier_domains=("wikidata.org",),
    jurisdictions=("global",),
    rate_limit_rps=1.0,
    requires_registration=False,
    notes=(
        "CC0 – keine Einschränkungen. SPARQL-Endpoint. "
        "User-Agent Pflicht. 100M+ Entitäten, 16B+ Tripel. "
        "Ideal für Entity-Verifizierung: Personen, Orte, Organisationen. "
        "SPARQL: /sparql?query={SPARQL}&format=json."
    ),
)

# ── Wikipedia (Kontext-Snippets, Enzyklopädie) ──────────────────────────────

_WIKIPEDIA = SourceConfig(
    source_id="wikipedia",
    display_name="Wikipedia (DE)",
    source_class="tools.sources.clients.wikipedia.WikipediaClient",
    base_url="https://de.wikipedia.org/w/rest.php/v1",
    auth_mode=AuthMode.NONE,
    supports_search=True,
    supports_detail_fetch=True,
    allowed_storage=AllowedStorage.CACHE,
    allowed_display=AllowedDisplay.EXCERPT,
    fulltext_allowed=False,
    commercial_reuse_ok=CommercialUsePolicy.CHECK_TERMS,
    citation_required=True,
    claim_domains=(
        ClaimDomain.BIOGRAPHICAL,
        ClaimDomain.GEOGRAPHIC,
        ClaimDomain.GENERAL,
    ),
    authority_weight=_SAW.get("wikipedia", 0.55),
    classifier_domains=("wikipedia.org",),
    jurisdictions=("global", "de"),
    rate_limit_rps=5.0,
    requires_registration=False,
    notes=(
        "CC-BY-SA 3.0 – Attribution + ShareAlike Pflicht. "
        "Nur Excerpts anzeigen (fulltext_allowed=False). "
        "CHECK_TERMS: Supplementäre Kontextquelle, nicht primäre Evidenz. "
        "REST API: /search/page?q={query}&limit={n}. "
        "Achtung: REST API v1 Deprecation ab Juli 2026."
    ),
)

# ── Source Registry ───────────────────────────────────────────────────────────

# Interne Tabelle: source_id → SourceConfig
_REGISTRY: dict[str, SourceConfig] = {
    src.source_id: src
    for src in [
        _WORLD_BANK,
        _GLEIF,
        _OPENFDA,
        _OPENALEX,
        _ARXIV,
        _CROSSREF,
        _CERN_OPEN_DATA,
        _EUROSTAT,
        _EUR_LEX,
        _USPTO,
        _COMPANIES_HOUSE,
        _CLINICALTRIALS,
        _DAILYMED,
        _PUBMED,
        _GDELT,
        _WIKIDATA,
        _WIKIPEDIA,
    ]
}


class SourceRegistry:
    """Zentrale Registry aller institutionellen Datenquellen.

    Alle Methoden sind Klassenmethoden – kein Instanziierung nötig.
    Die Registry ist zur Laufzeit unveränderlich (read-only).

    Beispiele::

        # Alle Quellen
        sources = SourceRegistry.all()

        # Quellen nach Claim-Domäne
        medical = SourceRegistry.by_domain(ClaimDomain.MEDICAL)

        # Quellen mit hoher Autorität
        tier1 = SourceRegistry.by_authority_weight(min_weight=0.90)

        # Nur kommerziell sichere Quellen
        safe = SourceRegistry.commercial_safe()

        # Einzelne Quelle per ID
        wb = SourceRegistry.get("world_bank")

        # Classifier-Patterns für source_classifier.py
        patterns = SourceRegistry.classifier_patterns()
    """

    @classmethod
    def get(cls, source_id: str) -> SourceConfig | None:
        """Gibt die Konfiguration einer Quelle per ID zurück, oder ``None``."""
        return _REGISTRY.get(source_id)

    @classmethod
    def all(cls) -> list[SourceConfig]:
        """Gibt alle registrierten Quellen zurück (Reihenfolge: authority_weight ↓)."""
        return sorted(_REGISTRY.values(), key=lambda s: s.authority_weight, reverse=True)

    @classmethod
    def by_domain(cls, domain: ClaimDomain) -> list[SourceConfig]:
        """Gibt alle Quellen zurück, die die gegebene ClaimDomain abdecken.

        Sortiert nach authority_weight (höchste zuerst).
        """
        return sorted(
            [s for s in _REGISTRY.values() if s.covers_domain(domain)],
            key=lambda s: s.authority_weight,
            reverse=True,
        )

    @classmethod
    def by_domain_safe(cls, domain: ClaimDomain) -> list[SourceConfig]:
        """Wie ``by_domain()``, aber nur kommerziell sichere Quellen (ALLOWED).

        Schließt CHECK_TERMS, RESTRICTED und UNKNOWN aus.
        Sortiert nach authority_weight (höchste zuerst).
        """
        return sorted(
            [s for s in _REGISTRY.values()
             if s.covers_domain(domain) and s.is_runtime_allowed()],
            key=lambda s: s.authority_weight,
            reverse=True,
        )

    @classmethod
    def by_authority_weight(cls, min_weight: float = 0.0) -> list[SourceConfig]:
        """Gibt alle Quellen mit authority_weight >= min_weight zurück.

        Sortiert nach authority_weight (höchste zuerst).
        """
        return sorted(
            [s for s in _REGISTRY.values() if s.authority_weight >= min_weight],
            key=lambda s: s.authority_weight,
            reverse=True,
        )

    @classmethod
    def commercial_safe(cls) -> list[SourceConfig]:
        """Gibt alle Quellen zurück, bei denen kommerzielle Nutzung eindeutig erlaubt ist.

        Schließt CHECK_TERMS und RESTRICTED aus.
        """
        return [s for s in _REGISTRY.values() if s.is_commercial_safe()]

    @classmethod
    def by_domain_tier(cls, max_tier: int = 2) -> list[SourceConfig]:
        """Gibt alle Quellen zurück, deren domain_tier() <= max_tier ist.

        Verwendet die gleiche Tier-Skala wie EvidenceSource.domain_tier:
            1 = Statistikämter, Regulatoren (authority_weight >= 0.90)
            2 = Behörden, IGOs (authority_weight >= 0.75)
        """
        return sorted(
            [s for s in _REGISTRY.values() if s.domain_tier() <= max_tier],
            key=lambda s: s.authority_weight,
            reverse=True,
        )

    @classmethod
    def classifier_patterns(cls) -> dict[int, list[str]]:
        """Generiert Tier → Domain-Pattern-Mapping für source_classifier.py.

        Gibt ein Dict zurück, das direkt in ``_TIER_PATTERNS`` eingespeist
        werden kann::

            {
                1: ["gleif.org", "api.fda.gov", ...],   # SourceTier.OFFICIAL (5)
                2: ["openalex.org", "crossref.org", ...], # SourceTier.OFFICIAL (5)
            }

        Nur Quellen mit nicht-leerem ``classifier_domains`` werden berücksichtigt.
        Das Tier-Mapping folgt ``SourceConfig.domain_tier()``.
        """
        result: dict[int, list[str]] = {}
        for src in _REGISTRY.values():
            if not src.classifier_domains:
                continue
            tier = src.domain_tier()
            result.setdefault(tier, []).extend(src.classifier_domains)
        return result

    @classmethod
    def by_jurisdiction(cls, jurisdiction: str) -> list[SourceConfig]:
        """Gibt alle Quellen zurück, die der gegebenen Jurisdiktion zugeordnet sind.

        Sortiert nach authority_weight (höchste zuerst).
        """
        return sorted(
            [s for s in _REGISTRY.values() if jurisdiction in s.jurisdictions],
            key=lambda s: s.authority_weight,
            reverse=True,
        )

    @classmethod
    def by_jurisdiction_safe(cls, jurisdiction: str) -> list[SourceConfig]:
        """Wie ``by_jurisdiction()``, aber nur kommerziell sichere Quellen (ALLOWED).

        Sortiert nach authority_weight (höchste zuerst).
        """
        return sorted(
            [s for s in _REGISTRY.values()
             if jurisdiction in s.jurisdictions and s.is_runtime_allowed()],
            key=lambda s: s.authority_weight,
            reverse=True,
        )

    @classmethod
    def source_ids(cls) -> list[str]:
        """Gibt alle registrierten source_ids zurück."""
        return list(_REGISTRY.keys())

    @classmethod
    def __len__(cls) -> int:
        return len(_REGISTRY)

    @classmethod
    def __contains__(cls, source_id: str) -> bool:
        return source_id in _REGISTRY
