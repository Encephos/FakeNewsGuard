# Frontend

> Zurück: [[README]] | Siehe auch: [[API]], [[Scout-Tiers]], [[Internationalisierung]]

Das Frontend ist eine **Next.js 14-App** mit TypeScript und React. Es kommuniziert ausschliesslich mit der [[API|FastAPI-API]] via `fetch`.

---

## Tech-Stack

| Technologie | Version | Zweck |
|---|---|---|
| Next.js | 14+ | App Router, SSR/CSR |
| TypeScript | 5+ | Typsicherheit |
| React | 18 | UI-Komponenten |
| Tailwind CSS | 3 | Styling |

---

## Verzeichnisstruktur

```
frontend/src/app/
├── page.tsx              # Haupt-Analyse-Seite
├── archiv/               # Analyse-Archiv
│   └── page.tsx
├── admin/                # Admin-Panel (nur für Admins)
│   └── page.tsx
├── login/                # Login & Registrierung
│   └── page.tsx
├── profile/              # Nutzerprofil & Einstellungen
│   └── page.tsx
├── components/           # Wiederverwendbare Komponenten
│   ├── LandingPage.tsx
│   ├── AnalysisPage.tsx
│   ├── Providers.tsx
│   ├── ChatInput.tsx
│   ├── Header.tsx
│   ├── ResultDisplay.tsx
│   ├── ReasoningSteps.tsx
│   ├── NeuralBrain.tsx
│   ├── TierSelector.tsx
│   ├── ThemeToggle.tsx
│   ├── LanguageSwitcher.tsx
│   ├── LeftPanel.tsx
│   └── RightPanel.tsx
├── lib/
│   ├── api.ts            # API-Client
│   ├── auth.tsx          # Auth-Context
│   ├── i18n.tsx          # i18n-Hook
│   ├── types.ts          # TypeScript-Typen
│   └── locales/
│       ├── de.ts
│       └── en.ts
└── globals.css
```

---

## Seiten

### Haupt-Analyse (`page.tsx`)

`page.tsx` rendert bedingt: `user ? <AnalysisPage /> : <LandingPage />`

- **LandingPage** (`components/LandingPage.tsx`) – Startseite für nicht eingeloggte Nutzer
- **AnalysisPage** (`components/AnalysisPage.tsx`) – Hauptfunktionalität für eingeloggte Nutzer:
  1. **Eingabe**: Textarea mit URL-Erkennung (ChatInput)
  2. **Tier-Auswahl**: Lite / Pro / Max (TierSelector)
  3. **Live-Analyse**: Schritte werden in Echtzeit angezeigt (ReasoningSteps)
  4. **Ergebnis**: Detaillierte Darstellung (ResultDisplay)

**Job-Persistenz:** Laufende Jobs werden in `localStorage` gespeichert. Bei Seiten-Reload wird der Job automatisch weiter gepollt (10-Minuten-Fenster).

**Consent-Management**: Vor der ersten Analyse wird der Nutzer nach Logging-Einwilligung gefragt.

### Admin-Panel (`admin/page.tsx`)

Nur für Nutzer mit `is_admin = true`:
- Nutzerliste mit Usage-Statistiken
- System-Metriken (Requests, Fehler, Latenz)
- Log-Viewer (Ring-Buffer der letzten 500 Einträge)

### Login / Registrierung (`login/page.tsx`)

- Email + Passwort
- JWT Access Token wird im Speicher gehalten
- Refresh Token in HttpOnly-Cookie (automatisch via `fetch`)
- Nach Login: Weiterleitung zur Haupt-Seite

### Profil (`profile/page.tsx`)

- Aktueller Tier anzeigen / ändern
- Telegram-Bot verbinden (6-stelliger Code)
- Usage-History (letzte Analysen)
- Logging-Einwilligung verwalten

### Archiv (`archiv/page.tsx`)

- Alle abgeschlossenen Analysen (paginiert)
- Volltextsuche
- Filter nach Rating
- Klick → Detailansicht

---

## Komponenten

### ChatInput

Textarea mit intelligenter URL-Erkennung:
- Erkennt URLs automatisch
- Zeigt Plattform-Icon (YouTube, Twitter, Spiegel …)
- Keyboard-Shortcut: Enter (Submit), Shift+Enter (Zeilenumbruch)

### Header

- FakeNewsGuard-Logo + Branding
- Sprach-Umschalter (DE/EN)
- Theme-Toggle (Light/Dark)
- Login/Profil-Link

### TierSelector

Wahl zwischen Lite / Pro / Max:
- Beschreibung jedes Tiers
- Visueller Hinweis auf Geschwindigkeit vs. Qualität

→ [[Scout-Tiers]]

### ReasoningSteps

Live-Visualisierung der Analyse-Schritte:
- Spinner für laufende Schritte
- Checkmark für abgeschlossene
- Fehler-Icon für fehlgeschlagene

### ResultDisplay

Vollständige Ergebnis-Darstellung:
- Overall-Rating mit Farbkodierung
- Konfidenzscore-Balken
- Expandierbare Claim-Karten
- Rhetorik-Techniken mit Severity-Badges
- Quellen-Liste mit Tier-Icons
- Fairness-Anmerkungen
- Korrekturen-Liste

### NeuralBrain

Animierte Ladeanimation während der Analyse.

---

## API-Client (`lib/api.ts`)

```typescript
async function analyzeArticle(
    text: string,
    onStep: (step: Step) => void,
    onJobId?: (jobId: string) => void,
    onExtractedContent?: (content: ExtractedContent) => void,
    url?: string,
    tier?: "lite" | "pro" | "max"
): Promise<SynthesisResult>
```

**Flow:**
1. Falls `url`: erst `POST /api/extract`
2. `POST /api/analyze` → `job_id`
3. Polling-Loop: `GET /api/jobs/{job_id}` alle 2s (960 Versuche = 32 Min.)
4. Steps werden via `onStep` Callback weitergeleitet
5. Bei `status === "done"` → `SynthesisResult` zurückgeben

**Auth:** JWT Access Token wird automatisch in `Authorization`-Header eingefügt.

---

## Auth-Context (`lib/auth.tsx`)

```typescript
const { user, token, login, logout, isAdmin } = useAuth()
```

- Beim App-Start: Refresh Token → neuer Access Token
- `user` enthält: id, email, tier, is_admin
- `isAdmin` für bedingte Darstellung von Admin-Links

---

## i18n (`lib/i18n.tsx`)

```typescript
const { t, locale, setLocale } = useI18n()
t("analysis.start_button")  // → "Analysieren" (DE) oder "Analyze" (EN)
```

→ [[Internationalisierung]]

---

## TypeScript-Typen (`lib/types.ts`)

Spiegeln die Python-Schemas wider:

```typescript
interface SynthesisResult {
    overall_rating: OverallRating
    confidence: number
    summary: string
    claims_analysis: FactCheckResult[]
    number_audits: NumberAuditResult[]
    key_corrections: string[]
    manipulation_techniques: RhetoricTechnique[]
    fairness_notes: string
    sources: string[]
    analysis_errors: string[]
}
```

---

## Verwandte Dokumente

- [[API]] – Backend-Endpunkte die das Frontend nutzt
- [[Scout-Tiers]] – Tier-Auswahl im Frontend
- [[Internationalisierung]] – i18n im Detail
- [[Datenmodelle]] – Python-Schemas (Mirror der TS-Types)
