# Scout-Tiers

> Zurück: [[README]] | Siehe auch: [[LLM-Abstraktion]], [[Konfiguration]]

FakeNewsGuard bietet drei **Scout-Tiers** an, die den Kompromiss zwischen Kosten, Geschwindigkeit und Qualität steuern.

---

## Übersicht

| Tier | Modell | Kosten | Geschwindigkeit | Qualität |
|---|---|---|---|---|
| **LITE** | Free Tier Router (OpenRouter) | Kostenlos | Variabel | Ausreichend |
| **PRO** | Gemma 3 (27B) | Moderat | Schnell | Gut |
| **MAX** | Gemma (schnell) + Qwen (komplex) | Höher | Schnell | Sehr gut |

---

## LITE

**Modell:** OpenRouter Free Tier Router

Nutzt die kostenlose Modell-Auswahl über OpenRouter. OpenRouter wählt je nach Verfügbarkeit ein freies Modell aus.

**Konfiguration:**
```python
LLMConfig(
    provider="openrouter",
    model="openrouter/auto",
    # + provider.sort="price", allow_fallbacks=true
)
```

**Eignet sich für:**
- Erste Tests
- Kostenloses Ausprobieren
- Nicht-kritische Analysen
- Entwicklung & Debugging

**Einschränkungen:**
- Keine garantierten Antwortzeiten
- Vision-Support möglicherweise nicht verfügbar
- Niedrigere Kontextfenster

---

## PRO

**Modell:** Gemma 3 27B (via OpenRouter)

Konsistente Leistung mit einem bewährten Open-Source-Modell. Alle Agenten nutzen dasselbe Modell.

**Konfiguration:**
```python
LLMConfig(
    provider="openrouter",
    model="google/gemma-3-27b-it",
)
```

**Eignet sich für:**
- Reguläre Nutzung
- Gute Balance aus Kosten und Qualität
- Produktivumgebungen mit Budget

---

## MAX

**Zwei-Modell-Strategie:** Verschiedene Agenten nutzen verschiedene Modelle.

| Agent | Modell | Begründung |
|---|---|---|
| ClaimExtractor | Gemma (schnell) | Strukturierte Extraktion, kein Reasoning nötig |
| FactChecker | Qwen (powerful) | Komplexe Quellenauswertung |
| NumberAuditor | Qwen (powerful) | Statistisches Reasoning |
| RhetoricAnalyzer | Gemma (schnell) | Muster-Erkennung, gut für Gemma |
| Synthesizer | Qwen (powerful) | Nuanciertes Gesamturteil |
| ImageAnalyzer | Qwen (powerful) | Vision + komplexes Reasoning |

**Konfiguration:**
```python
# Wird im Orchestrator pro Agent gesetzt
fast_llm = LLMConfig(provider="openrouter", model="google/gemma-3-27b-it")
powerful_llm = LLMConfig(provider="openrouter", model="qwen/qwen-2.5-72b-instruct")
```

**Eignet sich für:**
- Kritische Analysen
- Maximale Genauigkeit
- Keine Kostenbeschränkung

---

## Tier-Auswahl

### Via API
```json
POST /api/analyze
{ "text": "...", "tier": "pro" }
```

### Via CLI
```bash
python main.py "Text..." --tier max
```

### Via Frontend
[[Frontend#TierSelector|TierSelector-Komponente]] in der Web-UI.

### Via Konfiguration
```python
# config.py
AppConfig(tier=ScoutTier.PRO)
```

### Via Umgebungsvariable
```bash
SCOUT_TIER=pro
```

---

## ScoutTier-Enum

```python
class ScoutTier(Enum):   # kein str-Enum
    LITE = "lite"   # OpenRouter Free Tier Router (kostenlos)
    PRO  = "pro"    # Gemma 3 27B (Standard-Tier)
    MAX  = "max"    # Qwen3 235B Thinking (höchste Qualität)
```

---

## Verwandte Dokumente

- [[LLM-Abstraktion]] – Wie Modelle angesprochen werden
- [[Konfiguration]] – AppConfig.tier
- [[Frontend]] – TierSelector-UI-Komponente
- [[Konfiguration]] – Alle Tier-bezogenen Configs
