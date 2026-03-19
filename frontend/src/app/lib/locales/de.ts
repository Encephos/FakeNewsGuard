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

  // ── Language switcher ─────────────────────────────────────────
  language: {
    label: "Sprache",
    de: "Deutsch",
    en: "English",
  },

  // ── Metadata ──────────────────────────────────────────────────
  meta: {
    description: "KI-gestützter Faktencheck für Nachrichten und Behauptungen",
  },
} as const;

export default de;
