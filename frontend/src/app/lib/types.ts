export type OverallRating =
  | "Wahr"
  | "Größtenteils wahr"
  | "Irreführend"
  | "Größtenteils falsch"
  | "Falsch";

export type ClaimType =
  | "FACTUAL"
  | "STATISTICAL"
  | "CAUSAL"
  | "OPINION"
  | "CONTEXTUAL"
  | "PREDICTION";

export type FactRating =
  | "TRUE"
  | "MOSTLY_TRUE"
  | "MISLEADING"
  | "MOSTLY_FALSE"
  | "FALSE"
  | "UNVERIFIABLE";

export type ManipulationType =
  | "NONE"
  | "CHERRY_PICKING"
  | "CATEGORY_ERROR"
  | "MISLEADING_COMPARISON"
  | "FALSE_PRECISION"
  | "SCALE_DISTORTION";

export interface Step {
  id: string;
  phase: string;
  agent: string;
  emoji: string;
  message: string;
  status: "running" | "done" | "error";
  timestamp: number;
}

export interface ClaimResult {
  id: string;
  text: string;
  type: ClaimType;
  rating: FactRating;
  evidence: string;
  correction: string;
  missing_context: string;
  sources: string[];
  number_audit?: {
    manipulation: ManipulationType;
    calculation: string;
    correct_value: string;
  };
}

export interface RhetoricTechnique {
  name: string;
  severity: "LOW" | "MEDIUM" | "HIGH";
  description: string;
  example: string;
}

export interface AnalysisResult {
  overall_rating: OverallRating;
  confidence: number;
  summary: string;
  claims: ClaimResult[];
  rhetoric: RhetoricTechnique[];
  corrections: string[];
  fairness: string[];
  sources: string[];
}

export type AnalysisState =
  | { status: "idle" }
  | { status: "analyzing"; steps: Step[]; currentPhase: string }
  | { status: "done"; steps: Step[]; result: AnalysisResult }
  | { status: "error"; message: string };
