# Datenfluss – Eine Analyse von A bis Z

> Zurück: [[README]] | Siehe auch: [[Orchestrator]], [[Agenten]]

Dieses Dokument verfolgt eine einzelne Analyse-Anfrage durch das gesamte System.

---

## Überblick

```
Nutzer
  │  POST /api/analyze { text, url, tier }
  ▼
FastAPI (api/)
  │  job_id erstellt, Hintergrund-Task gestartet (_run_job)
  ▼
_run_job() Background Worker
  │
  ├─ Phase 0: URL-Content-Extraktion (nur wenn URL angegeben)
  │    └─ ContentExtractor → ExtractedContent (text + images[])
  │
  ├─ Phase 0.5: Bild-Analyse (nur für Social-Media-Plattformen)
  │    └─ ImageAnalyzerAgent(images) ← Twitter / Instagram / Threads
  │
  ├─ Orchestrator.analyze_async(text)
  │    ├─ Phase 1: ClaimExtractor + Deduplication
  │    │    ├─ LLM zerlegt Text in atomare Claims
  │    │    ├─ Dedupliziert nach canonical_hash (nur 1. Vorkommen behalten)
  │    │    └─ Top-N Filterung (nach priority_score)
  │    │
  │    ├─ Phase 2 + 3 (asyncio.gather, Claims in 4er-Batches):
  │    │    ├─ FactChecker (Claim 1) ──┐
  │    │    ├─ FactChecker (Claim 2)   │ parallel
  │    │    ├─ ...                     │
  │    │    ├─ NumberAuditor (stat.)   │
  │    │    └─ RhetoricAnalyzer   ─────┘
  │    │
  │    └─ Phase 4: Synthesizer
  │         └─ SynthesisResult (Rating, Korrekturen, Quellen, analysis_id …)
  │
  └─ Auto-Archivierung + Cross-Reference-Graph aktualisieren
  │
  ▼
Job-Store → GET /api/jobs/{id} liefert Ergebnis
```

---

## Phase 1: Claim-Extraktion und Deduplizierung

**Agent:** [[Agent-ClaimExtractor]]

Der Eingabetext (max. 10.000 Zeichen) wird an den `ClaimExtractorAgent` übergeben. Der Agent nutzt das LLM, um den Text in **atomare, verifizierbare Einzelbehauptungen** zu zerlegen. Nachdem alle Claims kanonisiert wurden, dedupliziert der Orchestrator sie nach `canonical_hash`.

**Input:**
```
"Die Bundesregierung hat 2023 die Rente um 4,39 % erhöht,
 obwohl die Inflation bei über 7 % lag."
```

**Output (ClaimExtractionResult):**
```python
claims = [
    Claim(id=1, text="Die Bundesregierung hat 2023 die Rente um 4,39 % erhöht.",
          type=ClaimType.STATISTICAL, canonical_hash="a1f3c2b9"),
    Claim(id=2, text="Die Inflation lag 2023 bei über 7 %.",
          type=ClaimType.STATISTICAL, canonical_hash="d4e5c7b1"),
]
implicit_claims = [
    "Die Rentenerhöhung reichte nicht aus, um die Inflation zu kompensieren."
]
```

**Deduplizierung:**
Nach Kanonisierung werden Claims mit gleichem `canonical_hash` gruppiert. Nur der erste eines Clusters wird analysiert – dies verhindert doppelte LLM-Aufrufe bei Paraphrasen wie:
- "Die Rente um 4,39% erhöht" (original)
- "Die Rentenerhöhung betrug etwa 4,39 Prozent" (Paraphrase, gleicher Hash)

Jeder Claim erhält außerdem ein `requires_agents`-Flag, das steuert, ob NumberAuditor hinzugezogen wird.

**Top-N Filterung:**
Nach Deduplizierung werden nach `priority_score` sortiert; konfigurierbar via `CLAIM_TOP_N` (0 = keine Limite).

---

## Phase 2: Claim-Analyse (Parallel)

**Agenten:** [[Agent-FactChecker]], [[Agent-NumberAuditor]]

Für **jeden** Claim wird ein eigener Async-Task gestartet. Alle laufen gleichzeitig mit `asyncio.gather()`:

```python
# Aus orchestrator.py:
tasks = []
for claim in top_claims:
    tasks.append(fact_checker.run_safe_async(claim, original_text=text))
    if claim.type == ClaimType.STATISTICAL:
        tasks.append(number_auditor.run_safe_async(claim))

results = await asyncio.gather(*tasks)
```

**Fehlerbehandlung:** Alle Agenten außer ClaimExtractor nutzen `run_safe_async()` → Fehler werden gesammelt, aber die Analyse läuft weiter.

### FactChecker-Pipeline pro Claim

**Agent:** `agents/fact_checker.py` (Facade, wraps EvidenceBuilder + CoVe + Verdict)

```
1. Cache-Lookup (SHA256-Key: claim_canonical_hash)
   ├─ Hit (exact) → FactCheckResult sofort zurückgeben
   ├─ Miss → Fallback zu Semantic Cache (optional)
   │   └─ Embedding-Similarity (Threshold 0.92)
   │   └─ Hit → FactCheckResult zurückgeben
   └─ Kein Hit → weiter

2. ClaimRouter.route_and_apply(claim)
   └─ Heuristische Quellenauswahl (kein LLM)
   └─ Mappt Claim-Signale → SourceRegistry
   └─ Output: Priorisierte Liste institutioneller Quellen + site:-Hints für SearXNG

3. EvidenceBuilderAgent.run_safe_async(claim)
   ├─ Adaptive Query-Generierung (LLM)
   │   └─ FACTUAL: 1-2 Queries
   │   └─ STATISTICAL: 3-5 Queries
   │
   ├─ Multi-Search (parallel)
   │   ├─ SearXNGClient (Primär, mit site:-Hints von Router)
   │   ├─ LangSearchClient (strukturierte Ergebnisse)
   │   ├─ GoogleFactCheckAPI (direkte Factchecker-Matches)
   │   ├─ LocalFactCheckDB (Offline-Fallback, DataCommons)
   │   └─ ClaimRouter → SourceClients (17 institutionelle Quellen)
   │       ├─ Neu: GDELTClient (Cross-Source-Corroboration)
   │       ├─ Neu: WikidataClient (Entity-Verifizierung, SPARQL)
   │       └─ Neu: WikipediaClient (Kontext-Snippets)
   │
   ├─ Evidence Deduplication (URL-Normalisierung)
   │
   ├─ Evidence Ranking
   │   └─ Formel: domain_tier*0.40 + relevance*0.35 + fc_bonus*0.15 + gfc_bonus*0.10
   │   └─ Optional: OpenPageRank Tier-Adjustment (±1.0 basierend auf Domain-PageRank)
   │
   ├─ Scraping (Top-N Quellen, async max 8 parallel)
   │   └─ Trafilatura/Newspaper4k mit Boilerplate-Entfernung
   │
   ├─ Contradiction Detection (WeightedContradiction)
   │   ├─ Typ: negation, numeric, temporal, tier, direction
   │   ├─ Severity: low, medium, high (abhängig von Source-Tier)
   │   └─ Max 5 beste Widersprüche pro Claim
   │
   └─ Trust Boundary: EvidencePack
       └─ EvidenceItem.excerpt max 800 Zeichen hard-cut
       └─ format_for_verdict() einzige Methode für LLM-Output

4. CoVeProcessor.process() (optional, if cove.enabled)
   ├─ Phase 1: Baseline-Assessment
   │   └─ LLM bewertet Claim auf Basis EvidencePack
   │
   ├─ Phase 2: Verifikationsfragen generieren
   │   └─ 2-N Fragen, die Baseline widerlegen könnten
   │   └─ Typen: number / timeframe / source / causality / definition / comparison / context
   │
   ├─ Phase 3: Unabhängige Antworten
   │   └─ Jede Frage einzeln beantworten, OHNE Baseline-Paraphrase
   │
   └─ Phase 4: Reconciliation
       └─ Baseline vs. Verifikationsantworten abgleichen
       └─ Output: CoVeTrace mit final_rating, confidence_delta, contradictions_found

5. VerdictAgent.execute(claim, evidence_pack)
   ├─ Liest NUR strukturiertes EvidencePack (NIEMALS rohes HTML)
   ├─ Bewertet Claim mit Confidence
   ├─ Berücksichtigt CoVeTrace falls vorhanden
   └─ Output: FactCheckResult(rating, evidence, correction, sources)

6. Verdict Calibration
   ├─ Confidence-Ceilings je nach Evidence-Qualität
   ├─ Consensus-Contradiction-Override
   │   └─ AGREEING+FALSE → MISLEADING
   │   └─ CONTRADICTORY+TRUE → MISLEADING
   └─ WeightedConfidence Berechnung

7. Cache-Storage
   ├─ FactCheckResult mit cache_key speichern
   ├─ TTL: Standard 24 Stunden (konfigurierbar via CACHE_TTL_HOURS)
   └─ Optional: Embedding für Semantic Cache
```

### NumberAuditor (nur für STATISTICAL-Claims)

**Agent:** `agents/number_auditor.py`

Wird nur für Claims mit `claim.type == ClaimType.STATISTICAL` gestartet (parallel zu FactChecker).

```
1. Cache-Lookup (same mechanism as FactChecker)
   └─ SHA256(claim_text) als Key

2. Adaptive Query-Generierung für Statistik-Kontext
   ├─ Spezifische Suchen für Statistik-Datenbanken
   ├─ Year-aware Queries
   └─ Betroffene Länder/Regionen als Kontext

3. Multi-Search nach Statistik-Quellen
   ├─ Primär: Nationale Statistik-Ämter (destatis.de, etc.)
   ├─ Sekundär: Internationale Datenbanken (World Bank, Eurostat)
   └─ Tertiär: Peer-reviewed Publikationen (arXiv, SSRN)

4. LLM-Prüfung der Methodik
   ├─ Berechnungsfehler? (Addition, Prozentrechnung, Normalisierung)
   ├─ Cherry-Picking? (Selektive Zeiträume, Base Effects)
   ├─ Simpson's Paradoxon? (Aggregation vs. Segment-Level)
   ├─ Kategorie-Verwechslung? (Suspects vs. Convicted vs. Charged)
   ├─ Pro-Kopf-Normalisierung? (Absolut vs. Relativ)
   └─ Statistische Signifikanz? (Schwankung vs. Trend)

5. NumberAuditResult
   ├─ verdict: CORRECT / PARTIALLY_CORRECT / INCORRECT / UNCLEAR
   ├─ methodological_issues: list[str]
   ├─ suggested_correction: str
   ├─ confidence: float
   └─ sources: list[str]

6. Cache-Storage
   └─ Result mit TTL speichern
```

**Hinweis:** NumberAuditor nutzt NICHT das Trust Boundary-Modell wie FactChecker. Es arbeitet direkt mit Raw-HTML-Inhalten und generiert seine eigene Strukturierung.

---

## Phase 3: Rhetorik-Analyse (Gleichzeitig mit Phase 2)

**Agent:** [[Agent-RhetoricAnalyzer]]

Der gesamte Originaltext wird an `RhetoricAnalyzerAgent` übergeben – **einmal, ohne Claim-Zerlegung**. Das LLM sucht nach Mustern auf Text-Ebene:

- Angstappelle, Loaded Language
- Whataboutismus, False Equivalence
- Cherry-Picking, Anecdotal Generalization
- Dog Whistles, implizite Kausalität

---

## Phase 4: Synthese

**Agent:** [[Agent-Synthesizer]]

Alle Ergebnisse aus Phase 2+3 werden gebündelt übergeben:

```
FactCheckResults (alle Claims)
NumberAuditResults (stat. Claims)
RhetoricAnalysisResult
ImageAnalysisResult (falls Bilder vorhanden)
```

Der Synthesizer:
1. Gewichtet alle Teilresultate
2. Bildet ein `OverallRating` (RELIABLE → FABRICATED)
3. Berechnet einen Konfidenzscore (0.0–1.0)
4. Erstellt eine Zusammenfassung
5. Listet bis zu 5 `key_corrections`
6. Schreibt `fairness_notes` (was war korrekt?)
7. Sammelt `analysis_errors` (Fehler einzelner Agenten)

→ [[Agent-Synthesizer]]

---

## Ergebnis-Persistenz

Nach Abschluss wird das `SynthesisResult` automatisch:
- im **Job-Store** (RAM, 1h TTL) für Polling gespeichert
- im **Analyse-Archiv** (SQLite, persistent) abgelegt – dedupliziert per SHA256(text)
- im **Cross-Reference-Graph** verknüpft (Claims ↔ Quellen)

---

## Fehlerbehandlung im Datenfluss

Jeder Agent ist in `run_safe_async()` eingebettet:

```python
result, error = await agent.run_safe_async(claim)
if error:
    synthesis_input.errors.append(error)
    # Analyse läuft weiter – ohne diesen Agenten
```

Das bedeutet: Fällt ein Agent aus (Timeout, LLM-Fehler, Netzwerkfehler), stoppt **nicht die gesamte Analyse**. Nur `ClaimExtractor` ist ein Hard-Failure – ohne Claims gibt es keine Analyse.

---

## Verwandte Dokumente

- [[Orchestrator]] – asyncio-Implementierung
- [[Agent-FactChecker]] – Suchpipeline im Detail
- [[Cache]] – Wie Zwischenspeicherung funktioniert
- [[API]] – Job-Queue und Polling
