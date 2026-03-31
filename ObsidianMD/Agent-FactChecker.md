# Agent: FactChecker

> Zurück: [[Agenten]] | Siehe auch: [[Websuche]], [[Cache]], [[Datenfluss]]

Der `FactCheckerAgent` ist der **Kern des Systems**. Er ist eine **Facade**, die mehrere spezialisierte Agenten koordiniert:

```
FactCheckerAgent (agents/fact_checker.py)
  ├── ClaimRouter: Heuristische Quellenauswahl
  ├── EvidenceBuilderAgent: Suche + Scraping
  ├── CoVeProcessor: Chain-of-Verification (optional)
  └── VerdictAgent: Finale Bewertung
```

**Gesamtaufgabe:** Gegeben einen `Claim` und den Original-Kontext:
1. Route Claim zu geeigneten Quellen (ClaimRouter)
2. Generiere optimierte Suchanfragen (Adaptive Query)
3. Suche das Web nach Belegen (Multi-Search parallel)
4. Klassifiziere und ranke Quellen nach Vertrauenswürdigkeit (Domain-Tier + Relevanz)
5. Scrapt die relevantesten Quellen (mit Trust Boundary)
6. Erkenne Widersprüche zwischen Quellen (WeightedContradiction)
7. Optionales Chain-of-Verification (CoVe) für kritische Claims
8. Bewertet Behauptung mit Confidence (VerdictAgent)
9. Kalibrier Confidence basierend auf Evidence-Qualität

---

## Input / Output

**Input:** `Claim`, `original_text: str`

**Output:** `FactCheckResult`
```python
@dataclass
class FactCheckResult:
    claim_id: int
    rating: FactRating
    evidence: str               # Zusammenfassung der Belege
    correction: str | None      # Korrekte Version, falls falsch
    missing_context: str | None # Fehlender Kontext
    sources: list[str]          # URLs
    classified_sources: list[ClassifiedSource]
```

---

## Bewertungsskala (FactRating)

| Rating | Bedeutung |
|---|---|
| `TRUE` | Wahr |
| `MOSTLY_TRUE` | Überwiegend wahr, kleinere Ungenauigkeiten |
| `MISLEADING` | Technisch korrekt, aber irreführend |
| `MOSTLY_FALSE` | Überwiegend falsch |
| `FALSE` | Eindeutig falsch |
| `UNVERIFIABLE` | Nicht verifizierbar |

---

## Ablauf (Implementierung)

```python
# agents/fact_checker.py
async def execute(self, claim: ProcessedClaim, original_text: str) -> FactCheckResult:
    # 0. Cache-Lookup (SHA256 der kanonisch Formulierung)
    cached = self.cache.get(claim.canonical_hash)
    if cached:
        return cached

    # 1. ClaimRouter: Quellenauswahl
    route_result = ClaimRouter.route_and_apply(claim)
    site_hints = route_result.search_profile.site_hints  # z.B. "site:destatis.de site:eurostat.eu"

    # 2-5. EvidenceBuilder: Such-Pipeline mit Trust Boundary
    evidence_pack = await self.evidence_builder.run_safe_async(claim, site_hints=site_hints)

    # 6. Optional: CoVe für kritische Claims
    if self.config.cove.enabled and claim.type in [STATISTICAL, CAUSAL]:
        cove_trace = await self.cove_processor.process(claim, evidence_pack)
    else:
        cove_trace = None

    # 7. VerdictAgent: Finale Bewertung
    verdict = await self.verdict_agent.execute(claim, evidence_pack, cove_trace)

    # 8. Calibration: Confidence anpassen
    calibrated_verdict = self._calibrate_confidence(verdict, evidence_pack, cove_trace)

    # 9. Cache-Storage
    self.cache.store(claim.canonical_hash, calibrated_verdict)

    return calibrated_verdict
```

---

## Pipeline im Detail

### Schritt 0: ClaimRouter – Intelligente Quellenauswahl

**Datei:** `tools/claim_router.py` → `ClaimRouter`

ClaimRouter ist **kein** LLM-Agent – er funktioniert heuristisch:

```python
# Beispiel:
claim = ProcessedClaim(
    text="Die Arbeitslosenquote lag 2023 in Deutschland bei 3,5%",
    type=ClaimType.STATISTICAL,
    entities=["Deutschland", "Arbeitslosenquote"]
)

route_result = ClaimRouter.route_and_apply(claim)
# Output: RouteResult(
#    claim_type=STATISTICAL,
#    suggested_sources=[
#        SourceConfig(domain="destatis.de", tier=1, weight=0.95),  # Statistisches Bundesamt
#        SourceConfig(domain="eurostat.ec.europa.eu", tier=1, weight=0.92),
#    ],
#    search_profile=SearchProfile(site_hints="site:destatis.de site:eurostat.eu"),
#    jurisdiction=Jurisdiction.DE
# )
```

**Routing-Signale:**
- **ClaimType** → STATISTICAL → Statistik-Datenbanken, FACTUAL → News/Journalismus, CAUSAL → Wissenschaftliche Quellen
- **ClaimDomain** → BIOGRAPHICAL → Wikidata, GEOGRAPHIC → Wikidata + Wikipedia, INSTITUTIONAL → Wikidata, GENERAL → GDELT
- **Entitäten** → Länder, Organisationen → Jurisdiktion-Boost
- **Schlüsselwörter** → z.B. "Behörde", "offizielle" → Tier-1-Ranking; "geboren", "gestorben" → Wikidata; "nachricht", "bericht" → GDELT
- **Kontext** → z.B. Budget/Finanzen → bestimmte Datenquellen präferieren

**Output:**
- Priorisierte Liste von `SourceConfig` mit Gewichten
- `SearchProfile` mit `site:`-Hints für SearXNG
- Jurisdiktion für Geographic-Prioritisierung

→ Siehe auch [[Cache#ClaimRouter-Caching]]

---

### Schritt 1: Adaptive Query-Generierung

Das LLM generiert **nicht nur eine naive Suche**, sondern 1–5 kontextsensitive Anfragen:

```
FACTUAL-Claim   → 1–2 Queries
STATISTICAL     → 3–5 Queries (mehr Quellen nötig)
CAUSAL          → 2–3 Queries
```

Das LLM bekommt den Originaltext + Claim und optimiert die Anfragen für Suchmaschinen:
- Schlagwörter statt ganzer Sätze
- Deutsche UND englische Varianten
- Zeitraum-Hinweise wenn nötig

### Schritt 2: Parallele Multi-Search

Alle Queries laufen gleichzeitig über `AsyncWebSearchClient.multi_search_async()`.
Ergebnisse werden dedupliziert (nach URL).

→ [[Websuche]]

### Schritt 3: Source-Klassifikation

Jede Quelle wird einem Tier zugeordnet:

| Tier | Beispiele |
|---|---|
| `OFFICIAL` | destatis.de, eurostat.eu, bundesregierung.de |
| `FACT_CHECKER` | correctiv.org, faktenfinder.tagesschau.de |
| `QUALITY_JOURNALISM` | spiegel.de, zeit.de, nyt.com |
| `MEDIA` | Allgemeine Nachrichtenmedien |
| `USER_GENERATED` | Wikipedia, Reddit, Blogs |

### Schritt 4: Relevanz-Scoring

Für jede Quelle wird ein Score berechnet:

```
score = keyword_overlap(claim_text, snippet + title)
# 0.0 bis 1.0
```

Pro Domain wird nur die relevanteste URL behalten (Deduplication).

### Schritt 5: Priorisiertes Scraping

Die Top-N-Quellen (Standard: 8) werden gescrapt:
- FACT_CHECKER-Quellen werden **immer** gescrapt
- Andere nur wenn Score > Threshold
- Bekannte Paywalls werden übersprungen
- Extraktion: trafilatura → BeautifulSoup Fallback
- Relevante Passagen werden herausgefiltert

→ Details in [[Tools#Source Scraper]]

### Schritt 6: Retry bei schlechter Qualität

Wenn die gescrapten Inhalte zu wenig relevante Information enthalten:
1. LLM generiert **Fallback-Suchanfragen**
2. Erneutes Scraping
3. Falls immer noch unzureichend → UNVERIFIABLE

### Schritt 7: LLM-Bewertung

Das LLM bekommt Claim + alle gescrapten Belege und erstellt strukturiert:
- `rating` (FactRating-Enum)
- `evidence` (Zusammenfassung der Belege)
- `correction` (korrekte Formulierung)
- `missing_context` (was fehlt)

---

### Schritt 8: Trust Boundary

**Problem:** Rohes HTML aus dem Web kann:
- Manipulative Inhalte enthalten
- Prompt-Injection ermöglichen
- Zu viel irrelevante Information enthalten

**Lösung:** Der VerdictAgent sieht **NIEMALS** rohes HTML.

EvidenceBuilderAgent durchlöchert Quellen und extrahiert nur relevante Abschnitte:

```python
# In evidence_builder.py:
for url, content in scraped_results:
    # 1. Extrahiere relevante Passagen
    relevant_passages = extract_relevant_passages(content, claim_text)

    # 2. Kürze auf max 800 Zeichen (Trust Boundary!)
    excerpt = "\n".join(relevant_passages)[:800]

    # 3. Erstelle strukturiertes EvidenceItem
    item = EvidenceItem(
        source=EvidenceSource(url=url, domain=domain, tier=tier),
        excerpt=excerpt,  # ← Hard-cut enforced!
        relevance_score=relevance,
        extraction_confidence=confidence
    )
```

**Ergebnis:** EvidencePack mit strukturierten EvidenceItems – sauberes Input für VerdictAgent.

---

### Schritt 7a: Contradiction Detection

**Datei:** `agents/evidence_scoring.py`

Das System erkennt automatisch Widersprüche zwischen Quellen:

```python
# Widerspruch-Typen:
class ContradictionType(Enum):
    NEGATION        # "ist" vs "ist nicht"
    NUMERIC         # Zahlen differieren 1.5x
    TEMPORAL        # Zeitpunkte unterscheiden sich
    TIER            # Konflikte zwischen hochqualitativen und nieder-qualitativen Quellen
    DIRECTION       # Dasselbe Thema, aber SUPPORTS vs REFUTES
```

**Schweregrad abhängig von Source-Tiers:**
- Tier 1 (Official) vs Tier 2 (Official) → HIGH
- Tier 1 vs Tier 3+ → MEDIUM
- Tier 3+ vs Tier 3+ → LOW

**Max 5 Widersprüche pro EvidencePack**, sortiert nach Schweregrad.

```python
# Beispiel:
contradiction = EvidenceContradiction(
    type=ContradictionType.NUMERIC,
    severity=ContradictionSeverity.HIGH,
    source1="destatis.de: Arbeitslosenquote 3,5%",
    source2="wirtschaft.org: Arbeitslosenquote 4,2%",
    claim1="Claim sagt 3,5%",
    claim2="Aber andere Quelle sagt 4,2%",
    reasoning="Unterschied von 0,7 Prozentpunkten, könnten verschiedene Berechnungsmethoden sein"
)
```

---

### Schritt 7b: Chain-of-Verification (Optional)

**Datei:** `agents/cove_processor.py`

Für kritische Claims (STATISTICAL, CAUSAL) wird CoVe aktiviert, um falsche Sicherheit zu vermeiden:

```python
# Beispiel CoVe-Workflow:
claim = ProcessedClaim(text="Inflationsrate 2023 war 11%")

# Phase 1: Baseline
baseline = verdict_agent.assess(claim, evidence_pack)
# → "MOSTLY_FALSE" (confidence 0.72, weil mehrere Quellen sagen 10.5%)

# Phase 2: Verifikationsfragen
questions = [
    "Wie wird Inflationsrate offiziell berechnet?",
    "Welche Länder hatten 11% Inflation?",
    "Könnten 11% für eine Teilmenge zutreffen (z.B. bestimmte Waren)?"
]

# Phase 3: Unabhängige Antworten
answers = [
    "HICP (harmonisierter Index) berechnet es so ...",
    "Ungarn, Polen hatten ~11%",
    "Ja, Energie-Sektor war 40%, Lebensmittel 20%"
]

# Phase 4: Reconciliation
# → Baseline bleibt MOSTLY_FALSE, aber mit Hinweis: "Claim könnte sich auf bestimmte Waren beziehen"
```

---

### Schritt 8: Verdict Agent & Calibration

**VerdictAgent** (`agents/verdict_agent.py`):
- Liest strukturiertes EvidencePack
- Berücksichtigt CoVeTrace falls vorhanden
- Generiert FactRating (TRUE / MOSTLY_TRUE / MISLEADING / MOSTLY_FALSE / FALSE / UNVERIFIABLE)
- Berechnet Confidence basierend auf Evidence-Qualität

**Calibration** (`agents/verdict_calibration.py`):
- **Consensus-Contradiction-Override:**
  - Wenn AGREEING+FALSE oder CONTRADICTORY+TRUE → MISLEADING downgrade
  - `VerdictRatingCalibrationConfig` steuert Schwellenwerte (konfigurierbar)
- **Confidence-Ceilings** (15+ Konstanten, Auswahl):
  | Szenario | Ceiling |
  |---|---|
  | Keine Primärquelle | 0.82 |
  | Hoher Off-topic-Anteil (>50%) | 0.75 |
  | Schwache Evidenzqualität | 0.70 |
  | Unzureichender Konsens | 0.65 |
  | Schlechte Claim-Qualität | 0.72 |
  | Niedrige Ø-Relevanz Top-5 | 0.68 |
  | Sehr niedrige Ø-Relevanz | 0.58 |
  | Hoher Low-Trust-Anteil | 0.62 |
  | Regulatory-Claim ohne offizielle Quelle | 0.72 |
  | Nur kontextuelle Evidenz | 0.65 |
  | Hoher Weak-Evidence-Anteil (>60%) | 0.60 |
  | Contextual + Low-Trust kombiniert | 0.55 |
  | Regulatory ohne direkten Beleg | 0.55 |
  | Veraltete Quellen | 0.72 |
  | Aktuell-Zustand-Claim ohne frische Quellen | 0.55 |
  | Keinerlei brauchbare Evidenz | 0.50 |
  | Regulatory + verrauschte Kontextevidenz | 0.45 |

---

## Caching

Cache-Key: `SHA256(claim.canonical_text.strip().lower())`

**Treffer-Raten:**
- Exakt identische Claims → ~5-10%
- Semantic Cache (Embeddings, Threshold 0.92) → ~15-25% zusätzlich

Bei Cache-Treffer: sofortiger Return, kein LLM/Suche-Aufruf.
TTL: 24 Stunden (konfigurierbar via `CACHE_TTL_HOURS`).

→ [[Cache]]

---

## Externe Faktenchecking-Datenbanken

Zusätzlich zur Websuche werden abgefragt:
- **Google Fact Check Tools API** – aggregiert Faktenchecks von Organisationen (kostenlos, 1000 Anfragen/Tag)
- **Lokale Faktencheck-DB** – DataCommons ClaimReview-Daten (SQLite+FTS5, Offline-Fallback wenn Google FCT keine Treffer liefert)

Diese Ergebnisse werden in `EvidencePack.google_fact_check_matches` eingebunden.

---

## Institutionelle Datenquellen (17 Adapter)

Der ClaimRouter mappt Claims auf spezialisierte API-Adapter aus `tools/sources/clients/`:

| Adapter | Domäne | Authority-Weight |
|---|---|---|
| WorldBankClient | Wirtschaft/Entwicklung | 0.88 |
| EurostatClient | EU-Statistiken | 0.90 |
| OpenAlexClient | Wissenschaft (CC0) | 0.82 |
| PubMedClient | Biomedizin | 0.85 |
| ClinicalTrialsClient | Klinische Studien | 0.88 |
| CrossrefClient | DOI-Metadaten | 0.78 |
| ArXivClient | Preprints | 0.75 |
| EURLexClient | EU-Legislation | 0.92 |
| GLEIFClient | Unternehmensregistrierung | 0.85 |
| OpenFDAClient | FDA-Regulierung | 0.87 |
| USPTOClient | US-Patente | 0.80 |
| CompaniesHouseClient | UK-Unternehmen | 0.83 |
| DailyMedClient | FDA-Etikettierungen | 0.83 |
| CERNOpenDataClient | Physik-Forschung | 0.87 |
| **GDELTClient** | Cross-Source-Corroboration | 0.55 |
| **WikidataClient** | Entity-Verifizierung (SPARQL) | 0.80 |
| **WikipediaClient** | Kontext-Snippets (DE) | 0.55 |

**Neue Domänen** für Routing: `BIOGRAPHICAL` (Personen), `GEOGRAPHIC` (Orte), `INSTITUTIONAL` (Organisationen), `GENERAL` (Nachrichten).

---

## Verwandte Dokumente

- [[Agenten]] – Übersicht aller Agenten
- [[Datenfluss]] – Gesamtablauf mit Phase 2+3
- [[Agent-EvidenceBuilder]] – Such-Pipeline Detail
- [[Agent-CoVeProcessor]] – Chain-of-Verification Detail
- [[Agent-VerdictAgent]] – Verdikt-Generierung
- [[Cache]] – TTL, Keys, Semantic Cache
- [[Agent-NumberAuditor]] – läuft parallel für statistische Claims
- [[Datenmodelle]] – FactCheckResult, FactRating, EvidencePack
