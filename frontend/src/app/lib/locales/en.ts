const en = {
  // ── Overall Ratings (backend sends these as overall_rating) ──
  // Keys must match what the backend sends for the active locale
  ratings: {
    "True": "True",
    "Mostly true": "Mostly true",
    "Mixed": "Mixed",
    "Misleading": "Misleading",
    "Mostly false": "Mostly false",
    "False": "False",
    // Also support German keys as fallback (backend might still send German)
    "Wahr": "True",
    "Größtenteils wahr": "Mostly true",
    "Irreführend": "Misleading",
    "Größtenteils falsch": "Mostly false",
    "Falsch": "False",
  } as Record<string, string>,

  // ── Claim Ratings (FactRating enum values) ────────────────────
  claimRatings: {
    TRUE: "True",
    MOSTLY_TRUE: "Mostly true",
    MISLEADING: "Misleading",
    MOSTLY_FALSE: "Mostly false",
    FALSE: "False",
    UNVERIFIABLE: "Unverif.",
  } as Record<string, string>,

  // ── Layout / Navigation ───────────────────────────────────────
  nav: {
    archive: "Archive",
    newAnalysis: "New Analysis",
    backToArchive: "Back to Archive",
  },

  // ── Main page ─────────────────────────────────────────────────
  input: {
    placeholder: "Paste text, article, claim, or link…",
    submitLabel: "Start analysis",
    shiftEnterHint: "Shift+Enter for line break · Links are auto-detected",
    contentAutoExtract: "Content will be extracted automatically",
    removeLinkLabel: "Remove link",
  },

  // ── Result Display ────────────────────────────────────────────
  result: {
    confidence: "Confidence",
    claimsDetail: "Claims in Detail",
    corrections: "Corrections",
    rhetoric: "Manipulation Techniques",
    fairness: "What's Correct",
    sources: "Sources",
    evidence: "Evidence",
    correction: "Correction",
    missingContext: "Missing Context",
    numberManipulation: "Number Manipulation",
  },

  // ── Left Panel ────────────────────────────────────────────────
  leftPanel: {
    phases: "Phases",
    phaseExtraction: "Extraction",
    phaseFactCheck: "Fact-Check",
    phaseRhetoric: "Rhetoric",
    phaseSynthesis: "Synthesis",
  },

  // ── Right Panel ───────────────────────────────────────────────
  rightPanel: {
    progress: "Progress",
    steps: "Steps",
    verdict: "Verdict",
    overview: "Overview",
    distribution: "Distribution",
    claims: "Claims",
    techniques: "Techniques",
    correctionsLabel: "Corrections",
    sourcesLabel: "Sources",
    ratingGroupTrue: "True",
    ratingGroupMisleading: "Misleading",
    ratingGroupUnverifiable: "Unverif.",
    ratingGroupFalse: "False",
  },

  // ── Archive ───────────────────────────────────────────────────
  archive: {
    title: "Archive",
    pastAnalyses: "{count} past analysis{plural}",
    allRatings: "All ratings",
    searchPlaceholder: "Search in title, summary, URL…",
    loading: "Loading…",
    noResults: "No results for these filters.",
    emptyState: "No analyses in the archive yet. Start a new analysis!",
    deleteConfirm: "Really delete this entry?",
    deleteLabel: "Delete",
    prev: "Previous",
    next: "Next",
    analysis: "Analysis",
    confidence: "Confidence",
    claim: "Claim",
    claimPlural: "Claims",
    technique: "Technique",
    techniquePlural: "Techniques",
  },

  // ── Time ──────────────────────────────────────────────────────
  time: {
    justNow: "Just now",
    minutesAgo: "{n} min ago",
    hoursAgo: "{n} hrs ago",
    daysAgo: "{n} days ago",
  },

  // ── Platform labels ───────────────────────────────────────────
  platforms: {
    twitter: "Twitter / X",
    threads: "Threads",
    instagram: "Instagram",
    facebook: "Facebook",
    youtube: "YouTube",
    article: "Article",
    fallback: "Link",
  },

  // ── API errors ────────────────────────────────────────────────
  errors: {
    extractionFailed: "Extraction failed",
    apiFailed: "API error",
    noJobId: "No job ID received from server.",
    jobNotFound: "Job not found — may have expired.",
    pollFailed: "Poll error",
    noResult: "No result received from server.",
    analysisFailed: "Analysis failed.",
    timeout: "Timeout: Analysis is taking too long.",
    loadFailed: "Error loading data",
  },

  // ── Language switcher ─────────────────────────────────────────
  language: {
    label: "Language",
    de: "Deutsch",
    en: "English",
  },

  // ── Metadata ──────────────────────────────────────────────────
  meta: {
    description: "AI-powered fact-checking for news and claims",
  },
} as const;

export default en;
