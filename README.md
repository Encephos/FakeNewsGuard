# FakeNewsGuard 🛡️

**Multi-Agent-System zur Erkennung von Fake News, Faktenverzerrung und manipulativer Rhetorik.**

Eigenständiges Python-System – läuft komplett unabhängig und nutzt LLM-APIs (Anthropic/OpenAI/Ollama) + Web Search (Tavily/Serper/Brave).

---

## Architektur

```
User-Input
    │
    ▼
┌──────────────────┐
│   ORCHESTRATOR    │──── Steuert den gesamten Workflow
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ CLAIM EXTRACTOR  │──── Zerlegt Text in atomare, prüfbare Behauptungen
└────────┬─────────┘     Klassifiziert: FACTUAL | STATISTICAL | CAUSAL | OPINION | CONTEXTUAL
         │
         ├── FACTUAL Claims ──────────────────▶ FACT CHECKER
         │                                         │
         ├── STATISTICAL Claims ──▶ FACT CHECKER ──┤──▶ NUMBER AUDITOR
         │                                         │
         ├── CAUSAL Claims ───────▶ FACT CHECKER ──┤──▶ RHETORIC ANALYZER
         │                                         │
         └── OPINION Claims ──────▶ (übersprungen)
                                                   │
┌──────────────────┐                               │
│ RHETORIC ANALYZER│◀── Analysiert Gesamttext ─────┘
│ (Gesamttext)     │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   SYNTHESIZER    │──── Aggregiert alles → Gesamtverdikt + Confidence
└──────────────────┘
```

### Warum Multi-Agent?

Ein einzelner Prompt scheitert, weil die Aufgabe **fundamental unterschiedliche Denkweisen** erfordert:

| Agent | Denkweise | Warum eigener Agent? |
|-------|-----------|---------------------|
| Claim Extractor | Textanalyse, Dekomposition | Muss neutral zerlegen, ohne schon zu bewerten |
| Fact Checker | Recherche, Quellenvergleich | Braucht Web Search, Quellenhierarchie |
| Number Auditor | Mathematik, Statistik | Andere Logik: rechnen statt recherchieren |
| Rhetoric Analyzer | Linguistik, Mustererkennung | Analysiert Sprache, nicht Fakten |
| Synthesizer | Aggregation, Gewichtung | Muss verschiedene Perspektiven abwägen |

---

## Setup

### 1. Installieren

```bash
cd faktencheck
pip install -r requirements.txt
```

### 2. API Keys konfigurieren

```bash
cp .env.example .env
# Editiere .env mit deinen Keys
```

**Benötigt:**
- Ein LLM: Anthropic API Key **oder** OpenAI Key **oder** Ollama (lokal)
- Eine Web-Suche: Tavily (empfohlen, $0 Free Tier) **oder** Serper **oder** Brave Search

### 3. Nutzung

```bash
# Direkte Eingabe
python main.py "Die Ausländerkriminalität ist unter der Ampel um 40% gestiegen."

# Aus Datei
python main.py --file rede_auszug.txt

# Interaktiver Modus
python main.py --interactive

# JSON-Output (für Weiterverarbeitung)
python main.py --json "..."

# Mit OpenAI statt Anthropic
python main.py --llm-provider openai --model gpt-4o "..."

# Mit lokalem Ollama
python main.py --llm-provider ollama --model llama3.1 "..."

# Pipe von stdin
echo "Behauptung hier" | python main.py
```

---

## Projektstruktur

```
faktencheck/
├── main.py                  # CLI – Entry Point, Formatierung, Argument-Parsing
├── config.py                # Konfiguration aus .env + Defaults
├── orchestrator.py          # Zentrale Steuerung – routet Claims an Agenten
├── agents/
│   ├── base.py              # BaseAgent – LLM + Search + Logging Interface
│   ├── claim_extractor.py   # Zerlegt Text → atomare Claims (JSON)
│   ├── fact_checker.py      # Verifiziert Claims via Websuche
│   ├── number_auditor.py    # Prüft Zahlen, Statistiken, Rechenlogik
│   ├── rhetoric_analyzer.py # Erkennt Framing, Dog Whistles, Manipulation
│   └── synthesizer.py       # Aggregiert alles → Gesamtbewertung
├── tools/
│   ├── llm.py               # LLM-Abstraction (Anthropic/OpenAI/Ollama)
│   └── web_search.py        # Web Search (Tavily/Serper/Brave)
├── models/
│   └── schemas.py           # Pydantic Models – typisierte Datenstrukturen
├── requirements.txt
└── .env.example
```

---

## Was erkennt das System?

### Zahlen-Tricks
- Verdrehte Prozentangaben und Rechenfehler
- Cherry-Picked Vergleichszeiträume (z.B. Vergleich mit 2015 statt normaler Baseline)
- Wechsel zwischen absoluten und relativen Zahlen zur Dramatisierung
- Fehlende Pro-Kopf-Normalisierung bei Ländervergleichen
- Verwechslung von Kategorien (Tatverdächtige ≠ Verurteilte ≠ Anzeigen)
- Statistische Schwankungen als "Trend" verkauft

### Rhetorische Manipulation
- Loaded Language ("Asylflut", "Messermänner", "Überfremdung")
- Strohmann-Argumente
- Appeal to Fear / Angstrhetorik
- Whataboutism
- Dog Whistles und codierte Sprache
- Implizite Kausalität (Korrelation → Kausalität suggeriert)
- Anekdotische Verallgemeinerung (Einzelfall → systemisches Problem)

### Quellen-Probleme
- Veraltete oder nicht-existente Quellen
- Falsch zitierte Statistiken
- Fehlender Kontext bei technisch korrekten Zahlen

---

## Erweiterung

### Neuen Agenten hinzufügen

1. Erstelle `agents/mein_agent.py` – Klasse erbt von `BaseAgent`
2. Implementiere `execute(self, input_data, context)`
3. Definiere Output-Schema in `models/schemas.py`
4. Registriere im `Orchestrator.__init__()` und `analyze()`

```python
from agents.base import BaseAgent
from models.schemas import MeinErgebnis

SYSTEM_PROMPT = """Du bist ein..."""

class MeinAgent(BaseAgent):
    name = "Mein Agent"
    emoji = "🆕"

    def execute(self, input_data, context=""):
        result = self._llm_json(SYSTEM_PROMPT, str(input_data))
        return MeinErgebnis(**result)
```

### Ideen für Erweiterungen

- **Source Memory**: Bereits geprüfte Claims in SQLite cachen
- **Batch-Mode**: Mehrere Texte parallel prüfen (asyncio)
- **Widerspruchs-Agent**: Vergleicht Aussagen mit früheren desselben Politikers
- **Medien-Agent**: Bilder/Videos auf Manipulation prüfen (multimodales LLM)
- **Web-Interface**: FastAPI + React/Svelte Frontend
- **Telegram-Bot**: Direktes Prüfen von weitergeleiteten Nachrichten
- **Monitoring-Agent**: RSS-Feeds / Telegram-Kanäle automatisch scannen

---

## Design-Entscheidungen

**Warum kein LangChain/CrewAI/AutoGen?**
Minimale Dependencies, volle Kontrolle über Prompt-Qualität und Routing-Logik, einfacher zu debuggen. Die Abstraktionsschicht ist dünn genug, dass man alles versteht.

**Warum Pydantic?**
Typisierte Datenstrukturen zwischen Agenten = weniger Bugs, automatische Validierung des LLM-Outputs, JSON-Serialisierung für CLI und zukünftige API.

**Warum sequentiell statt parallel?**
Der Number Auditor profitiert vom Kontext des Fact Checkers. Parallelisierung ist als Erweiterung einfach nachzurüsten, aber korrektes Routing ist wichtiger als Speed.
