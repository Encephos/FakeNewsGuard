"""tools.sources.clients – Konkrete Adapter-Implementierungen für institutionelle Datenquellen.

Jeder Adapter erbt von ``BaseSourceAdapter`` und implementiert das einheitliche
Vier-Methoden-Interface: search, fetch_details, normalize, get_policy.

Verfügbare Adapter (17 Quellen):
    WorldBankClient        – World Bank Open Data (Wirtschaft/Entwicklung)
    OpenAlexClient         – OpenAlex (Wissenschaft, CC0)
    ClinicalTrialsClient   – ClinicalTrials.gov (Klinische Studien)
    GLEIFClient            – GLEIF (Unternehmensregistrierung)
    OpenFDAClient          – openFDA (FDA-Regulierungsdaten)
    CrossrefClient         – Crossref (DOI-Metadaten)
    ArXivClient            – arXiv (Preprints, metadata-only)
    EurostatClient         – Eurostat (EU-Statistiken, simplified)
    EURLexClient           – EUR-Lex (EU-Legislation)
    USPTOClient            – USPTO PatentsView (US-Patente)
    CompaniesHouseClient   – Companies House (UK-Unternehmensregister)
    DailyMedClient         – DailyMed (FDA-Etikettierungen)
    PubMedClient           – PubMed (Biomedizin-Literatur, metadata-only)
    CERNOpenDataClient     – CERN Open Data (Physik-Forschungsdaten)
    GDELTClient            – GDELT Project (Cross-Source-Corroboration)
    WikidataClient         – Wikidata (Entity-Verifizierung, SPARQL)
    WikipediaClient        – Wikipedia DE (Kontext-Snippets)

Basisklassen / Utilities:
    BaseSourceAdapter      – Abstrakte Basisklasse; von allen Adaptern zu erben.
    AdapterHTTPClient      – Dünner httpx-Wrapper mit Retry und Timeout.
    AdapterHTTPError       – Exception für permanente HTTP-Fehler nach allen Retries.

Neuen Adapter hinzufügen:
    1. Datei ``tools/sources/clients/<source_id>.py`` anlegen.
    2. ``BaseSourceAdapter`` als Basisklasse verwenden.
    3. ``config`` auf ``SourceRegistry.get("<source_id>")`` setzen.
    4. Die vier Pflichtmethoden implementieren.
    5. Hier in ``__all__`` exportieren.

Verwendung::

    from tools.sources.clients import WorldBankClient, ClinicalTrialsClient, PubMedClient

    wb = WorldBankClient()
    items = wb.search("Germany GDP", max_results=5)

    ct = ClinicalTrialsClient()
    studies = ct.search("obesity", max_results=3)

    pm = PubMedClient()
    articles = pm.search("covid vaccine", max_results=10)
"""

from tools.sources.clients.arxiv import ArXivClient
from tools.sources.clients.base import (
    AdapterHTTPClient,
    AdapterHTTPError,
    BaseSourceAdapter,
)
from tools.sources.clients.cern_opendata import CERNOpenDataClient
from tools.sources.clients.clinicaltrials import ClinicalTrialsClient
from tools.sources.clients.companies_house import CompaniesHouseClient
from tools.sources.clients.crossref import CrossrefClient
from tools.sources.clients.dailymed import DailyMedClient
from tools.sources.clients.eur_lex import EURLexClient
from tools.sources.clients.eurostat import EurostatClient
from tools.sources.clients.gdelt import GDELTClient
from tools.sources.clients.gleif import GLEIFClient
from tools.sources.clients.openalex import OpenAlexClient
from tools.sources.clients.openfda import OpenFDAClient
from tools.sources.clients.pubmed import PubMedClient
from tools.sources.clients.uspto import USPTOClient
from tools.sources.clients.wikidata import WikidataClient
from tools.sources.clients.wikipedia import WikipediaClient
from tools.sources.clients.world_bank import WorldBankClient

__all__ = [
    "AdapterHTTPClient",
    "AdapterHTTPError",
    "BaseSourceAdapter",
    "ArXivClient",
    "CERNOpenDataClient",
    "GDELTClient",
    "ClinicalTrialsClient",
    "CompaniesHouseClient",
    "CrossrefClient",
    "DailyMedClient",
    "EURLexClient",
    "EurostatClient",
    "GLEIFClient",
    "OpenAlexClient",
    "OpenFDAClient",
    "PubMedClient",
    "USPTOClient",
    "WikidataClient",
    "WikipediaClient",
    "WorldBankClient",
]
