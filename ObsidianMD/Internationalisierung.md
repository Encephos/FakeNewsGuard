# Internationalisierung (i18n)

> Zurück: [[README]] | Siehe auch: [[Konfiguration]], [[Frontend]]

FakeNewsGuard unterstützt **Deutsch und Englisch** – sowohl im Backend (Agent-Prompts) als auch im Frontend (UI-Strings).

---

## Backend i18n (`i18n/`)

### Architektur

```
i18n/
├── __init__.py        # Loader + t() Funktion
└── locales/
    ├── de.py          # Deutsche Strings (Standard)
    └── en.py          # Englische Strings (Fallback)
```

### Verwendung

```python
from i18n import t, set_default_locale

# Locale setzen (einmalig beim Start):
set_default_locale("de")

# String abrufen:
prompt = t("agents.fact_checker.system_prompt")
message = t("agents.claim_extractor.user_message").format(text=text)
```

### Dot-Notation

Keys verwenden Dot-Notation für verschachtelte Strukturen:

```python
# de.py (vereinfacht):
LOCALES = {
    "agents": {
        "fact_checker": {
            "system_prompt": "Du bist ein Faktenprüfer...",
            "user_message": "Prüfe folgende Behauptung: {claim}",
        },
        "claim_extractor": {
            "system_prompt": "Extrahiere alle Behauptungen...",
        },
    },
    "errors": {
        "input_too_long": "Eingabe überschreitet {max} Zeichen.",
    }
}
```

### Fallback-Kette

```
Angefordertes Locale (z.B. "en")
    → Falls Key fehlt: Standard-Locale ("de")
    → Falls Key immer noch fehlt: Key selbst zurückgeben
```

Das verhindert KeyErrors auch bei unvollständigen Übersetzungen.

### Locale-Wechsel zur Laufzeit

```python
set_default_locale("en")
# Alle nachfolgenden t() Aufrufe nutzen Englisch
```

Das ermöglicht pro-Request-Locale bei mehrsprachiger API.

---

## Übersetzte Inhalte (Backend)

Alle **Agent-Prompts** sind übersetzt:
- System-Prompts für alle 6 Agenten
- User-Messages mit Platzhaltern
- Fehler-Nachrichten
- Bewertungs-Labels

Das ist wichtig, weil LLMs **besser auf deutsche Prompts reagieren** wenn sie deutschen Text analysieren.

---

## Frontend i18n (`frontend/src/app/lib/`)

### Architektur

```
lib/
├── i18n.tsx           # React-Context + useI18n Hook
└── locales/
    ├── de.ts          # Deutsche UI-Strings
    └── en.ts          # Englische UI-Strings
```

### useI18n Hook

```typescript
const { t, locale, setLocale } = useI18n()

// Verwendung in Komponenten:
<button>{t("analysis.start_button")}</button>
// → "Analysieren" (DE) oder "Analyze" (EN)
```

### Locale-Persistenz

Die gewählte Sprache wird in `localStorage` gespeichert und beim Reload wiederhergestellt.

### Übersetzte Frontend-Strings

- Alle Button-Labels
- Navigationsmenü
- Fehlermeldungen
- Rating-Labels (`"Verlässlich"` / `"Reliable"`)
- Agenten-Beschreibungen
- Lade-Nachrichten

### LanguageSwitcher-Komponente

Wechselt zwischen DE/EN in der Header-Leiste. Ändert sofort alle UI-Strings ohne Seitenreload (React-State).

→ [[Frontend#Header]]

---

## Zusammenspiel Backend ↔ Frontend

Der Nutzer wählt im Frontend eine Sprache. Diese wird an die API mitgesendet:

```http
POST /api/analyze
{ "text": "...", "language": "en" }
```

Der Orchestrator setzt die Locale für diesen Request:
```python
set_default_locale(request.language or config.language)
```

Dadurch antworten alle Agenten in der gewählten Sprache – sowohl Prompts als auch Ausgaben.

---

## Hinzufügen einer neuen Sprache

1. `i18n/locales/fr.py` erstellen (Kopie von `de.py`, alle Strings übersetzen)
2. `frontend/src/app/lib/locales/fr.ts` erstellen
3. `LanguageSwitcher`-Komponente um `"fr"` erweitern
4. `set_default_locale("fr")` testen

---

## Verwandte Dokumente

- [[Konfiguration]] – `AppConfig.language`
- [[Frontend]] – LanguageSwitcher, useI18n
- [[Agenten]] – Alle Agent-Prompts nutzen t()
