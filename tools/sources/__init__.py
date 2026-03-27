"""tools.sources – Strukturierte Source-Integration-Schicht.

Dieses Package definiert die zentrale Registry aller kommerziell nutzbaren
institutionellen Datenquellen (World Bank, GLEIF, openFDA, Eurostat, etc.)
und die zugehörigen Policy-Typen.

Öffentliche API:
    SourceRegistry      – Zentrale Registry aller Quellen
    SourceConfig        – Konfiguration einer einzelnen Quelle
    AuthMode            – Authentifizierungsmodell
    AllowedStorage      – Zulässige Speicherung (Cache / Session / No)
    AllowedDisplay      – Zulässige Darstellung (Full / Excerpt / Metadata)
    CommercialUsePolicy – Kommerzielle Nutzungsrechte
    ClaimDomain         – Thematischer Anwendungsbereich für Claim-Routing

Verwendung::

    from tools.sources import SourceRegistry, ClaimDomain

    medical_sources = SourceRegistry.by_domain(ClaimDomain.MEDICAL)
    top_sources = SourceRegistry.by_authority_weight(min_weight=0.90)
    commercial_ok = SourceRegistry.commercial_safe()
"""

from tools.sources.registry import SourceRegistry
from tools.sources.types import (
    AllowedDisplay,
    AllowedStorage,
    AuthMode,
    ClaimDomain,
    CommercialUsePolicy,
    SourceConfig,
)

__all__ = [
    "AllowedDisplay",
    "AllowedStorage",
    "AuthMode",
    "ClaimDomain",
    "CommercialUsePolicy",
    "SourceConfig",
    "SourceRegistry",
]
