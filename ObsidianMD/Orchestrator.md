# Orchestrator

> Zurück: [[README]] | Siehe auch: [[Datenfluss]], [[Agenten]]

Der `Orchestrator` ist der **Dirigent** des Systems. Er koordiniert alle Agenten, verwaltet den Async-Workflow und stellt sicher, dass Phasen in der richtigen Reihenfolge und mit maximaler Parallelisierung laufen.

---

## Dateipfad

`orchestrator.py`

---

## Klassen-Interface

```python
class Orchestrator:
    def __init__(self, config: AppConfig)

    def analyze(self, text: str, url: str | None = None) -> SynthesisResult
    async def analyze_async(self, text: str, url: str | None = None,
                            on_step: Callable | None = None) -> SynthesisResult
```

`on_step` ist ein optionaler Callback für Schritt-Updates – wird von der [[API|FastAPI-Job-Queue]] verwendet, um dem Client Live-Updates zu senden.

---

## 4-Phasen-Workflow

### Phase 0 – Initialisierung

```python
# Generiere eindeutige Korrelations-ID für durchgängiges Tracing
analysis_id = uuid.uuid4().hex[:12]  # z.B. "a1f3c2b9d4e5"
```

Diese ID wird in alle Log-Einträge eingebunden und am Ende im `SynthesisResult` zurückgegeben.

---

### Phase 1 – Extraktion (sequenziell)

```python
claims = await claim_extractor.run_async(text)
claims = self._select_top_claims(claims)  # Deduplizierung + Top-N Filterung
```

Muss zuerst abgeschlossen sein – alle anderen Phasen hängen von den extrahierten Claims ab. Hard-Failure bei Fehler.

**Deduplizierung:** Claims mit gleichem `canonical_hash` werden gruppiert; nur der erste bleibt, weitere werden aussortiert. Dies spart LLM-Kosten bei semantisch identischen Behauptungen (verschiedene Formulierungen).

→ [[Agent-ClaimExtractor]]

---

### Phase 2 – Claim-Analyse (parallel)

Für jeden Claim wird ein asyncio-Task erstellt:

```python
tasks = []
for claim in claims:
    tasks.append(fact_checker.run_safe_async(claim, original_text=text))
    if claim.type == ClaimType.STATISTICAL:
        tasks.append(number_auditor.run_safe_async(claim))
```

Alle Tasks laufen gleichzeitig mit `asyncio.gather(*tasks)`.

→ [[Agent-FactChecker]], [[Agent-NumberAuditor]]

---

### Phase 3 – Rhetorik (gleichzeitig mit Phase 2)

Der RhetoricAnalyzer wird als **zusätzlicher Task** zu Phase 2 hinzugefügt:

```python
tasks.append(rhetoric_analyzer.run_safe_async(text))
```

Da er den Volltext (nicht einzelne Claims) analysiert, muss er nicht auf Phase 2 warten. Er läuft komplett parallel.

→ [[Agent-RhetoricAnalyzer]]

---

### asyncio.gather – Die Parallelisierung

```python
# Phase 2 + 3 zusammen:
results = await asyncio.gather(*tasks, return_exceptions=True)
```

`return_exceptions=True` stellt sicher, dass ein Fehler in einem Task nicht alle anderen abbricht – zur [[Agenten#Graceful Degradation|Graceful Degradation]].

---

### Phase 4 – Synthese (sequenziell)

Nachdem Phase 2+3 abgeschlossen sind:

```python
synthesis_input = SynthesisInput(
    fact_checks=fact_check_results,
    number_audits=number_audit_results,
    rhetoric=rhetoric_result,
    image_analysis=image_result,
    errors=collected_errors,
)
result = await synthesizer.run_async(synthesis_input)
result.analysis_id = analysis_id  # Attach correlation ID
```

→ [[Agent-Synthesizer]]

---

## Schritt-Callbacks (`on_step`)

Der Orchestrator emittiert Schritt-Updates während der Analyse:

```python
# Beispiel-Steps die an on_step übergeben werden:
{ "step": "extraction", "status": "done", "claims_count": 7 }
{ "step": "fact_check", "claim_id": 1, "status": "running" }
{ "step": "fact_check", "claim_id": 1, "status": "done", "rating": "MOSTLY_TRUE" }
{ "step": "rhetoric", "status": "done" }
{ "step": "synthesis", "status": "done" }
```

Diese Steps werden von der [[API|FastAPI-Job-Queue]] gespeichert und per Polling an den Client gesendet.

---

## Input-Validierung

```python
def _validate_input(self, text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("Input ist leer")
    if len(text) > self.config.max_input_chars:
        raise ValueError(f"Input überschreitet {self.config.max_input_chars} Zeichen")
    return text
```

Standard-Limit: 10.000 Zeichen. Konfigurierbar via `AppConfig.max_input_chars`.

---

## Shared Clients

Der Orchestrator erstellt die Clients einmal und übergibt sie an alle Agenten:

```python
self.llm_client = LLMClient(config.llm)
self.search_client = AsyncWebSearchClient(config.search)
self.cache = ClaimCache(config.cache)
self.archive = AnalysisArchive(config.archive)
```

→ [[LLM-Abstraktion]], [[Websuche]], [[Cache]]

---

## Sync-Wrapper

Für CLI-Nutzung gibt es einen synchronen Wrapper:

```python
def analyze(self, text: str) -> SynthesisResult:
    return asyncio.run(self.analyze_async(text))
```

---

## Verwandte Dokumente

- [[Datenfluss]] – Visualisierung des Ablaufs
- [[Agenten]] – Alle Agenten die der Orchestrator nutzt
- [[API]] – Wie die Job-Queue den Orchestrator aufruft
- [[Scout-Tiers]] – Beeinflusst welche Modelle Orchestrator konfiguriert
