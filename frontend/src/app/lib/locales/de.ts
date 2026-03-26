const de = {
  // ── Overall Ratings (backend sends these as overall_rating) ──
  ratings: {
    "Wahr": "Wahr",
    "Größtenteils wahr": "Größtenteils wahr",
    "Irreführend": "Irreführend",
    "Größtenteils falsch": "Größtenteils falsch",
    "Falsch": "Falsch",
  } as Record<string, string>,

  // ── Claim Ratings (FactRating enum values) ────────────────────
  claimRatings: {
    TRUE: "Wahr",
    MOSTLY_TRUE: "Größtenteils wahr",
    MISLEADING: "Irreführend",
    MOSTLY_FALSE: "Größtenteils falsch",
    FALSE: "Falsch",
    UNVERIFIABLE: "Unverif.",
  } as Record<string, string>,

  // ── Layout / Navigation ───────────────────────────────────────
  nav: {
    archive: "Archiv",
    admin: "Admin",
    profile: "Profil",
    newAnalysis: "Neue Analyse",
    backToArchive: "Zurück zum Archiv",
  },

  // ── Main page ─────────────────────────────────────────────────
  input: {
    placeholder: "Text, Artikel, Behauptung oder Link einfügen…",
    submitLabel: "Analyse starten",
    shiftEnterHint: "Shift+Enter für Zeilenumbruch · Links werden automatisch erkannt",
    contentAutoExtract: "Inhalt wird automatisch extrahiert",
    removeLinkLabel: "Link entfernen",
  },

  // ── Result Display ────────────────────────────────────────────
  result: {
    confidence: "Konfidenz",
    claimsDetail: "Claims im Detail",
    corrections: "Korrekturen",
    rhetoric: "Manipulationstechniken",
    fairness: "Was stimmt",
    sources: "Quellen",
    evidence: "Evidenz",
    correction: "Korrektur",
    missingContext: "Kontext fehlt",
    numberManipulation: "Zahlenmanipulation",
  },

  // ── Left Panel ────────────────────────────────────────────────
  leftPanel: {
    phases: "Phasen",
    phaseExtraction: "Extraktion",
    phaseFactCheck: "Fact-Check",
    phaseRhetoric: "Rhetorik",
    phaseSynthesis: "Synthese",
  },

  // ── Right Panel ───────────────────────────────────────────────
  rightPanel: {
    progress: "Fortschritt",
    steps: "Schritte",
    verdict: "Urteil",
    overview: "Überblick",
    distribution: "Verteilung",
    claims: "Claims",
    techniques: "Techniken",
    correctionsLabel: "Korrekturen",
    sourcesLabel: "Quellen",
    ratingGroupTrue: "Wahr",
    ratingGroupMisleading: "Irreführend",
    ratingGroupUnverifiable: "Unverif.",
    ratingGroupFalse: "Falsch",
  },

  // ── Archive ───────────────────────────────────────────────────
  archive: {
    title: "Archiv",
    pastAnalyses: "{count} vergangene Analyse{plural}",
    allRatings: "Alle Bewertungen",
    searchPlaceholder: "Suche in Titel, Zusammenfassung, URL…",
    loading: "Wird geladen…",
    noResults: "Keine Ergebnisse für diese Filter.",
    emptyState: "Noch keine Analysen im Archiv. Starte eine neue Analyse!",
    deleteConfirm: "Diesen Eintrag wirklich löschen?",
    deleteLabel: "Löschen",
    prev: "Zurück",
    next: "Weiter",
    analysis: "Analyse",
    confidence: "Konfidenz",
    claim: "Claim",
    claimPlural: "Claims",
    technique: "Technik",
    techniquePlural: "Techniken",
  },

  // ── Time ──────────────────────────────────────────────────────
  time: {
    justNow: "Gerade eben",
    minutesAgo: "vor {n} Min.",
    hoursAgo: "vor {n} Std.",
    daysAgo: "vor {n} Tagen",
  },

  // ── Platform labels ───────────────────────────────────────────
  platforms: {
    twitter: "Twitter / X",
    threads: "Threads",
    instagram: "Instagram",
    facebook: "Facebook",
    youtube: "YouTube",
    article: "Artikel",
    fallback: "Link",
  },

  // ── API errors ────────────────────────────────────────────────
  errors: {
    extractionFailed: "Extraktion fehlgeschlagen",
    apiFailed: "API-Fehler",
    noJobId: "Keine Job-ID vom Server erhalten.",
    jobNotFound: "Job nicht gefunden — möglicherweise abgelaufen.",
    pollFailed: "Poll-Fehler",
    noResult: "Kein Ergebnis vom Server erhalten.",
    analysisFailed: "Analyse fehlgeschlagen.",
    timeout: "Zeitüberschreitung: Analyse dauert zu lange.",
    loadFailed: "Fehler beim Laden",
  },

  // ── Scout Tiers ──────────────────────────────────────────────
  tiers: {
    label: "Analyse-Modus",
    lite: "Scout Lite",
    liteDesc: "Schnell · Kostenlos",
    pro: "Scout Pro",
    proDesc: "Ausgewogen · Gemma",
    max: "Scout Max",
    maxDesc: "Beste Qualität · Gemma + Qwen",
  },

  // ── Language switcher ─────────────────────────────────────────
  language: {
    label: "Sprache",
    de: "Deutsch",
    en: "English",
  },

  // ── Admin Dashboard ──────────────────────────────────────────
  admin: {
    title: "Admin Dashboard",
    totalUsers: "Nutzer gesamt",
    totalAnalyses: "Analysen gesamt",
    monthAnalyses: "Analysen (30 Tage)",
    tierDistribution: "Tier-Verteilung",
    user: "Nutzer",
    tier: "Tier",
    analysesTotal: "Analysen",
    analysesMonth: "30 Tage",
    lastAnalysis: "Letzte Analyse",
    registered: "Registriert",
    noUsers: "Keine Nutzer vorhanden.",
    noSearchResults: "Keine Nutzer gefunden.",
    searchUsers: "Nutzer suchen…",
    noAccess: "Zugriff verweigert. Nur Admins.",
    loadError: "Fehler beim Laden der Daten.",
    loading: "Wird geladen…",
    tab: {
      users: "Nutzer",
      system: "System",
    },
    system: {
      uptime: "Uptime",
      requestsTotal: "Anfragen gesamt",
      errors: "Fehler",
      avgLatency: "Ø Latenz",
      activeJobs: "Aktive Jobs",
      authAttempts: "Auth-Versuche",
      authFailures: "Auth-Fehler",
      topEndpoints: "Anfragen nach Endpoint",
      endpoint: "Endpoint",
      count: "Anfragen",
      avgMs: "Ø ms",
      errorRate: "Fehlerrate",
      recentLogs: "Aktuelle Logs",
      allLevels: "Alle Level",
      noLogs: "Keine Logs vorhanden.",
      refresh: "Aktualisieren",
    },
  },

  // ── Auth ─────────────────────────────────────────────────────
  auth: {
    login: "Anmelden",
    register: "Registrieren",
    logout: "Abmelden",
    email: "E-Mail",
    password: "Passwort",
    passwordPlaceholder: "Min. 8 Zeichen",
    displayName: "Anzeigename",
    displayNamePlaceholder: "Optional",
    loginButton: "Anmelden",
    registerButton: "Konto erstellen",
    loading: "Wird geladen…",
    genericError: "Ein Fehler ist aufgetreten.",
    tierNote: "Neue Konten starten mit dem Lite-Plan.",
    rememberMe: "Angemeldet bleiben",
  },

  // ── Profile ─────────────────────────────────────────────────
  profile: {
    title: "Profil",
    account: "Account",
    plan: "Plan",
    displayNameSection: "Anzeigename",
    displayNamePlaceholder: "Dein Anzeigename",
    save: "Speichern",
    saving: "Wird gespeichert…",
    saved: "Gespeichert!",
    error: "Ein Fehler ist aufgetreten.",
    telegram: "Telegram",
    telegramLinked: "Telegram ist verknüpft",
    telegramDescription: "Verknüpfe dein Telegram-Konto, um Analysen direkt im Chat zu starten.",
    connectTelegram: "Telegram verbinden",
    unlinkTelegram: "Verknüpfung aufheben",
    unlinkConfirm: "Telegram-Verknüpfung wirklich aufheben?",
    yourCode: "Dein Verknüpfungscode",
    codeExpires: "Gültig für {seconds}s",
    step1: "Öffne den FakeNewsGuard Bot in Telegram",
    step2: "Sende dem Bot den Befehl:",
    step3: "Die Verknüpfung wird automatisch bestätigt",
    newCode: "Neuen Code generieren",
  },

  // ── Consent ─────────────────────────────────────────────────────
  consent: {
    notice: "Alle Anfragen werden protokolliert, um das Modell und die Architektur zu verbessern.",
    accept: "Zustimmen & starten",
    accepted: "Datenverarbeitung zugestimmt",
  },

  // ── Graph Explorer ────────────────────────────────────────────
  graph: {
    title: "Netzwerk",
    description: "Interaktive Visualisierung der Beziehungen zwischen überprüften Claims, Quellen und erwähnten Akteuren.",
    stats: {
      nodes: "Knoten",
      edges: "Verbindungen",
      claims: "Claims",
      sources: "Quellen",
      actors: "Akteure",
    },
    searchPlaceholder: "Suche im Graphen…",
    typeFilterFilter: "Alle Typen",
    loading: "Graph wird geladen…",
    noData: "Keine Netzwerkdaten verfügbar.",
    nodeTypes: {
      CLAIM: "Claim",
      SOURCE: "Quelle",
      ACTOR: "Akteur",
    },
    detail: {
      neighbors: "Verbundene Knoten ({count})",
      relation: "Beziehung",
      noNeighbors: "Keine Verbindungen",
      rating: "Bewertung",
      claimType: "Art des Claims",
      totalReferences: "Erwähnungen gesamt",
      viewAnalysis: "Analyse ansehen",
      relationLabels: {
        supported_by: "Gestützt von",
        contradicted_by: "Widersprochen von",
        mentions: "Erwähnt",
        related_to: "Verwandt mit",
        cites: "Zitiert",
        referenced_by: "Referenziert von",
      },
    },
  },

  // ── Metadata ──────────────────────────────────────────────────
  meta: {
    description: "KI-gestützter Faktencheck für Nachrichten und Behauptungen",
  },
} as const;

export default de;
