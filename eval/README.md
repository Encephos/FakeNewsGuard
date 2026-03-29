# Retrieval Evaluation Framework

Measures the quality of the FakeNewsGuard retrieval/websearch pipeline
across claim categories, search backends, and retrieval metrics.

## Modes

| Mode     | Network | What it tests                                       |
|----------|---------|-----------------------------------------------------|
| `replay` | No      | Deterministic re-run of scoring, ranking, routing on stored snapshots |
| `live`   | Yes     | Real search queries against SearXNG/LangSearch, captures new snapshots |
| `smoke`  | Yes     | Full end-to-end pipeline (claim processing + retrieval + verdict) |

### Replay

Replays stored snapshots offline. Re-runs deterministic functions (query dedup,
claim routing, source ranking, quality signal computation) and compares to
stored values. Catches regressions in scoring logic without network access.

### Live

Executes real queries against search backends using the **production retrieval
path**: `_build_search_queries()` -> `ClaimRouter.route_and_apply()` ->
`SearXNGQuery` objects with per-query engine/category selection + multi-page ->
dedup -> `rank_sources()` -> lite evidence items (snippet-based) -> quality signals.

Live mode builds **lite evidence items** from search result snippets without
HTTP scraping. This captures evidence_type (direct/contextual/weak),
source_direction (supports/refutes/neutral/offtopic), and claim_scope_score
using the same production scoring functions.

### Smoke

Runs 2-3 cases through the full Orchestrator pipeline (claim extraction,
fact-checking, verdict). Validates end-to-end correctness, not just retrieval.

## Metrics

| Metric                          | Depends on       | Higher/Lower is better |
|---------------------------------|------------------|------------------------|
| `official_source_recall_at_k`   | evidence_items   | Higher                 |
| `preferred_domain_hit_rate`     | merged_results   | Higher                 |
| `low_trust_rate`                | evidence_items   | Lower                  |
| `offtopic_rate`                 | evidence_items   | Lower                  |
| `freshness_hit_rate`            | evidence_items   | Higher                 |
| `direct_evidence_rate`          | evidence_items   | Higher                 |
| `contextual_only_rate`          | evidence_items   | Lower                  |
| `scrape_waste_rate`             | ranked_sources   | Lower                  |
| `structured_source_hit_rate`    | source_clients   | Higher                 |
| `retrieval_precision_proxy_at_k`| evidence_items   | Higher                 |
| `query_duplication_rate`        | queries          | Lower                  |
| `source_diversity`              | merged_results   | Higher                 |
| `cache_hit_rate`                | cache metadata   | (informational)        |

Metrics depending on `evidence_items` are **production-grade** in both replay
and live mode. In live mode, evidence items are built from snippets using the
same `_classify_evidence_type`, `_classify_source_direction`, and
`_compute_claim_scope_score` functions as the production EvidenceBuilderAgent.

## Commands

```bash
# Replay on seed snapshots
python -m eval replay

# Replay with baseline save
python -m eval replay --save-baseline

# Replay with regression detection
python -m eval replay --baseline latest --fail-on-regression

# Live eval with SearXNG
python -m eval live --backends searxng

# Live eval with SearXNG + LangSearch
python -m eval live --backends searxng,langsearch --save-baseline

# Live eval for specific categories
python -m eval live --backends searxng --categories current_state,statistical

# Live eval for specific case
python -m eval live --backends searxng --case-ids cs-001

# Compare baselines
python -m eval compare --baseline1 baseline_2025-03-29_120000 --baseline2 latest

# Smoke test
python -m eval smoke
```

## Prerequisites

- **Replay:** No external services needed. Runs on committed seed snapshots.
- **Live:** Requires a running SearXNG instance (configured via `SEARXNG_URL` env var).
  LangSearch is optional (`LANGSEARCH_API_KEY`). No paid APIs required.
- **Smoke:** Requires SearXNG + an LLM provider (Anthropic/OpenAI/Ollama).

## Creating new snapshots / baselines

1. Run a live evaluation to capture snapshots:
   ```bash
   python -m eval live --backends searxng --save-baseline
   ```
   Snapshots are saved to `eval/snapshots/{case_id}.json`.

2. For regression testing, save a baseline:
   ```bash
   python -m eval replay --save-baseline
   ```
   Baselines are saved to `eval/data/baselines/`.

3. Compare against a previous baseline:
   ```bash
   python -m eval replay --baseline latest --fail-on-regression
   ```

## Adding evaluation cases

1. Add a new line to `eval/data/cases.jsonl`:
   ```json
   {"id": "cat-NNN", "claim_text": "...", "category": "...", "expectations": {...}}
   ```

2. Create a seed snapshot (either by running live eval or hand-crafting JSON).

3. Run replay to verify:
   ```bash
   python -m eval replay --case-ids cat-NNN
   ```

## Seed snapshot coverage

| Category               | Snapshots |
|------------------------|-----------|
| current_state          | cs-001    |
| regulatory             | reg-001   |
| statistical            | stat-001  |
| corporate              | corp-001  |
| medical_pharma         | med-001   |
| legal_eu               | leu-001   |
| noisy_or_underspecified| noisy-001 |
| off_topic_traps        | trap-001  |
| multilingual           | ml-001    |

## Exit codes

| Code | Meaning                                    |
|------|--------------------------------------------|
| 0    | All cases passed                           |
| 1    | Error-level violations or regressions      |
| 2    | Infrastructure failure (SearXNG unreachable)|
