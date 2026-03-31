# Agenten – Übersicht

> Zurück: [[README]] | Siehe auch: [[Architektur]], [[Orchestrator]]

FakeNewsGuard verwendet **6 Top-Level-Agenten**, die alle von `BaseAgent` erben. Intern werden weitere spezialisierte Hilfsagenten eingesetzt (EvidenceBuilder, CoVeProcessor, VerdictAgent). Jeder Agent hat eine klar abgegrenzte Aufgabe.

---

## Agenten-Hierarchie

```
BaseAgent (agents/base.py)
├── ClaimExtractorAgent    → [[Agent-ClaimExtractor]]
├── FactCheckerAgent       → [[Agent-FactChecker]]
├── NumberAuditorAgent     → [[Agent-NumberAuditor]]
├── RhetoricAnalyzerAgent  → [[Agent-RhetoricAnalyzer]]
├── SynthesizerAgent       → [[Agent-Synthesizer]]
└── ImageAnalyzerAgent     → [[Agent-ImageAnalyzer]]
```

---

## BaseAgent (`agents/base.py`)

Die Basisklasse stellt allen Agenten eine gemeinsame Infrastruktur bereit.

### Konstruktor-Parameter
```python
BaseAgent(
    config: AppConfig,
    llm_client: LLMClient | None = None,        # optional
    search_client: WebSearchClient | None = None,
    cache: ClaimCache | None = None,
)
```

### Wichtige Methoden

| Methode | Signatur | Beschreibung |
|---|---|---|
| `run(input_data, context="")` | sync | Synchrone Ausführung |
| `run_async(input_data, context="")` | async | Asynchrone Ausführung |
| `run_safe(input_data, context="")` | sync | → `(result, None)` oder `(None, error_str)` |
| `run_safe_async(input_data, context="")` | async | Async-Version von run_safe |
| `execute(input_data, context="")` | abstract | Muss in Subklassen überschrieben werden |
| `_llm_text(system, user)` | | Reine Text-Antwort vom LLM |
| `_llm_json(system, user)` | | JSON-Antwort (mit Parsing) |
| `_llm_structured(system, user, schema, tool_name, tool_description)` | | Native Structured Output |
| `_llm_vision(system, user, image_urls)` | | Multimodale Anfrage |
| `_web_search(query, max_results=5)` | | Single-Query Websuche |
| `_web_multi_search(queries, max_results=5)` | | Multi-Query Websuche parallel |
| `_cache_get(claim_text, context="")` | | Cache-Abfrage |
| `_cache_set(claim_text, result, context="")` | | Cache-Eintrag schreiben |
| `_log(message)` | | Strukturierter Log-Eintrag |

### Graceful Degradation

Jeder Agent ist in `run_safe()` / `run_safe_async()` eingebettet. Tritt ein Fehler auf, wird er als String zurückgegeben – die Analyse läuft weiter.

```python
result, error = await agent.run_safe_async(claim)
# result ist None wenn Fehler, error ist None wenn Erfolg
```

**Ausnahme:** `ClaimExtractorAgent` ist ein Hard-Failure. Ohne extrahierte Claims kann keine Analyse stattfinden.

### Timeout

Jeder Agent hat einen Timeout von **180 Sekunden**. Bei Überschreitung wird die Ausführung abgebrochen und der Fehler als graceful degradation behandelt.

### Sync→Async-Bridge

Für Agenten, die sync-Bibliotheken verwenden, gibt es einen Thread-Pool:

```python
loop.run_in_executor(thread_pool, sync_function, *args)
```

Standard: `min(32, os.cpu_count() * 4)` Worker-Threads.

---

## Übersicht aller Agenten

### [[Agent-ClaimExtractor]] – ClaimExtractorAgent
- **Input:** Rohtext (str)
- **Output:** `ClaimExtractionResult`
- **Cache:** Nein
- **Websuche:** Nein
- **Besonderheit:** Hard-Failure, kein graceful degradation

### [[Agent-FactChecker]] – FactCheckerAgent
- **Input:** `Claim` + Originaltext
- **Output:** `FactCheckResult`
- **Cache:** Ja (24h TTL, optional Semantic Cache mit Embedding-Similarity)
- **Websuche:** Ja (adaptiv, 1–5 Queries)
- **Besonderheit:** Pipeline aus EvidenceBuilder (Retrieval + Trust Boundary) → CoVe (Chain-of-Verification) → VerdictAgent (Rating) + VerdictCalibration (Overrides)
- **Interne Pipeline:**
  1. EvidenceBuilder – Generiert strukturiertes EvidencePack, erkennt Widersprüche (Typ/Schweregrad)
  2. CoVeProcessor – Baseline-Assessment → Verifikationsfragen → Reconciliation
  3. VerdictAgent – Finale Rating-Entscheidung mit Confidence
  4. VerdictCalibration – Confidence-Ceilings + Consensus-Contradiction-Override

### [[Agent-NumberAuditor]] – NumberAuditorAgent
- **Input:** `Claim` (STATISTICAL)
- **Output:** `NumberAuditResult`
- **Cache:** Ja (24h TTL)
- **Websuche:** Ja
- **Besonderheit:** 9 Manipulationstypen erkennbar

### [[Agent-RhetoricAnalyzer]] – RhetoricAnalyzerAgent
- **Input:** Volltext (str)
- **Output:** `RhetoricAnalysisResult`
- **Cache:** Nein
- **Websuche:** Nein
- **Besonderheit:** Analysiert den ganzen Text, nicht einzelne Claims

### [[Agent-Synthesizer]] – SynthesizerAgent
- **Input:** Alle Agenten-Ergebnisse
- **Output:** `SynthesisResult`
- **Cache:** Nein
- **Websuche:** Nein
- **Besonderheit:** Gewichtet Teilresultate, berechnet Konfidenzscore

### [[Agent-ImageAnalyzer]] – ImageAnalyzerAgent
- **Input:** `dict` mit `image_urls: list[str]` (max. 5) + `post_text: str`
- **Output:** `ImageAnalysisResult`
- **Cache:** Nein
- **Websuche:** Nein
- **Besonderheit:** Erfordert Vision-fähiges LLM, max. 5 Bilder

---

## Agenten im Orchestrator

Die Agenten werden vom [[Orchestrator]] in einem 4-Phasen-Workflow koordiniert:

| Phase | Agenten | Modus |
|---|---|---|
| 1 | ClaimExtractor | Sequenziell |
| 2 | FactChecker, NumberAuditor | Parallel (asyncio.gather) |
| 3 | RhetoricAnalyzer | Gleichzeitig mit Phase 2 |
| 4 | Synthesizer | Sequenziell (nach Phase 2+3) |

---

## Verwandte Dokumente

- [[Agent-ClaimExtractor]] | [[Agent-FactChecker]] | [[Agent-NumberAuditor]]
- [[Agent-RhetoricAnalyzer]] | [[Agent-Synthesizer]] | [[Agent-ImageAnalyzer]]
- [[Datenmodelle]] – Alle Input/Output-Typen
- [[LLM-Abstraktion]] – Wie Agenten mit dem LLM kommunizieren
