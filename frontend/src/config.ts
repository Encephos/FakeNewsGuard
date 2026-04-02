// Zentralisierte Frontend-Konfiguration.

export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "/api/v1";
export const POLL_INTERVAL_MS = 2000;
export const MAX_POLL_ATTEMPTS = 960;

// ── API Route Timeouts (ms) ──────────────────────────────────────
export const TIMEOUT_ANALYZE = 30_000;
export const TIMEOUT_EXTRACT = 20_000;
export const TIMEOUT_DEFAULT = 10_000;

// ── SSE (Server-Sent Events) ────────────────────────────────────
export const SSE_MAX_RECONNECT_FAILURES = 3;

// ── Internal Backend URL (server-side only) ─────────────────────
export const INTERNAL_BACKEND_URL = process.env.BACKEND_URL ?? "http://backend:8000";
