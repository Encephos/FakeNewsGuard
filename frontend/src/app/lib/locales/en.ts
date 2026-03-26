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
    admin: "Admin",
    profile: "Profile",
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

  // ── Scout Tiers ──────────────────────────────────────────────
  tiers: {
    label: "Analysis Mode",
    lite: "Scout Lite",
    liteDesc: "Fast · Free",
    pro: "Scout Pro",
    proDesc: "Balanced · Gemma",
    max: "Scout Max",
    maxDesc: "Best Quality · Gemma + Qwen",
  },

  // ── Language switcher ─────────────────────────────────────────
  language: {
    label: "Language",
    de: "Deutsch",
    en: "English",
  },

  // ── Admin Dashboard ──────────────────────────────────────────
  admin: {
    title: "Admin Dashboard",
    totalUsers: "Total Users",
    totalAnalyses: "Total Analyses",
    monthAnalyses: "Analyses (30 days)",
    tierDistribution: "Tier Distribution",
    user: "User",
    tier: "Tier",
    analysesTotal: "Analyses",
    analysesMonth: "30 days",
    lastAnalysis: "Last Analysis",
    registered: "Registered",
    noUsers: "No users found.",
    noSearchResults: "No users matching your search.",
    searchUsers: "Search users…",
    noAccess: "Access denied. Admins only.",
    loadError: "Error loading data.",
    loading: "Loading…",
    tab: {
      users: "Users",
      system: "System",
    },
    system: {
      uptime: "Uptime",
      requestsTotal: "Total Requests",
      errors: "Errors",
      avgLatency: "Avg Latency",
      activeJobs: "Active Jobs",
      authAttempts: "Auth Attempts",
      authFailures: "Auth Failures",
      topEndpoints: "Requests by Endpoint",
      endpoint: "Endpoint",
      count: "Requests",
      avgMs: "Avg ms",
      errorRate: "Error Rate",
      recentLogs: "Recent Logs",
      allLevels: "All Levels",
      noLogs: "No logs available.",
      refresh: "Refresh",
    },
  },

  // ── Auth ─────────────────────────────────────────────────────
  auth: {
    login: "Sign in",
    register: "Sign up",
    logout: "Sign out",
    email: "Email",
    password: "Password",
    passwordPlaceholder: "Min. 8 characters",
    displayName: "Display name",
    displayNamePlaceholder: "Optional",
    loginButton: "Sign in",
    registerButton: "Create account",
    loading: "Loading…",
    genericError: "An error occurred.",
    tierNote: "New accounts start with the Lite plan.",
    rememberMe: "Stay signed in",
  },

  // ── Profile ─────────────────────────────────────────────────
  profile: {
    title: "Profile",
    account: "Account",
    plan: "Plan",
    displayNameSection: "Display Name",
    displayNamePlaceholder: "Your display name",
    save: "Save",
    saving: "Saving…",
    saved: "Saved!",
    error: "An error occurred.",
    telegram: "Telegram",
    telegramLinked: "Telegram is linked",
    telegramDescription: "Link your Telegram account to start analyses directly in the chat.",
    connectTelegram: "Connect Telegram",
    unlinkTelegram: "Unlink",
    unlinkConfirm: "Really unlink your Telegram account?",
    yourCode: "Your link code",
    codeExpires: "Valid for {seconds}s",
    step1: "Open the FakeNewsGuard Bot in Telegram",
    step2: "Send the bot the command:",
    step3: "The link will be confirmed automatically",
    newCode: "Generate new code",
  },

  // ── Consent ─────────────────────────────────────────────────────
  consent: {
    notice: "All queries are logged to improve the model and architecture.",
    accept: "Agree & start",
    accepted: "Data processing accepted",
  },

  // ── Graph Explorer ────────────────────────────────────────────
  graph: {
    title: "Network",
    description: "Interactive visualization of relationships between checked claims, sources, and mentioned actors.",
    stats: {
      nodes: "Nodes",
      edges: "Connections",
      claims: "Claims",
      sources: "Sources",
      actors: "Actors",
    },
    searchPlaceholder: "Search graph…",
    typeFilterFilter: "All Types",
    loading: "Loading graph…",
    noData: "No network data available.",
    nodeTypes: {
      CLAIM: "Claim",
      SOURCE: "Source",
      ACTOR: "Actor",
    },
    detail: {
      neighbors: "Connected Nodes ({count})",
      relation: "Relation",
      noNeighbors: "No connections",
      rating: "Rating",
      claimType: "Claim Type",
      totalReferences: "Total references",
      viewAnalysis: "View Analysis",
      relationLabels: {
        supported_by: "Supported by",
        contradicted_by: "Contradicted by",
        mentions: "Mentions",
        related_to: "Related to",
        cites: "Cites",
        referenced_by: "Referenced by",
      },
    },
  },

  // ── Metadata ──────────────────────────────────────────────────
  meta: {
    description: "AI-powered fact-checking for news and claims",
  },
} as const;

export default en;
