"""Source Layer – Policy-Enums und SourceConfig-Dataclass.

Diese Typen bilden das gemeinsame Vokabular aller Source-Registry-Einträge
und sind bewusst von den Evidence-Modellen (models/evidence_models.py) getrennt:

    tools/sources/ → Retrieval-Konfiguration (Quellen, Policies, Auth)
    models/         → Evidence-Layer (verarbeitetes Ergebnis, Trust Boundary)

Designprinzipien:
    - SourceConfig ist ein ``frozen`` Dataclass → hashbar, unveränderlich
    - Claim-Domains und Classifier-Domains als ``tuple`` (hashbar statt list)
    - ``domain_tier()`` mappt authority_weight auf die bestehende 1–5-Skala
      aus EvidenceSource.domain_tier, damit die Registry nahtlos ins
      bestehende Ranking integrierbar ist
    - Keine Imports aus anderen Projekt-Modulen (minimale Kopplung)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ── Policy-Enums ──────────────────────────────────────────────────────────────


class AuthMode(str, Enum):
    """Authentifizierungsmodell einer Datenquelle."""

    NONE = "none"
    """Keine Authentifizierung – vollständig öffentlicher Zugang."""

    API_KEY = "api_key"
    """API-Key zwingend erforderlich (Header oder Query-Parameter)."""

    API_KEY_OPTIONAL = "api_key_optional"
    """API-Key optional – ohne Key niedrigeres Rate-Limit oder kleinere Ergebnismengen."""

    OAUTH2 = "oauth2"
    """OAuth 2.0 – Authorization-Code- oder Client-Credentials-Flow."""

    BEARER_TOKEN = "bearer_token"
    """Bearer-Token (z. B. JWT) – ohne OAuth-Flow."""

    EMAIL_POLITE = "email_polite"
    """E-Mail-Adresse im User-Agent für „Polite Pool" (OpenAlex, Crossref).
    Kein API-Key, aber höhere Rate-Limits bei Einhaltung der Etikette."""


class AllowedStorage(str, Enum):
    """Zulässige Speicherung von Quelldaten gemäß Lizenz und Terms of Service."""

    CACHE = "cache"
    """Persistenter lokaler Cache zulässig (z. B. SQLite mit TTL wie in tools/cache.py)."""

    SESSION_ONLY = "session"
    """Nur In-Memory-Speicherung – keine persistente Ablage."""

    NO_STORAGE = "none"
    """Speicherung explizit untersagt – Daten dürfen nur transient verarbeitet werden."""


class AllowedDisplay(str, Enum):
    """Zulässige Darstellung von Quellinhalten gegenüber Endnutzern."""

    FULL = "full"
    """Vollständiger Antwortinhalt darf angezeigt werden."""

    EXCERPT = "excerpt"
    """Nur Auszüge (≤ 800 Zeichen, konform mit Trust-Boundary in EvidenceItem)."""

    METADATA_ONLY = "metadata"
    """Ausschließlich Metadaten (Titel, URL, Datum, IDs) – kein Textinhalt."""


class CommercialUsePolicy(str, Enum):
    """Kommerzielle Nutzungsrechte der Quelldaten."""

    ALLOWED = "allowed"
    """Kommerzielle Nutzung explizit erlaubt (CC BY, OGL v3, Public Domain, CC0)."""

    RESTRICTED = "restricted"
    """Nur nicht-kommerzielle Nutzung gestattet."""

    CHECK_TERMS = "check_terms"
    """Nutzungsrecht je nach Use Case prüfen (z. B. Metadaten vs. Volltext,
    API-Nutzung vs. Bulk-Download, Quelle der Inhalte)."""

    UNKNOWN = "unknown"
    """Lizenz nicht eindeutig geklärt – vor kommerziellem Einsatz prüfen."""


class ClaimDomain(str, Enum):
    """Thematischer Anwendungsbereich einer Quelle für Claim-basiertes Routing.

    Wird genutzt, um bei einem gegebenen Claim-Typ die passenden
    Quellen aus der Registry auszuwählen (SourceRegistry.by_domain).
    """

    CLINICAL = "clinical"
    """Klinische Studien, medizinische Forschungsvorhaben."""

    CORPORATE = "corporate"
    """Unternehmensregistrierung, -struktur, LEIs, Eigentümerschaft."""

    ECONOMIC = "economic"
    """Wirtschaftsdaten, Entwicklungsindikatoren, BIP, Armut."""

    FINANCIAL = "financial"
    """Finanzmärkte, Unternehmensfinanzen, Kapitalmärkte."""

    LEGAL = "legal"
    """Gesetze, Verordnungen, Rechtsprechung, Verträge."""

    MEDICAL = "medical"
    """Medizin, Gesundheitswesen, Diagnostik, Therapie."""

    PATENT = "patent"
    """Patente, Gebrauchsmuster, geistiges Eigentum."""

    PHARMACEUTICAL = "pharmaceutical"
    """Arzneimittelzulassungen, Wirkstoffdaten, Packungsbeilagen."""

    REGULATORY = "regulatory"
    """Behördliche Regulierung, Compliance, Aufsicht."""

    SCIENTIFIC = "scientific"
    """Wissenschaftliche Publikationen, Peer-Review, Forschungsdaten."""

    STATISTICAL = "statistical"
    """Statistische Erhebungen, Indikatoren, amtliche Statistiken."""

    TRADE = "trade"
    """Handel, Außenwirtschaft, Zölle, Exportkontrollen."""

    # ── Thematische Domänen (Klima, Migration, Technologie) ──

    ENVIRONMENT = "environment"
    """Klimawandel, Umwelt, Energie, Biodiversität, Emissionen."""

    MIGRATION = "migration"
    """Asyl, Flucht, Zuwanderung, Aufenthaltsrecht, Integration."""

    TECHNOLOGY = "technology"
    """Digitalisierung, KI, Cybersicherheit, Datenschutz, Netzpolitik."""

    # ── Wissens- und Nachrichtendomänen (GDELT, Wikidata, Wikipedia) ──

    BIOGRAPHICAL = "biographical"
    """Personen-Fakten: Amt, Geburt, Tod, Beziehungen."""

    GENERAL = "general"
    """Allgemeine Nachrichtenverifizierung, Cross-Source-Corroboration."""

    GEOGRAPHIC = "geographic"
    """Orte: Hauptstädte, Einwohnerzahlen, Fläche, Lage."""

    INSTITUTIONAL = "institutional"
    """Organisations-Fakten: Gründung, Sitz, Leitung."""


# ── SourceConfig ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceConfig:
    """Konfiguration einer einzelnen Datenquelle in der Source Registry.

    Das Dataclass ist ``frozen=True``:
        - Hashbar → verwendbar als dict-Key und in Sets
        - Unveränderlich → thread-safe, keine versehentliche Mutation
        - Alle Sequenzfelder als ``tuple`` (nicht ``list``) für Hashbarkeit

    Felder:
        source_id:
            Eindeutiger Bezeichner in snake_case.
        display_name:
            Menschenlesbarer Name für Logging, UI, Zitierungen.
        source_class:
            Vollständiger Python-Pfad des (zukünftigen) Client-Adapters,
            z. B. ``"tools.sources.clients.world_bank.WorldBankClient"``.
            Dient als Kontrakt für spätere Implementierung.
        base_url:
            Haupt-Endpunkt der API (ohne trailing slash).
        auth_mode:
            Authentifizierungsanforderung der Quelle.
        supports_search:
            Quelle unterstützt textuelle Suche oder Keyword-Abfrage.
        supports_detail_fetch:
            Quelle liefert Detail-Abruf per ID / URI / DOI.
        allowed_storage:
            Zulässige lokale Speicherung gemäß Lizenz/TOS.
        allowed_display:
            Zulässige Anzeige von Inhalten gegenüber Endnutzern.
        fulltext_allowed:
            ``True`` wenn Volltext abgerufen und verarbeitet werden darf.
            ``False`` = nur Metadaten / Abstracts (z. B. arXiv, PubMed).
        commercial_reuse_ok:
            Kommerzielle Nutzungsrechte (ALLOWED / RESTRICTED / CHECK_TERMS).
        citation_required:
            Zitierungspflicht bei Veröffentlichung von Ergebnissen.
        claim_domains:
            Für welche Claim-Domänen diese Quelle herangezogen werden soll.
        authority_weight:
            Glaubwürdigkeitsgewicht [0.0–1.0] für das Evidence-Ranking.
            Wird via ``domain_tier()`` auf die bestehende 1–5-Skala gemappt.
        classifier_domains:
            Domain-Muster (Substrings des Hostnames) für die automatische
            Erweiterung von ``source_classifier._TIER_PATTERNS``.
            Leer = keine automatische Classifier-Integration.
        jurisdictions:
            Jurisdiktions-Tags (``"eu"``, ``"uk"``, ``"us"``, ``"de"``,
            ``"global"``). Ermöglicht jurisdiction-basiertes Routing über
            ``SourceRegistry.by_jurisdiction()``.
        rate_limit_rps:
            Maximale Requests pro Sekunde. ``None`` = unbekannt / unbegrenzt.
        requires_registration:
            ``True`` wenn API-Key oder Nutzer-Registrierung nötig ist.
        notes:
            Freitext für Lizenzdetails, Nutzungshinweise, Besonderheiten.
    """

    source_id: str
    display_name: str
    source_class: str
    base_url: str
    auth_mode: AuthMode
    supports_search: bool
    supports_detail_fetch: bool
    allowed_storage: AllowedStorage
    allowed_display: AllowedDisplay
    fulltext_allowed: bool
    commercial_reuse_ok: CommercialUsePolicy
    citation_required: bool
    claim_domains: tuple[ClaimDomain, ...]
    authority_weight: float  # [0.0, 1.0]
    classifier_domains: tuple[str, ...] = ()
    jurisdictions: tuple[str, ...] = ()
    rate_limit_rps: float | None = None
    requires_registration: bool = False
    notes: str = ""

    def domain_tier(self) -> int:
        """Mappe authority_weight auf die bestehende domain_tier-Skala (1–5).

        Erhält Rückwärtskompatibilität mit ``EvidenceSource.domain_tier``::

            weight >= 0.90  →  Tier 1  (Statistikämter, Regulatoren, amtliche Register)
            weight >= 0.75  →  Tier 2  (Behörden, IGOs, anerkannte Fachbehörden)
            weight >= 0.55  →  Tier 3  (kuratierte Fachportale, Qualitätsjournalismus-Äquiv.)
            weight >= 0.35  →  Tier 4  (Faktenchecker-Äquivalent)
            else            →  Tier 5  (Sonstige)
        """
        if self.authority_weight >= 0.90:
            return 1
        elif self.authority_weight >= 0.75:
            return 2
        elif self.authority_weight >= 0.55:
            return 3
        elif self.authority_weight >= 0.35:
            return 4
        return 5

    def is_commercial_safe(self) -> bool:
        """Gibt ``True`` zurück wenn kommerzielle Nutzung eindeutig erlaubt ist."""
        return self.commercial_reuse_ok == CommercialUsePolicy.ALLOWED

    def is_runtime_allowed(self) -> bool:
        """Darf diese Quelle im Standardpfad geroutet werden?

        Nur Quellen mit eindeutig erlaubter kommerzieller Nutzung (ALLOWED)
        werden im Default-Pipeline-Pfad verwendet. CHECK_TERMS, RESTRICTED
        und UNKNOWN sind ausgeschlossen.
        """
        return self.commercial_reuse_ok == CommercialUsePolicy.ALLOWED

    def can_cache(self) -> bool:
        """Darf persistent gecacht werden (SQLite)?

        Nur Quellen mit ``AllowedStorage.CACHE`` erlauben persistenten Cache.
        SESSION_ONLY und NO_STORAGE werden nicht in SQLite gespeichert.
        """
        return self.allowed_storage == AllowedStorage.CACHE

    def max_excerpt_length(self) -> int:
        """Maximale Excerpt-Länge basierend auf display_policy + fulltext_allowed.

        Returns:
            0   für METADATA_ONLY (kein Inhalt erlaubt)
            400 für EXCERPT oder wenn fulltext_allowed=False (konservativ)
            800 für FULL mit fulltext_allowed=True
        """
        if self.allowed_display == AllowedDisplay.METADATA_ONLY:
            return 0
        if not self.fulltext_allowed or self.allowed_display == AllowedDisplay.EXCERPT:
            return 400
        return 800

    def covers_domain(self, domain: ClaimDomain) -> bool:
        """Gibt ``True`` zurück wenn diese Quelle die gegebene ClaimDomain abdeckt."""
        return domain in self.claim_domains
