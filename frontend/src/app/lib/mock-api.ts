import { Step, AnalysisResult } from "./types";

const MOCK_STEPS: Omit<Step, "id" | "timestamp" | "status">[] = [
  {
    phase: "Phase 1",
    agent: "Claim Extractor",
    emoji: "search",
    message: "Claims werden extrahiert...",
  },
  {
    phase: "Phase 1",
    agent: "Claim Extractor",
    emoji: "search",
    message: "6 Claims extrahiert, davon 3 implizite",
  },
  {
    phase: "Phase 2",
    agent: "Fact Checker",
    emoji: "check",
    message: "Prüfe: Satellitendaten der NASA zeigen Meereiszuwachs...",
  },
  {
    phase: "Phase 2",
    agent: "Fact Checker",
    emoji: "check",
    message: "Websuche läuft (3 Queries)...",
  },
  {
    phase: "Phase 2",
    agent: "Fact Checker",
    emoji: "check",
    message: "Claim C2: UNVERIFIABLE",
  },
  {
    phase: "Phase 2",
    agent: "Fact Checker",
    emoji: "check",
    message: "Prüfe: Meereiszuwachs betrug über 1,2 Mio km²...",
  },
  {
    phase: "Phase 2",
    agent: "Number Auditor",
    emoji: "hash",
    message: "Zahlenprüfung C3: CATEGORY_ERROR erkannt",
  },
  {
    phase: "Phase 2",
    agent: "Fact Checker",
    emoji: "check",
    message: "Prüfe: Zuwachs einer der größten der letzten Jahrzehnte...",
  },
  {
    phase: "Phase 2",
    agent: "Fact Checker",
    emoji: "check",
    message: "Claim C4: FALSE",
  },
  {
    phase: "Phase 3",
    agent: "Rhetoric Analyzer",
    emoji: "theater",
    message: "Rhetorische Analyse gestartet...",
  },
  {
    phase: "Phase 3",
    agent: "Rhetoric Analyzer",
    emoji: "theater",
    message: "3 Manipulationstechniken erkannt",
  },
  {
    phase: "Phase 4",
    agent: "Synthesizer",
    emoji: "chart",
    message: "Erstelle Gesamtbewertung...",
  },
  {
    phase: "Phase 4",
    agent: "Synthesizer",
    emoji: "chart",
    message: "Analyse abgeschlossen",
  },
];

const MOCK_RESULT: AnalysisResult = {
  overall_rating: "Irreführend",
  confidence: 85,
  summary:
    "Der Text behauptet, dass das antarktische Meereis im Juli rekordverdächtig gewachsen sei und dies die Klimatheorie widerlege. Zwar ist ein saisonales Wachstum im Winter korrekt, jedoch zeigen Satellitendaten ein signifikantes Defizit gegenüber dem Durchschnitt, keinen Rekordzuwachs. Die Verallgemeinerung eines einzelnen Monats auf den langfristigen Trend ist wissenschaftlich nicht haltbar.",
  claims: [
    {
      id: "C2",
      text: "Satellitendaten der NASA zeigen einen Meereiszuwachs in der Antarktis im vergangenen Juli.",
      type: "FACTUAL",
      rating: "UNVERIFIABLE",
      evidence:
        "Die NASA veröffentlicht Satellitendaten zur Meereisausdehnung, jedoch konnte der spezifische Zuwachs für den genannten Zeitraum nicht verifiziert werden.",
      correction: "",
      missing_context:
        "Saisonales Wachstum im antarktischen Winter (Juli) ist ein normaler Vorgang und kein Indikator gegen den Klimawandel.",
      sources: [
        "https://correctiv.org/faktencheck/2023/04/05/klimawandel-meeresspiegel-eismassen/",
      ],
    },
    {
      id: "C3",
      text: "Der Meereiszuwachs in der Antarktis im vergangenen Juli betrug über 1,2 Millionen Quadratkilometer.",
      type: "STATISTICAL",
      rating: "UNVERIFIABLE",
      evidence:
        "Die genannte Zahl von 1,2 Millionen km² lässt sich in keiner offiziellen Quelle bestätigen.",
      correction:
        "Daten zeigen ein Defizit von ca. 1,4-1,6 Mio km² gegenüber dem langjährigen Durchschnitt.",
      missing_context:
        "Die Meereisausdehnung lag deutlich unter dem Durchschnitt, nicht darüber.",
      sources: [
        "https://ardalpha.de/wissen/umwelt/klima/eisschmelze-antarktis-100.html",
      ],
      number_audit: {
        manipulation: "CATEGORY_ERROR",
        calculation:
          "Behauptung: +1,2 Mio km² Zuwachs. Tatsächlich: -1,4 bis -1,6 Mio km² Defizit gegenüber dem Durchschnitt.",
        correct_value:
          "Die Meereisausdehnung lag ca. 1,4-1,6 Mio km² UNTER dem langjährigen Durchschnitt.",
      },
    },
    {
      id: "C4",
      text: "Dieser Zuwachs ist einer der größten der letzten Jahrzehnte.",
      type: "STATISTICAL",
      rating: "FALSE",
      evidence:
        "Laut NSIDC-Daten lag die Meereisausdehnung im Juli deutlich unter dem langjährigen Mittel. Ein Rekordzuwachs ist nicht belegt.",
      correction:
        "Die Meereisausdehnung in der Antarktis zeigt in den letzten Jahren einen rückläufigen Trend, nicht einen Zuwachs.",
      missing_context:
        "Der langfristige Trend des antarktischen Meereises ist seit 2016 rückläufig.",
      sources: [
        "https://correctiv.org/faktencheck/2023/04/05/klimawandel-meeresspiegel-eismassen/",
      ],
    },
    {
      id: "C5",
      text: "Dieser Zuwachs widerlegt die Theorie der stetig schmelzenden Polkappen völlig.",
      type: "CAUSAL",
      rating: "MOSTLY_FALSE",
      evidence:
        "Ein einzelner saisonaler Datenpunkt kann keine langfristige wissenschaftliche Theorie widerlegen.",
      correction:
        "Saisonales Wachstum im Winter ist normal und widerspricht nicht dem langfristigen Schmelztrend.",
      missing_context:
        "Die Arktis verliert seit Jahrzehnten massiv an Eis. Der antarktische Eisschild verliert jährlich ca. 150 Mrd. Tonnen Masse.",
      sources: [
        "https://mdr.de/wissen/umwelt-klima/klimawandel-polkappen-schmelzen-100.html",
      ],
    },
    {
      id: "C6",
      text: "Es existiert eine Theorie über stetig schmelzende Polkappen.",
      type: "FACTUAL",
      rating: "TRUE",
      evidence:
        "Dies ist wissenschaftlicher Konsens, gestützt durch IPCC-Berichte und umfangreiche Satellitenmessungen.",
      correction:
        "Es handelt sich nicht um eine 'Theorie', sondern um beobachtete Tatsachen, bestätigt durch tausende Studien.",
      missing_context: "",
      sources: [
        "https://mdr.de/wissen/umwelt-klima/klimawandel-polkappen-schmelzen-100.html",
      ],
    },
  ],
  rhetoric: [
    {
      name: "Loaded Language",
      severity: "MEDIUM",
      description:
        "Emotional negativ besetzter Begriff zur Diskreditierung von Aktivisten",
      example: '"Panikmache"',
    },
    {
      name: "Cherry-Picking",
      severity: "HIGH",
      description:
        "Selektive Nutzung eines einzelnen Zeitraums und einer Region zur Verallgemeinerung gegen globale Trends",
      example: '"Meereis in der Antarktis im vergangenen Juli"',
    },
    {
      name: "False Equivalence",
      severity: "HIGH",
      description:
        "Einzelner Datensatz wird als vollständige Widerlegung komplexer wissenschaftlicher Erkenntnisse dargestellt",
      example: '"widerlegt die Theorie ... völlig"',
    },
  ],
  corrections: [
    "Die Behauptung eines Zuwachses von 1,2 Mio km² ist falsch; Daten zeigen ein Defizit von ca. 1,4-1,6 Mio km² gegenüber dem langjährigen Durchschnitt.",
    "Ein saisonales Wachstum im Juli (Winterhalbjahr) widerlegt nicht den langfristigen Trend des Eisschmelzens.",
  ],
  fairness: [
    "Es ist korrekt, dass das antarktische Meereis im Juli aufgrund der Jahreszeit saisonal wächst.",
    "Satellitendaten werden tatsächlich zur Überwachung der Eismassen genutzt.",
  ],
  sources: [
    "https://correctiv.org/faktencheck/2023/04/05/klimawandel-meeresspiegel-eismassen/",
    "https://mdr.de/wissen/umwelt-klima/klimawandel-polkappen-schmelzen-100.html",
    "https://ardalpha.de/wissen/umwelt/klima/eisschmelze-antarktis-100.html",
  ],
};

/**
 * Simulates a streaming analysis with step-by-step progress.
 * Replace this with real API calls when backend is ready.
 */
export async function analyzeArticle(
  _text: string,
  onStep: (step: Step) => void,
): Promise<AnalysisResult> {
  for (let i = 0; i < MOCK_STEPS.length; i++) {
    const step = MOCK_STEPS[i];
    const id = `step-${i}`;
    const timestamp = Date.now();

    // Emit as running
    onStep({ ...step, id, timestamp, status: "running" });

    // Simulate processing time
    await new Promise((r) => setTimeout(r, 400 + Math.random() * 600));

    // Emit as done
    onStep({ ...step, id, timestamp, status: "done" });
  }

  return MOCK_RESULT;
}
