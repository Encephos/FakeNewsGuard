# Telegram-Bot

> Zurück: [[README]] | Siehe auch: [[Datenbank#Nutzer-Datenbank]], [[API]]

`telegram_bot.py` ist ein eigenständiger Telegram-Bot, der Faktencheck-Analysen direkt in Telegram ermöglicht – ohne Web-Frontend.

---

## Starten

```bash
python telegram_bot.py
```

Oder via Docker:
```yaml
# docker-compose.yml
telegram-bot:
  build: .
  command: python telegram_bot.py
  environment:
    - TELEGRAM_BOT_TOKEN=123:ABC...
    - BACKEND_URL=http://backend:8000
```

→ [[Docker]]

---

## Befehle

| Befehl | Beschreibung |
|---|---|
| `/start` | Willkommensnachricht, Registrierung |
| `/analyze <text>` | Text analysieren |
| `/analyze <url>` | URL analysieren |
| `/tier` | Aktuellen Scout-Tier anzeigen |
| `/tier lite\|pro\|max` | Tier wechseln |
| `/help` | Hilfe anzeigen |

---

## Analyse-Ablauf

```
1. Nutzer schickt: /analyze https://spiegel.de/...
2. Bot: "Analyse gestartet... ⏳"
3. Bot schickt Job an Backend (POST /api/analyze)
4. Bot pollt Job-Status (GET /api/jobs/{id}) alle 2 Sekunden
5. Timeout: 32 Minuten
6. Bot schickt formatiertes Ergebnis in MarkdownV2
```

### Ergebnis-Format (MarkdownV2)

```
🔍 *Analyse abgeschlossen*

📊 Bewertung: *IRREFÜHREND* (Konfidenz: 74%)

📝 Zusammenfassung:
Der Artikel enthält mehrere korrekte Grundfakten, rahmt diese jedoch...

⚠️ Wichtigste Korrekturen:
• Die Rentenerhöhung betrug 4,39%, nicht 4%
• Die Inflationsrate bezieht sich auf 2022

🎭 Rhetorik-Techniken: Loaded Language (HIGH), Cherry-Picking (MEDIUM)

🌐 Quellen: destatis.de, tagesschau.de
```

---

## Nutzer-Verwaltung

### Registrierung

Beim ersten `/start`:
1. Bot prüft ob Telegram-ID bereits registriert
2. Falls nicht → neuen Account erstellen (Standard-Tier: `lite`)
3. Willkommensnachricht mit Tier-Info

### Migration von users.json

Beim Start migriert der Bot automatisch alte Nutzer-Daten aus `users.json` in die SQLite-Datenbank (`UserDB`). Einmalig, dann wird `users.json` nicht mehr genutzt.

### Telegram–Web-Verlinkung

Nutzer können ihren Telegram-Account mit einem Web-Account verbinden:

```
1. Web-Frontend: "Telegram verbinden" → 6-stelliger Code
2. Telegram: /link <code>
3. Bot: verknüpft Telegram-ID mit Web-Account
4. Jetzt: gleicher Tier, gleiche Analyse-History
```

→ [[Datenbank#Nutzer-Datenbank]]

---

## Scout-Tier im Bot

Jeder Bot-Nutzer hat einen persönlichen Tier:

```
/tier        → "Dein aktueller Tier: PRO"
/tier max    → "Tier auf MAX gesetzt ✅"
```

Der Tier wird beim API-Aufruf übergeben:
```python
await backend.post("/api/analyze", json={
    "text": text,
    "tier": user.tier
})
```

→ [[Scout-Tiers]]

---

## Technische Details

- **Library:** `python-telegram-bot` (async)
- **Polling-Interval:** 2 Sekunden
- **Job-Timeout:** 32 Minuten (1920s)
- **Format:** MarkdownV2 (Telegram-spezifisch, erfordert Escaping)

---

## Konfiguration

```bash
TELEGRAM_BOT_TOKEN=123456:ABCdef...
BACKEND_URL=http://localhost:8000  # oder http://backend:8000 in Docker
```

→ [[Konfiguration#TelegramConfig]]

---

## Verwandte Dokumente

- [[Datenbank#Nutzer-Datenbank]] – UserDB für Nutzer-Verwaltung
- [[API]] – Backend-Endpunkte die der Bot nutzt
- [[Scout-Tiers]] – Tier-Auswahl im Bot
- [[Docker]] – telegram-bot Service in docker-compose
