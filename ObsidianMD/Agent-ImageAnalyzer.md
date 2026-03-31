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

**Input:** `dict` mit Schlüsseln:
- `"image_urls"`: `list[str]` – Bild-URLs (max. 5)
- `"post_text"`: `str` – Begleittext des Posts (Kontext)

**Output:** `ImageAnalysisResult`
```python
class ImageAnalysisResult(BaseModel):
    items: list[ImageAnalysisItem]
    cross_image_observations: str  # Übergreifende Muster
    overall_assessment: str
```

Jedes `ImageAnalysisItem`:
```python
class ImageAnalysisItem(BaseModel):
    image_index: int                    # Index des Bildes (0-basiert)
    ocr_text: str = ""                  # Erkannter Text im Bild
    visible_elements: list[str] = []    # Personen, Orte, Logos, Symbole
    manipulation_signs: list[str] = []  # Inkonsistente Beleuchtung, Cloning-Artefakte etc.
    emotional_framing: str = ""         # Emotionale Rahmung durch Bildwahl/Perspektive
    infographic_data: str = ""          # Daten aus Infografiken/Charts (Text)
    context_clues: list[str] = []       # Zeitstempel, Geo-Hinweise, Logos
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

## Implementierung

**Datei:** `agents/image_analyzer.py` → `ImageAnalyzerAgent`

### Hauptablauf

```python
def execute(self, input_data: dict, context: str = "") -> ImageAnalysisResult:
    image_urls = input_data.get("image_urls", [])
    post_text = input_data.get("post_text", "")
    if not image_urls:
        return ImageAnalysisResult(overall_assessment="Keine Bilder vorhanden")

    # Limit auf max 5 Bilder (API-Kosten)
    image_urls = image_urls[:5]

    # 1. Vision API Analysis
    vision_result = await self._llm_vision(
        system_prompt="Analysiere diese Bilder auf Manipulation, emotionales Framing, Text-Extraktion ...",
        image_urls=image_urls
    )

    # 2. Parse strukturiertes Output
    items = [ImageAnalysisItem(**item) for item in vision_result.items]

    # 3. Cross-Image-Analyse
    cross_obs = self._analyze_cross_image_patterns(items)

    return ImageAnalysisResult(
        items=items,
        cross_image_observations=cross_obs,
        overall_assessment=vision_result.overall_assessment
    )
```

### Voraussetzungen

Der Agent erfordert ein **Vision-fähiges LLM**:
- **Claude Sonnet 4+** – vollständige Vision-Unterstützung
- **Claude Opus** – vollständige Vision-Unterstützung
- **Claude Haiku** – eingeschränkte Vision-Unterstützung
- **OpenAI GPT-4V** – Vision möglich

Bei [[Scout-Tiers|LITE-Tier]] oder Modellen ohne Vision-Support wird der Agent mit Warnung übersprungen (graceful degradation).

---

## Integration in den Workflow

Der ImageAnalyzer wird außerhalb der normalen Claim-Schleife gestartet – wenn der Input URL-basiert ist und Bilder extrahiert wurden:

```
URL eingeben
  → Content Extractor (tools/extractors/) → text + images[]
  → ClaimExtractor(text)
  → FactChecker + NumberAuditor (Claims)
  → RhetoricAnalyzer(text)
  → ImageAnalyzer(images)  ← parallel
  → Synthesizer(alle Ergebnisse)
```

### Media-Extraktion (`tools/extractors/`)

Für Video-Plattformen wird zusätzlich eine `MediaContent`-Struktur erzeugt:

| Platform | Extraktion |
|---|---|
| YouTube | Transkript (Untertitel/Whisper) + Keyframe-OCR |
| Instagram Reels | Video-Download + Whisper-Transkription |
| Twitter/Threads | Text + eingebettete Bilder |
| Facebook | Text + eingebettete Bilder |

Das Transkript (`list[TranscriptSegment]`) wird als Text an `ClaimExtractor` übergeben (max. 25.000 Zeichen). `frame_ocr`-Ergebnisse fließen in den `ImageAnalyzer`.

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
