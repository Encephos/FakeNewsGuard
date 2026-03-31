# Agent: ClaimExtractor

> Zurück: [[Agenten]] | Siehe auch: [[Datenfluss]], [[Datenmodelle]]

Der `ClaimExtractorAgent` ist der **erste Agent** in der Pipeline. Er zerlegt einen beliebigen Text in atomare, verifizierbare Einzelbehauptungen.

---

## Aufgabe

Gegeben ein Rohtext (Artikel, Post, Transkript), identifiziert der Agent:
1. **Explizite Claims** – direkt ausgesprochene Behauptungen
2. **Implizite Claims** – Behauptungen, die im Subtext stecken

Jede Behauptung wird klassifiziert und mit Kontext angereichert.

---

## Input / Output

**Input:** `str` (Rohtext, max. 25.000 Zeichen)

**Output:** `ClaimProcessingResult` (Rückgabetyp ist `ClaimProcessingResult`, nicht das ältere `ClaimExtractionResult`)
```python
class ClaimProcessingResult(BaseModel):
    claims: list[ProcessedClaim]    # Erweiterte Claims mit canonical_text, priority_score etc.
    implicit_claims: list[str]
```

> **Hinweis:** `ClaimExtractionResult` existiert als Typ-Alias für Abwärtskompatibilität. Intern delegiert `ClaimExtractorAgent` an den `ClaimProcessorAgent`, der die vollständige 6-Stufen-Pipeline ausführt.

Jeder `Claim`:
```python
@dataclass
class Claim:
    id: int
    text: str
    type: ClaimType
    context: str           # umgebender Satzkontext
    requires_agents: list[str]  # ["fact_checker", "number_auditor"]
```

---

## Claim-Typen

| ClaimType | Beschreibung | Beispiel |
|---|---|---|
| `FACTUAL` | Überprüfbare Tatsachenbehauptung | „Scholz wurde 1958 geboren." |
| `STATISTICAL` | Zahlen, Prozente, Statistiken | „Die Inflation lag bei 7,9 %." |
| `CAUSAL` | Ursache-Wirkungs-Beziehung | „Die EZB-Zinspolitik verursachte die Inflation." |
| `OPINION` | Werturteil | „Das war die schlechteste Regierung." |
| `CONTEXTUAL` | Erfordert historischen/politischen Kontext | „Wie schon 2008 …" |

`STATISTICAL`-Claims lösen automatisch den [[Agent-NumberAuditor]] aus.

---

## Besonderheiten

### Hard-Failure
Im Gegensatz zu allen anderen Agenten ist `ClaimExtractorAgent` **kein graceful degradation**. Wenn er fehlschlägt (z.B. LLM-Timeout), wird die gesamte Analyse abgebrochen. Ohne Claims gibt es nichts zu prüfen.

```python
# Im Orchestrator:
result = await claim_extractor.run_async(text)
# Kein run_safe_async() hier!
```

### Kein Cache, keine Websuche
Der Agent benötigt weder Cache noch Websuche – er arbeitet rein auf dem Eingabetext mit dem LLM.

### Atomarisierung
Der Agent ist angewiesen, Claims so weit wie möglich aufzuteilen. Ein Satz wie:

> „Scholz erhöhte die Rente um 4% und senkte gleichzeitig die Steuern für Konzerne."

wird zu **zwei getrennten Claims**:
1. „Scholz erhöhte die Rente um 4%."
2. „Scholz senkte die Steuern für Konzerne."

Das ermöglicht präzisere Einzelprüfungen.

---

## Prompt-Struktur

Der System-Prompt ist über das [[Internationalisierung|i18n-System]] übersetzbar:

```python
system = t("agents.claim_extractor.system_prompt")
user = t("agents.claim_extractor.user_message").format(text=text)
```

Der Prompt enthält explizite Anweisungen zur Atomarisierung, Typisierung und Kontext-Erhaltung.

---

## Verwandte Dokumente

- [[Agenten]] – Gesamtübersicht aller Agenten
- [[Datenmodelle]] – Claim, ClaimExtractionResult
- [[Agent-FactChecker]] – verarbeitet jeden einzelnen Claim
- [[Orchestrator]] – ruft ClaimExtractor als Phase 1 auf
