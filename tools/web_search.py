"""Backward-Compat-Shim – alle Symbole aus tools.search re-exportiert.

Bestehende Imports wie ``from tools.web_search import SearchResult`` funktionieren weiter.
Neuer Code sollte ``from tools.search import ...`` verwenden.
"""

from tools.search import *  # noqa: F401,F403
