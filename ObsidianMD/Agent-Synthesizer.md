# Agent: Synthesizer

> Zurück: [[Agenten]] | Siehe auch: [[Datenfluss]], [[Datenmodelle]]

Der `SynthesizerAgent` ist der **letzte Agent** in der Pipeline. Er aggregiert alle Teilresultate zu einem abschliessenden Gesamturteil.

---

## Aufgabe

Gegeben alle Agenten-Ergebnisse:
- FactCheckResults (alle Claims)
- NumberAuditResults (stat. Claims)
- RhetoricAnalysisResult
- ImageAnalysisResult (optional)

Erstellt:
1. Ein `OverallRating`
2. Einen Konfidenzscore
3. Eine Zusammenfassung
4. Korrekturen
5. Fairness-Anmerkungen

---

## Input / Output

**Input:** `SynthesisInput` (gebündelte Ergebnisse aller Agenten)

**Output:** `SynthesisResult`
```python
@dataclass
class SynthesisResult:
    overall_rating: OverallRating
    confidence: float              # 0.0 – 1.0
    summary: str
    claims_analysis: list[FactCheckResult]
    number_audits: list[NumberAuditResult]
    key_corrections: list[str]     # max. 5 wichtigste Korrekturen
    manipulation_techniques: list[RhetoricTechnique]
    fairness_notes: str            # Was war korrekt?
    sources: list[str]             # Alle verwendeten Quellen
    analysis_errors: list[str]     # Fehler einzelner Agenten
```

---

## Overall-Rating-Skala

| Rating | Bedeutung | Typisches Szenario |
|---|---|---|
| `RELIABLE` | Verlässlich | Alle Claims TRUE, keine Rhetorik |
| `MOSTLY_RELIABLE` | Überwiegend verlässlich | 1–2 kleinere Ungenauigkeiten |
| `MIXED` | Gemischt | Mix aus True/False, moderate Rhetorik |
| `MISLEADING` | Irreführend | Technisch korrekt, aber manipulativ gerahmt |
| `HIGHLY_MISLEADING` | Stark irreführend | Mehrere FALSE-Claims + Rhetorik-Muster |
| `FABRICATED` | Erfunden | Klare Falschbehauptungen, kein Faktenfundament |

---

## Konfidenzscore

Der Konfidenzscore (0.0–1.0) gibt an, **wie sicher** das System in seinem Urteil ist. Er hängt ab von:
- Qualität der gefundenen Quellen
- Anzahl UNVERIFIABLE-Ratings
- Widersprüchen zwischen Quellen
- Scraping-Erfolgsrate

Ein Wert von 0.5 bedeutet: Das System konnte viele Claims nicht eindeutig einordnen.

---

## Fairness-Mechanismus

Eine der wichtigsten Designentscheidungen: Der Synthesizer muss explizit notieren, **was korrekt war**.

```
fairness_notes: "Die statistischen Grunddaten (Rentenhöhe, Inflationsrate)
                 sind korrekt. Der Anstieg der Lebenshaltungskosten ist belegbar.
                 Nur die Kausalzuschreibung ist irreführend."
```

Das verhindert, dass das System einen Text mit einem korrekten Faktenkern pauschal als „falsch" abstempelt.

---

## Key Corrections

Maximal 5 der wichtigsten Korrekturen, prägnant formuliert:

```python
key_corrections = [
    "Die Rente stieg 2023 um 4,39%, nicht 4%.",
    "Die genannte Inflationsrate bezieht sich auf 2022, nicht 2023.",
    "Der Vergleich fehlt: Rentensteigerungen lagen historisch meist unter Inflation.",
]
```

---

## Fehlersammlung

Schlägt ein Agent fehl (Timeout, LLM-Fehler), wird der Fehler in `analysis_errors` gesammelt – der Synthesizer erstellt das Urteil trotzdem, mit reduzierter Konfidenz:

```python
analysis_errors = [
    "NumberAuditor konnte Claim #3 nicht prüfen: LLM-Timeout nach 180s",
    "FactChecker konnte keine Quellen für Claim #5 finden",
]
```

---

## Kein Cache

Der Synthesizer verwendet keinen Cache – jedes Gesamturteil hängt von frischen Agenten-Ergebnissen ab und sollte nicht wiederverwendet werden.

---

## Verwandte Dokumente

- [[Agenten]] – Gesamtübersicht
- [[Datenmodelle]] – SynthesisResult, OverallRating
- [[Datenfluss]] – Phase 4 im Workflow
- [[Orchestrator]] – Wie Synthesizer aufgerufen wird
- [[API]] – Wie SynthesisResult zum Frontend gelangt
