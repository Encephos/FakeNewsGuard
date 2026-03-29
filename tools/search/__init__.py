"""Web-Search-Abstraktionsschicht – SearXNG (primär), LangSearch (semantisch), + optionale Plugins.

Kern-Clients: SearXNGClient, LangSearchClient
Optionale Plugins: TavilyClient, Serper, Brave (via WebSearchClient Provider-Router)

Alle Klassen werden hier re-exportiert für Backward-Kompatibilität:
    from tools.web_search import SearchResult, WebSearchClient, ...
    from tools.search import SearchResult, WebSearchClient, ...
"""

from tools.search.client import AsyncWebSearchClient, WebSearchClient  # noqa: F401
from tools.search.langsearch import LangSearchClient  # noqa: F401
from tools.search.models import SearchResult  # noqa: F401
from tools.search.searxng import SearXNGClient, SearXNGQuery  # noqa: F401
from tools.search.tavily import TavilyClient  # noqa: F401

__all__ = [
    "AsyncWebSearchClient",
    "LangSearchClient",
    "SearchResult",
    "SearXNGClient",
    "SearXNGQuery",
    "TavilyClient",
    "WebSearchClient",
]
