# Agent: FactChecker

> Zurück: [[Agenten]] | Siehe auch: [[Websuche]], [[Cache]], [[Datenfluss]]

Der `FactCheckerAgent` ist der **Kern des Systems**. Er prüft eine einzelne Behauptung gegen reale Webquellen und bewertet sie mit einem strukturierten Rating.

---

## Aufgabe

Gegeben einen `Claim` und den Original-Kontext:
1. Generiert optimierte Suchanfragen
2. Sucht das Web nach Belegen
3. Klassifiziert und rankt Quellen nach Vertrauenswürdigkeit
4. Scrapt die relevantesten Quellen
5. Bewertet die Behauptung anhand der gefundenen Belege

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

## Such-Pipeline im Detail

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

## Caching

Cache-Key: `SHA256("fact_checker::{claim.text.lower()}::{context[:100]}")`

Bei Cache-Treffer: sofortiger Return, kein LLM/Suche-Aufruf.
TTL: 24 Stunden (konfigurierbar).

→ [[Cache]]

---

## Externe Faktenchecking-Datenbanken

Zusätzlich zur Websuche werden abgefragt:
- **Google Fact Check Tools API** – aggregiert Faktenchecks von Organisationen
- **ClaimBuster API** – prüft Claim-Datenbank auf bekannte Überprüfungen

---

## Verwandte Dokumente

- [[Agenten]] – Übersicht aller Agenten
- [[Websuche]] – Such-Provider und Multi-Search
- [[Cache]] – TTL, Keys, WAL-Modus
- [[Agent-NumberAuditor]] – läuft parallel für statistische Claims
- [[Datenmodelle]] – FactCheckResult, FactRating
