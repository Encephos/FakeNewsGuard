# Agent: NumberAuditor

> Zurück: [[Agenten]] | Siehe auch: [[Agent-FactChecker]], [[Datenmodelle]]

Der `NumberAuditorAgent` spezialisiert sich auf die **Validierung statistischer und mathematischer Behauptungen**. Er erkennt typische Datentricks, die technisch korrekte Zahlen trotzdem irreführend machen.

---

## Aufgabe

Gegeben einen `Claim` vom Typ `STATISTICAL`:
1. Sucht nach den Originaldaten
2. Prüft die Berechnung rechnerisch
3. Identifiziert methodische Manipulationen
4. Liefert die korrekte Interpretation

---

## Input / Output

**Input:** `Claim` (ClaimType.STATISTICAL)

**Output:** `NumberAuditResult`
```python
@dataclass
class NumberAuditResult:
    claim_id: int
    calculation_check: str      # Ist die Rechnung korrekt?
    methodology_issues: list[str]
    correct_interpretation: str
    manipulation_type: ManipulationType | None
```

---

## Erkannte Manipulationstypen (ManipulationType)

| Typ | Beschreibung | Beispiel |
|---|---|---|
| `BASE_EFFECT` | Basiseffekt ignoriert | „+100% nach −50%" |
| `ABSOLUTE_VS_RELATIVE` | Absolut/Relativ verwechselt | „3× so viele" statt „+0,003%" |
| `CATEGORY_ERROR` | Kategorien falsch zusammengefasst | Äpfel + Birnen |
| `CHERRY_PICKED_TIMEFRAME` | Günstiger Zeitraum ausgewählt | Nur 2019–2021 |
| `CUMULATION_TRICK` | Kumulierte Werte als Perioden-Werte | „€50 Mrd. in 10 Jahren" → „€5 Mrd./Jahr" |
| `TREND_VS_NOISE` | Statistisches Rauschen als Trend | Einzelwert-Ausreißer |
| `PER_CAPITA_MISSING` | Fehlende Pro-Kopf-Normierung | Absolute Zahlen ohne Bevölkerungsgröße |
| `CALCULATION_ERROR` | Rechenfehler | Falsch gerechneter Prozentwert |
| `OTHER` | Sonstiger Methodenfehler | – |

---

## Beispiel

**Behauptung:** „Verbrechen durch Migranten sind um 800% gestiegen."

**Audit-Ergebnis:**
```
calculation_check: "Steigerung von 100 auf 900 Fällen = +800%. Rechnerisch korrekt."
methodology_issues:
  - "Vergleicht absolute Zahlen ohne Bevölkerungsnormierung"
  - "Zeitraum 2015–2016 (Einwanderungspeak) als Basis"
  - "Keine Unterscheidung Tatverdächtige vs. Verurteilte"
correct_interpretation: "Pro-Kopf-Rate stieg um ~40%, deutlich weniger als absolute Zahlen suggerieren."
manipulation_type: ABSOLUTE_VS_RELATIVE
```

---

## Such-Strategie

Der NumberAuditor sucht bevorzugt nach:
- Originalquellen (Statistikämter, Forschungsinstitute)
- Methodenpapieren
- Gegendarstellungen von Statistik-Experten

**Bevorzugte Quellen:**
- destatis.de, statista.com, eurostat.eu
- Forschungsinstitute (ifo, DIW, IMF, Weltbank)
- Peer-reviewed Journale

---

## Zusammenspiel mit FactChecker

NumberAuditor und FactChecker laufen **parallel** für STATISTICAL-Claims:

```
STATISTICAL Claim
    ├─ FactChecker: "Ist die Zahl überhaupt korrekt?"
    └─ NumberAuditor: "Ist die Zahl korrekt dargestellt?"
```

Die Ergebnisse werden vom [[Agent-Synthesizer]] zusammengeführt.

---

## Caching

Gleiche Strategie wie [[Cache|FactChecker-Cache]]:
- SHA256-Key mit `"number_auditor"` als Agent-Prefix
- 24h TTL
- Nur bei identischem Claim-Text + Kontext Cache-Treffer

---

## Verwandte Dokumente

- [[Agenten]] – Gesamtübersicht
- [[Agent-FactChecker]] – prüft parallel die Faktengrundlage
- [[Agent-Synthesizer]] – führt beide Ergebnisse zusammen
- [[Datenmodelle]] – NumberAuditResult, ManipulationType
