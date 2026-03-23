# Agent: ImageAnalyzer

> Zurück: [[Agenten]] | Siehe auch: [[Datenmodelle]], [[LLM-Abstraktion]]

Der `ImageAnalyzerAgent` ist der **neueste Agent** im System. Er analysiert Bilder – besonders Social-Media-Grafiken, Screenshots und Infografiken – auf Manipulation, emotionales Framing und enthaltene Text-Claims.

---

## Aufgabe

Gegeben eine Liste von Bild-URLs (max. 5):
1. Extrahiert Text via OCR
2. Erkennt Bildmanipulationen
3. Analysiert emotionales Framing
4. Extrahiert Daten aus Infografiken
5. Identifiziert Kontext-Hinweise (Datum, Ort, Metadaten)

---

## Input / Output

**Input:** `list[str]` (Bild-URLs, max. 5)

**Output:** `ImageAnalysisResult`
```python
@dataclass
class ImageAnalysisResult:
    items: list[ImageAnalysisItem]
    cross_image_observations: str  # Übergreifende Muster
    overall_assessment: str
```

Jedes `ImageAnalysisItem`:
```python
@dataclass
class ImageAnalysisItem:
    url: str
    ocr_text: str | None
    manipulation_indicators: list[str]
    emotional_framing: str
    infographic_data: dict | None
    context_clues: list[str]
    credibility_flags: list[str]
```

---

## Analyse-Dimensionen

### OCR / Text-Extraktion
Der Agent liest alle im Bild enthaltenen Texte: Überschriften, Bildunterschriften, Wasserzeichen, eingebettete Statistiken.

### Manipulationserkennung
Sucht nach visuellen Artefakten:
- Inkonsistente Beleuchtung (Copy-Paste-Hinweise)
- Clone-Stamp-Muster
- Auflösungssprünge zwischen Bildregionen
- Verdächtige Schärfeverläufe

### Emotionales Framing
Beschreibt, welche emotionale Reaktion das Bild beim Betrachter auslösen soll:
- Angst / Bedrohung
- Mitleid / Empörung
- Triumph / Überlegenheit
- Normalisierung von Extremem

### Infografik-Daten
Bei Diagrammen und Infografiken: Extraktion der dargestellten Werte für numerischen Abgleich durch [[Agent-NumberAuditor]].

### Kontext-Hinweise
- Sichtbare Zeitstempel
- Geografische Hinweise
- Nachrichtenkanal-Logos
- Metadaten-Fragmente

---

## Voraussetzungen

Der Agent erfordert ein **Vision-fähiges LLM**. Er nutzt `_llm_vision()` aus der Basisklasse:

```python
result = await self._llm_vision(
    system_prompt=t("agents.image_analyzer.system_prompt"),
    user_message=t("agents.image_analyzer.user_message"),
    image_urls=image_urls[:5]
)
```

Bei [[Scout-Tiers|LITE-Tier]] oder Modellen ohne Vision-Support wird der Agent übersprungen.

---

## Integration in den Workflow

Der ImageAnalyzer wird außerhalb der normalen Claim-Schleife gestartet – wenn der Input URL-basiert ist und Bilder extrahiert wurden:

```
URL eingeben
  → Content Extractor → text + images[]
  → ClaimExtractor(text)
  → FactChecker + NumberAuditor (Claims)
  → RhetoricAnalyzer(text)
  → ImageAnalyzer(images)  ← parallel
  → Synthesizer(alle Ergebnisse)
```

---

## Cross-Image-Beobachtungen

Bei mehreren Bildern analysiert der Agent auch **Muster über Bilder hinweg**:
- Wiederholen sich dieselben manipulierten Elemente?
- Stammen alle Bilder erkennbar aus derselben Quelle/Kampagne?
- Erzählen die Bilder zusammen eine inkonsistente Story?

---

## Verwandte Dokumente

- [[Agenten]] – Gesamtübersicht
- [[LLM-Abstraktion]] – `_llm_vision()` Methode
- [[Scout-Tiers]] – Vision-Support je nach Tier
- [[Datenmodelle]] – ImageAnalysisResult
- [[Agent-Synthesizer]] – verarbeitet ImageAnalysisResult
