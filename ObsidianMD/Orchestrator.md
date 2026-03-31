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
    def __init__(self, config: AppConfig, on_step: Callable[[str, str], None] | None = None)

    def analyze(self, text: str) -> SynthesisResult
    async def analyze_async(self, text: str) -> SynthesisResult
```

`on_step` ist ein optionaler Callback für Schritt-Updates (gesetzt im Konstruktor) – wird von der [[API|FastAPI-Job-Queue]] verwendet, um dem Client Live-Updates zu senden.

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
    tasks.append(fact_checker.run_safe_async(claim, context=text))
    if _should_run_number_auditor(claim):  # Regelungsclaim-Erkennung via Frame
        tasks.append(number_auditor.run_safe_async(claim))
```

Alle Tasks laufen gleichzeitig mit `asyncio.gather(*tasks)`.

**Regelungsclaim-Erkennung (`_should_run_number_auditor`):** Nicht alle `STATISTICAL`-Claims brauchen den NumberAuditor. Claims mit `sanction`, `enforcement` oder `policy_context + institution` im Frame (z.B. „250 Euro Bußgeld") sind normative Angaben – kein numerisches Audit nötig. Explizite `requires_agents`-Anforderung hat Vorrang.

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
raw_results = await asyncio.gather(*tasks, return_exceptions=False)
```

`return_exceptions=False` ist möglich, weil jeder Claim-Task intern `run_safe_async()` nutzt – Fehler werden innerhalb des Tasks abgefangen (→ [[Agenten#Graceful Degradation|Graceful Degradation]]).

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

## Fehlerbehandlung & Graceful Degradation

Der Orchestrator sammelt Fehler aus einzelnen Agenten, ohne die Analyse zu unterbrechen:

```python
result, error = await agent.run_safe_async(claim)
if error:
    synthesis_input.analysis_errors.append({
        "agent": agent.__class__.__name__,
        "claim_id": claim.id,
        "error": error
    })
    # Analyse läuft weiter – ohne diesen Agenten
```

**Hard-Failures (stoppt die Analyse):**
- ClaimExtractor.run_async() – ohne Claims gibt es keine Analyse
- Input-Validierung – leerer oder zu langer Text

**Soft-Failures (werden gesammelt, Analyse läuft weiter):**
- FactChecker.run_safe_async()
- NumberAuditor.run_safe_async()
- RhetoricAnalyzer.run_safe_async()
- ImageAnalyzer.run_safe_async()
- Synthesizer.run_safe_async()

Alle Fehler werden in `SynthesisResult.analysis_errors` gesammelt und sind für den Client einsehbar.

---

## Timeout Management

Jeder Agent hat einen globalen Timeout von 180s (konfigurierbar via `AGENT_TIMEOUT`):

```python
# In BaseAgent.run_safe_async():
async with asyncio.timeout(self.config.agent_timeout):
    result = await self.execute(claim)
```

**Timeout-Behavior:**
- Timeout wird ausgelöst → `asyncio.TimeoutError`
- Error wird in `run_safe_async()` abgefangen
- Error wird zu `analysis_errors` hinzugefügt
- Analyse läuft weiter

---

## Input-Validierung

```python
def _validate_input(self, text: str) -> str:
    text = text.strip()
    if not text:
        raise InputValidationError("Kein Text zur Analyse angegeben.")
    if len(text) > self.config.max_input_chars:
        self._log(f"Input gekürzt: {len(text)} → {self.config.max_input_chars} Zeichen")
        text = text[: self.config.max_input_chars]  # Kürzt statt Fehler!
    return text
```

Standard-Limit: 25.000 Zeichen. Konfigurierbar via `AppConfig.max_input_chars`. Zu lange Texte werden **stillschweigend gekürzt** (kein Fehler).

---

## Deduplication & Top-N Selection

Nach Phase 1 wird `_select_top_claims()` aufgerufen:

```python
def _select_top_claims(self, result: ClaimProcessingResult) -> list[Claim]:
    # 1. Filterung: OPINION, is_checkworthy=False und is_valid_claim=False ausschließen
    checkable = [c for c in result.claims
                 if c.type != ClaimType.OPINION and c.is_checkworthy and c.is_valid_claim]

    # 2. Deduplizierung: Claims mit gleichem canonical_hash gruppieren
    seen_hashes: set[str] = set()
    deduped: list[Claim] = []
    for c in checkable:
        h = getattr(c, "canonical_hash", "") or ""
        if h and h in seen_hashes:
            continue
        if h:
            seen_hashes.add(h)
        deduped.append(c)
    checkable = deduped

    # 3. Top-N: Sortierung + Limit (0 = alle)
    top_n = self.config.claim_processing.top_n
    if top_n > 0 and len(checkable) > top_n:
        checkable.sort(key=lambda c: -c.priority_score)
        checkable = checkable[:top_n]

    return checkable
```

**Reihenfolge:** Erst Filter (OPINION, nicht checkworthy, ungültig), dann Dedup, dann Top-N. Übersprungene Claims werden mit Grund geloggt.

**Beispiel:** Wenn 15 Claims extrahiert werden aber nur 5 eindeutig sind (nach Deduplication) und `CLAIM_TOP_N=3`, werden nur Top-3 faktengeprüft.

---

## Shared Clients

Der Orchestrator erstellt die Clients einmal und übergibt sie an alle Agenten:

```python
self.llm_client = LLMClient(config.llm)
self.search_client = WebSearchClient(config.search, config.retry)
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
