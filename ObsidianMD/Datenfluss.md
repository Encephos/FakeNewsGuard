# Datenfluss – Eine Analyse von A bis Z

> Zurück: [[README]] | Siehe auch: [[Orchestrator]], [[Agenten]]

Dieses Dokument verfolgt eine einzelne Analyse-Anfrage durch das gesamte System.

---

## Überblick

```
Nutzer
  │  POST /api/analyze { text, tier }
  ▼
FastAPI (api.py)
  │  job_id erstellt, Hintergrund-Task gestartet
  ▼
Orchestrator.analyze_async()
  │
  ├─ Phase 0: Initialisierung
  │    └─ analysis_id = uuid.uuid4().hex[:12]  (Korrelations-ID)
  │
  ├─ Phase 1: ClaimExtractor + Deduplication
  │    ├─ LLM zerlegt Text in atomare Claims
  │    ├─ Dedupliziert nach canonical_hash (nur 1. Vorkommen behalten)
  │    └─ Top-N Filterung (nach priority_score)
  │
  ├─ Phase 2 + 3 (asyncio.gather):
  │    ├─ FactChecker (Claim 1) ──┐
  │    ├─ FactChecker (Claim 2)   │ parallel
  │    ├─ ...                     │
  │    ├─ NumberAuditor (stat.)   │
  │    └─ RhetoricAnalyzer   ─────┘
  │
  └─ Phase 4: Synthesizer
       └─ SynthesisResult (Rating, Korrekturen, Quellen, analysis_id …)
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

Für **jeden** Claim wird ein eigener Async-Task gestartet. Alle laufen gleichzeitig.

### FactChecker-Pipeline pro Claim

```
1. Cache-Lookup (SHA256-Key)
     └─ Treffer → sofort zurückgeben
     └─ Kein Treffer → Fallback zu Semantic Cache (optional, Threshold 0.92)
     └─ Semantic Treffer → sofort zurückgeben
     └─ Kein Treffer → weiter

2. LLM generiert 3 optimierte Suchanfragen
     (kontextsensitiv: Original-Text + Claim-Text)

3. Multi-Search (alle Anfragen parallel)
     └─ Ergebnisse: Titel, URL, Snippet

4. EvidenceBuilder: Strukturiertes EvidencePack
     └─ Source-Klassifikation (Tier-Hierarchie)
     └─ Relevanz-Scoring (Keyword-Overlap vs. Claim-Text)
     └─ Scraping (async, max 8 gleichzeitig)
     └─ Widerspruchserkennung (Typ/Schweregrad, max 5)
     └─ Trust Boundary: Excerpts auf max. 800 Zeichen kürzen

5. CoVeProcessor (optional, if cove.enabled)
     └─ Baseline-Assessment (Schätzung basierend auf Evidence)
     └─ Verifikationsfragen generieren (2–3)
     └─ Unabhängige Antworten (ohne Baseline-Paraphrase)
     └─ Reconciliation (Konsistenz-Check)

6. VerdictAgent: Rating-Entscheidung
     └─ Liest strukturiertes EvidencePack (nie rohes HTML)
     └─ Confidence basierend auf Evidence-Qualität

7. VerdictCalibration: Confidence-Ceilings + Overrides
     └─ Consensus-Contradiction-Override:
        Wenn AGREEING+FALSE oder CONTRADICTORY+TRUE → MISLEADING

8. Cache-Storage
     └─ Ergebnis wird mit cache_key gespeichert
     └─ Optional: Embedding für Semantic Cache
```

### NumberAuditor (nur für STATISTICAL-Claims)

```
1. Cache-Lookup
2. LLM/Suche: Statistik-Quellen finden
3. LLM prüft Methodik:
   - Berechnungsfehler?
   - Manipulation (Basiseffekt, Cherry-Picking …)?
   - Korrekte Interpretation?
```

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
