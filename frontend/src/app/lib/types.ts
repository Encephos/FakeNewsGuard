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

export interface ExtractedContent {
  platform: string;
  title: string;
  author: string;
  images: string[];
  url: string;
}

export type ScoutTier = "lite" | "pro" | "max";

export interface ScoutTierInfo {
  id: ScoutTier;
  agent: string;       // "Scout Lite" | "Scout Pro" | "Scout Max"
  label: string;       // display label (localized)
  description: string; // short description (localized)
}

export type AnalysisState =
  | { status: "idle" }
  | { status: "analyzing"; steps: Step[]; currentPhase: string; extractedContent?: ExtractedContent }
  | { status: "done"; steps: Step[]; result: AnalysisResult; extractedContent?: ExtractedContent }
  | { status: "error"; message: string };

// ── Graph types ─────────────────────────────────────────────────

export type GraphNodeType = "CLAIM" | "SOURCE" | "ACTOR";

export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  properties?: Record<string, string>;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
}

export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  nodes_by_type: Record<string, number>;
  edges_by_relation: Record<string, number>;
}

export interface GraphNodeDetail {
  node: GraphNode;
  edges: GraphEdge[];
  neighbors: GraphNode[];
}

export interface GraphSearchResult {
  nodes: GraphNode[];
}
