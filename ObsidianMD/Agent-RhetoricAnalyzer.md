# Agent: RhetoricAnalyzer

> Zurück: [[Agenten]] | Siehe auch: [[Datenmodelle]], [[Agent-Synthesizer]]

Der `RhetoricAnalyzerAgent` analysiert **manipulative Sprach- und Argumentationsmuster** im Gesamttext – unabhängig von der Wahrheit einzelner Behauptungen.

---

## Aufgabe

Gegeben der vollständige Originaltext (nicht einzelne Claims):
1. Identifiziert rhetorische Manipulationstechniken
2. Bewertet deren Schwere (Severity)
3. Liefert konkrete Textbeispiele
4. Beschreibt das übergreifende Framing

---

## Input / Output

**Input:** `str` (Volltext)

**Output:** `RhetoricAnalysisResult`
```python
@dataclass
class RhetoricAnalysisResult:
    techniques: list[RhetoricTechnique]
    overall_framing: str   # Zusammenfassung der Gesamt-Manipulation
```

Jede `RhetoricTechnique`:
```python
@dataclass
class RhetoricTechnique:
    name: str
    description: str
    example: str      # Zitat aus dem Text
    severity: Severity  # LOW | MEDIUM | HIGH
```

---

## Erkannte Manipulationstechniken

| Technik | Beschreibung | Beispiel |
|---|---|---|
| **Loaded Language** | Emotional aufgeladene Wörter | „Flüchtlingswelle", „Linksextremisten" |
| **Cherry-Picking** | Nur günstige Fakten auswählen | Nur das beste Quartal zitieren |
| **False Equivalence** | Ungleichgewichtiges gleichsetzen | „Beide Seiten haben Extremisten" |
| **Straw Man** | Verfälschte Gegenposition | „Die Grünen wollen alle Autos verbieten" |
| **Appeal to Fear** | Angst schüren ohne Belege | „Wenn wir nichts tun, wird es Chaos geben" |
| **Whataboutism** | Ablenkung durch Gegenfrage | „Was ist mit den Saudis?" |
| **Dog Whistle** | Codierte Botschaften | Bestimmte Formulierungen für Eingeweihte |
| **Implicit Causality** | Zusammenhang ohne Belege implizieren | „Seit der Reform stiegen die Preise" |
| **Anecdotal Generalization** | Einzelfall → Allgemeinaussage | „Mein Nachbar hat auch …" |
| **Numbers Framing** | Zahlen manipulativ rahmen | „Nur 5%" vs. „1 von 20" |

---

## Schweregradklassifikation

| Severity | Kriterien |
|---|---|
| `LOW` | Stilistisch auffällig, kaum irreführend |
| `MEDIUM` | Kann Leser irreführen, subtile Manipulation |
| `HIGH` | Klare Manipulationsabsicht, gefährliche Fehlinformation |

---

## Unterschied zu FactChecker

| FactChecker | RhetoricAnalyzer |
|---|---|
| Prüft **ob** etwas stimmt | Prüft **wie** etwas gesagt wird |
| Claim-basiert | Text-basiert |
| Nutzt Websuche | Kein externes Lookup |
| STATISTICAL/FACTUAL-Claims | Alle Texte |

Ein Text kann faktisch korrekt sein und trotzdem stark manipulative Rhetorik enthalten – und umgekehrt.

---

## Besonderheiten

### Kein Cache, keine Websuche
Der Agent arbeitet rein auf dem Eingabetext mit dem LLM. Rhetorik-Muster lassen sich nicht aus Datenbanken abrufen.

### Gleichzeitig mit Phase 2
Der RhetoricAnalyzer läuft **parallel zu den FactChecker-/NumberAuditor-Tasks**, nicht danach. Das spart erheblich Zeit:

```python
# orchestrator.py (vereinfacht)
tasks = [fact_check(claim) for claim in claims]
tasks += [rhetoric_analyze(text)]   # zusätzlicher Task
await asyncio.gather(*tasks)
```

### Gesamtframing
Neben Einzeltechniken erstellt der Agent eine Gesamt-Framing-Einschätzung: In welche Richtung wird der Leser gelenkt? Welches Bild soll erzeugt werden?

---

## Verwandte Dokumente

- [[Agenten]] – Gesamtübersicht
- [[Agent-Synthesizer]] – verarbeitet RhetoricAnalysisResult
- [[Datenmodelle]] – RhetoricAnalysisResult, Severity
- [[Orchestrator]] – Parallelisierungs-Strategie
