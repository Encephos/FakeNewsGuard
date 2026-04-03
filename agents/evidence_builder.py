"""EvidenceBuilderAgent – Retrieval, Scraping und Evidenz-Strukturierung.

Verantwortlichkeiten:
    1. Query-Erstellung (LLM-optimiert)
    2. Retrieval via SearXNG (primär, breit) + LangSearch (semantisch, parallel)
    3. Google Fact Check API (separate Priority-Schicht)
    4. Deduplication + Quality-Aware Ranking
    5. Scraping der Top-K Quellen
    6. Strukturierung zu einem EvidencePack

Trust Boundary:
    Dieser Agent ist die Grenze zwischen ungefilterten Webinhalten und
    strukturierter Evidenz. Rohe HTML-Inhalte verlassen diesen Agent nie.
    Nur begrenzte, strukturierte excerpt-Felder (max. 800 Zeichen) werden
    im EvidencePack weitergegeben.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from agents.base import BaseAgent
from config import EvidenceRetrievalConfig
from tools.data_loader import searxng_engines
from tools.reranker import rerank, reranker_available
from tools.iterative_search import extract_feedback_terms, generate_refinement_queries
from models.evidence_models import (
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    GoogleFactCheckMatch,
    SourceDirection,
)
from models.schemas import Claim, ClaimSearchProfile, ProcessedClaim
from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
from tools.llm import LLMClient
from tools.scrape_ranker import RankedSource, rank_sources
from tools.source_scraper import ScrapedSource, scrape_sources
from tools.web_search import AsyncWebSearchClient, LangSearchClient, SearchResult, SearXNGClient, SearXNGQuery, WebSearchClient
from agents.evidence_scoring import (
    _cluster_by_perspective,
    _classify_evidence_type,
    _classify_source_direction,
    _compute_claim_scope_score,
    _compute_freshness,
    _compute_quality_signals,
    _count_active_anchors,
    _count_anchor_hits,
    _dedup_results,
    _detect_contradictions,
    _direction_weight,
    _domain_tier,
    _entity_overlap,
    _extract_best_excerpt,
    _extract_domain,
    _extract_entities,
    _has_commercial_content,
    _is_fact_check_org,
    _is_generic_reference,
    _is_low_trust_site,
    _is_offtopic_content,
    _is_offtopic_url,
    _langsearch_query_count,
    _profile_anchor_score,
    _rank_evidence_items,
    _relevance_score,
    _select_retrieval_strategy,
)

import re as _re


def _dedup_queries(queries: list[str]) -> list[str]:
    """Dedupliziere Queries nach Normalisierung (lowercase, whitespace, Satzzeichen)."""
    seen: set[str] = set()
    result: list[str] = []
    for q in queries:
        norm = _re.sub(r"\s+", " ", q.strip().lower()).rstrip(".,;:!?")
        if norm and norm not in seen:
            seen.add(norm)
            result.append(q)
    return result


def _dedup_searxng_queries(queries: list[SearXNGQuery]) -> list[SearXNGQuery]:
    """Dedupliziere SearXNGQuery-Liste nach (normalized_query, pageno)."""
    seen: set[tuple[str, int]] = set()
    result: list[SearXNGQuery] = []
    for sq in queries:
        norm = _re.sub(r"\s+", " ", sq.query.strip().lower()).rstrip(".,;:!?")
        key = (norm, sq.pageno)
        if key not in seen:
            seen.add(key)
            result.append(sq)
    return result


# ── EvidenceBuilderAgent ──────────────────────────────────────────────────────

class EvidenceBuilderAgent(BaseAgent):
    """Baut ein strukturiertes EvidencePack für einen Claim auf.

    Ablauf:
        1. Query-Optimierung (LLM)
        2. Paralleles Retrieval: LangSearch + SearXNG
        3. Google Fact Check API (asynchron parallel)
        4. Deduplication + Ranking
        5. Scraping der Top-K Quellen
        6. Trust-Boundary: Extraktion auf max. 800 Zeichen begrenzen
        7. EvidencePack zusammenstellen
    """

    name = "Evidence Builder"
    emoji = "🔎"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Search Cache (Valkey wenn verfügbar, sonst In-Memory)
        from tools.db.factory import create_search_cache
        self._search_cache = create_search_cache(self.config)

        # Google Fact Check Client
        self._gfc_client = FactCheckDatabaseClient(
            config=FactCheckDatabaseConfig(
                google_factcheck_api_key=self.config.google_fact_check.api_key,
                enabled=self.config.google_fact_check.enabled,
            ),
            retry=self.config.retry,
        )

        # LangSearch Client (primär – semantische Hauptsuche)
        self._langsearch = LangSearchClient(
            config=self.config.langsearch,
            retry=self.config.retry,
            search_cache=self._search_cache,
        )

        # SearXNG Client (unterstützend – kostenlos, alle Queries, self-hosted)
        # Dedizierter Client: immer und ausschließlich SearXNG.
        # Kein Provider-Routing, keine Abhängigkeit von search.provider.
        self._searxng = SearXNGClient(
            config=self.config.searxng,
            retry=self.config.retry,
            search_cache=self._search_cache,
        )

        # ClaimRouter (einmalig instanziiert, nicht pro Claim)
        from tools.claim_router import ClaimRouter
        self._router = ClaimRouter()

    def _get_source_adapter(self, source_config: "SourceConfig"):  # type: ignore[name-defined]
        """Instanziiere oder hole Source Adapter via AdapterGuardian (mit Caching).

        AdapterGuardian handled:
        - Caching (24h default, 168h für statische Quellen)
        - Rate-Limiting (pro-Quelle Token-Bucket)
        - Circuit-Breaker (verhindert Cascade-Fehler)
        """
        if not hasattr(self, "_source_adapters"):
            self._source_adapters = {}

        if source_config.source_id not in self._source_adapters:
            try:
                from tools.sources.adapter_guardian import AdapterGuardian
                import tools.sources.clients as clients_module

                # Map source_id → adapter class
                adapter_class_map = {
                    "world_bank": clients_module.WorldBankClient,
                    "gleif": clients_module.GLEIFClient,
                    "openfda": clients_module.OpenFDAClient,
                    "openalex": clients_module.OpenAlexClient,
                    "arxiv": clients_module.ArXivClient,
                    "crossref": clients_module.CrossrefClient,
                    "cern_open_data": clients_module.CERNOpenDataClient,
                    "eurostat": clients_module.EurostatClient,
                    "eur_lex": clients_module.EURLexClient,
                    "uspto": clients_module.USPTOClient,
                    "companies_house": clients_module.CompaniesHouseClient,
                    "clinicaltrials": clients_module.ClinicalTrialsClient,
                    "dailymed": clients_module.DailyMedClient,
                    "pubmed": clients_module.PubMedClient,
                }

                adapter_class = adapter_class_map.get(source_config.source_id)
                if not adapter_class:
                    self._log(f"⚠ Source client nicht verfügbar: {source_config.source_id}")
                    return None

                adapter = adapter_class(source_config)
                self._source_adapters[source_config.source_id] = AdapterGuardian(adapter)
            except Exception as e:
                self._log(f"⚠ Source adapter init failed ({source_config.source_id}): {type(e).__name__}")
                return None

        return self._source_adapters[source_config.source_id]

    def _select_query_for_source(
        self,
        queries: list[str],
        route_result: "RouteResult",  # type: ignore[name-defined]
        source_config: "SourceConfig",  # type: ignore[name-defined]
    ) -> str | None:
        """Wähle beste Query für ein Source basierend auf Domänen-Match."""
        if not route_result.domains or not source_config.claim_domains:
            return queries[0] if queries else None

        # Finde Queries die zu Source-Domänen passen
        # (simplistically: prefer first query for now)
        return queries[0] if queries else None

    @staticmethod
    def _convert_official_evidence_to_search_results(
        source_results: dict[str, list]
    ) -> list[SearchResult]:
        """Konvertiere OfficialEvidenceItem-Lister zu SearchResult-Format.

        Source Clients liefern OfficialEvidenceItems mit title, url, excerpt, full_text.
        Diese müssen zu SearchResult konvertiert werden für Deduplication + Ranking.
        """
        results: list[SearchResult] = []

        for source_id, items in source_results.items():
            if not items:
                continue

            # items sind listen von dicts (OfficialEvidenceItem serialisiert)
            for item in items:
                try:
                    title = item.get("title", "") if isinstance(item, dict) else getattr(item, "title", "")
                    url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
                    excerpt = item.get("excerpt", "") if isinstance(item, dict) else getattr(item, "excerpt", "")
                    full_text = item.get("full_text", "") if isinstance(item, dict) else getattr(item, "full_text", "")

                    # Display-Policy durchsetzen
                    display_policy = (
                        item.get("display_policy", "metadata")
                        if isinstance(item, dict)
                        else getattr(item, "display_policy", "metadata")
                    )
                    # Normalisiere Enum-Werte zu Strings
                    dp = display_policy.value if hasattr(display_policy, "value") else str(display_policy)

                    if dp == "metadata":
                        excerpt = ""
                        full_text = ""
                    elif dp == "excerpt":
                        full_text = ""
                        excerpt = (excerpt or "")[:400]

                    result = SearchResult(
                        title=title,
                        url=url,
                        snippet=excerpt or "",
                        content=full_text or "",
                    )
                    results.append(result)
                except Exception:
                    continue  # Skip malformed items

        return results

    async def _build_source_client_tasks(
        self,
        claim: "Claim",  # type: ignore[name-defined]
        route_result: "RouteResult",  # type: ignore[name-defined]
        queries: list[str],
    ) -> tuple[list[tuple[str, Any]], dict[str, list]]:  # task list + results placeholder
        """Erstelle Source Client Tasks für hohe-confidence Routes.

        Returns:
            (task_list, results_map) wobei results_map ein dict für Ergebnisse ist
        """
        source_cfg = self.config.source_clients

        if not source_cfg.enabled:
            return [], {}

        # Nur wenn Routing-Konfidenz ausreichend ist
        if route_result.confidence < source_cfg.min_confidence:
            self._log(
                f"Source clients übersprungen (confidence: {route_result.confidence:.2f} < {source_cfg.min_confidence})"
            )
            return [], {}

        # Source-Tasks erstellen
        source_tasks: list[tuple[str, Any]] = []
        results_map: dict[str, list] = {}

        for source_config in route_result.sources[: source_cfg.max_sources_per_claim]:
            # Adapter instantiieren
            adapter = self._get_source_adapter(source_config)
            if not adapter:
                continue

            # Beste Query für diese Source
            query = self._select_query_for_source(queries, route_result, source_config)
            if not query:
                continue

            # Task erstellen
            try:
                task = adapter.search(query, max_results=source_cfg.max_results_per_source)
                source_tasks.append((source_config.source_id, task))
                results_map[source_config.source_id] = []
            except Exception as e:
                self._log(f"⚠ Source task creation failed ({source_config.source_id}): {type(e).__name__}")

        if source_tasks:
            self._log(
                f"Source clients aktiviert: {len(source_tasks)} Quellen "
                f"(Domains: {[d.value for d in route_result.domains]})"
            )

        return source_tasks, results_map

    def execute(self, input_data: Any, context: str = "") -> EvidencePack:
        """Synchrone Version – nutzt asyncio.run intern."""
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                # In async context: neuen Task erstellen
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(_asyncio.run, self.execute_async(input_data, context))
                    return future.result()
            else:
                return loop.run_until_complete(self.execute_async(input_data, context))
        except RuntimeError:
            return _asyncio.run(self.execute_async(input_data, context))

    async def execute_with_queries_async(
        self, claim: Claim, queries: list[str], context: str = "",
    ) -> EvidencePack:
        """Vom Commander aufgerufen mit vorgegebenen Queries.

        Überspringt die interne Query-Generierung und nutzt die übergebenen
        Queries direkt für Retrieval + EvidencePack-Assembly.
        """
        return await self.execute_async(claim, context=context, _queries_override=queries)

    async def execute_async(
        self, input_data: Any, context: str = "", _queries_override: list[str] | None = None,
    ) -> EvidencePack:
        """Async-Version – Retrieval läuft parallel.

        Retrieval-Rollen (klar getrennt, Priorität bei Fusion):
            SearXNG    = primäre Breitensuche (alle Queries, kostenlos, self-hosted)
            LangSearch = semantische Hauptsuche (adaptiv 3–5 Queries, Dedup-Priorität)
            GFC        = strukturierter Shortcut-Layer (höchste Priorität)

        Args:
            _queries_override: Wenn gesetzt, werden diese Queries statt der
                intern generierten verwendet (für Commander-Integration).
        """
        claim: Claim = input_data
        notes: list[str] = []

        # Profil FRÜH extrahieren – wird für adaptives Retrieval + Pre-Scraping-Filter benötigt
        profile: ClaimSearchProfile | None = None
        if isinstance(claim, ProcessedClaim) and claim.search_profile:
            profile = claim.search_profile

        # ── 1. Query-Optimierung ──────────────────────────────────────────────
        if _queries_override is not None:
            queries = list(_queries_override)
            notes.append(f"Queries (Commander-Override): {queries}")
        else:
            queries = await self._build_queries_async(claim, context)
            notes.append(f"Queries: {queries}")

        # Unspezifik-Notiz: falls Qualitätssignale auf fehlende Spezifik hinweisen,
        # festhalten dass generische Query-Familien genutzt wurden und die
        # Verifizierbarkeit eingeschränkt sein kann.
        if isinstance(claim, ProcessedClaim):
            _underspec = {"underspecified_actor", "missing_artifact_evidence"}
            detected = _underspec & set(claim.quality_signals or [])
            if detected:
                signal_labels = ", ".join(sorted(detected))
                notes.append(
                    f"Unspezifik erkannt ({signal_labels}): Generische Query-Familien "
                    f"(direct/artifact/fact-check/official-response) verwendet. "
                    f"Claim ohne eindeutigen Akteur oder verifizierbares Artefakt – "
                    f"Verifizierung kann unvollständig bleiben."
                )

        # ── 2. Adaptives paralleles Retrieval ────────────────────────────────
        # Rollen: SearXNG = primäre Breitensuche, LangSearch = semantische Hauptsuche,
        #         GFC = Shortcut-Layer, SourceClients = institutionelle Datenquellen
        from agents.fact_checker import _categories_for_claim, _is_current_state_claim

        categories = _categories_for_claim(claim)
        retrieval_cfg = self.config.evidence_retrieval

        # ── Adaptive RAG: Retrieval-Strategie basierend auf Claim-Komplexität ──
        from config.processing import RetrievalStrategy
        strategy = _select_retrieval_strategy(claim, retrieval_cfg)
        if strategy != RetrievalStrategy.STANDARD:
            notes.append(f"Adaptive RAG: Strategie={strategy.value}")

        # Route claim für Source Client selection
        route_result = None
        try:
            if isinstance(claim, ProcessedClaim):
                route_result, _ = self._router.route_and_apply(claim)
        except Exception as e:
            self._log(f"ClaimRouter error: {type(e).__name__}")

        # Recency-Override: Aktuell-Zustand-Claims (z.B. Amtsinhaber) brauchen frische Quellen
        is_current_state = _is_current_state_claim(claim.text)
        if is_current_state:
            _current_year = str(datetime.now(timezone.utc).year)
            news_cats = ",".join(retrieval_cfg.searxng_news_categories)
            if categories != news_cats:
                categories = news_cats
            # Alle Queries mit aktuellem Jahr anreichern (nicht nur erste 2)
            queries = [
                f"{q} {_current_year}" if _current_year not in q else q
                for q in queries
            ]
            # Ersten Query zusätzlich mit "aktuell" versehen (stärkstes Recency-Signal)
            if queries and "aktuell" not in queries[0].lower() and "current" not in queries[0].lower():
                queries[0] = f"{queries[0]} aktuell"
            notes.append(
                f"Recency-Override: News-Kategorien ({categories}), "
                f"Jahr {_current_year} + 'aktuell' ergänzt für Aktuell-Zustand-Claim"
            )

        # Query-Deduplizierung: entferne normalisierte Duplikate
        queries_before_dedup = len(queries)
        queries = _dedup_queries(queries)

        # LangSearch: adaptiv nach Claim-Komplexität (2–4 Queries)
        # Adaptive RAG überschreibt Query-Anzahl bei SIMPLE/DEEP
        if strategy == RetrievalStrategy.SIMPLE:
            ls_count = retrieval_cfg.adaptive_simple_langsearch_queries
        elif strategy == RetrievalStrategy.DEEP:
            ls_count = retrieval_cfg.adaptive_deep_langsearch_queries
        else:
            ls_count = _langsearch_query_count(claim, retrieval_cfg)
        langsearch_queries = queries[:ls_count]

        # Parallele Tasks starten
        # SearXNG: Per-Query-Routing + Multi-Page (pageno=1 und pageno=2 für Top-Queries)
        # Faktencheck/Falschmeldung-Queries → News-Engines
        # Regulatory/Current-State-Claims → News-Engines + time_range
        # Top-2 Queries bekommen zusätzlich pageno=2 für mehr Tiefe
        searxng_queries: list[SearXNGQuery] = []
        for i, q in enumerate(queries):
            sq = SearXNGQuery(query=q, categories=categories)
            if "Faktencheck" in q or "Falschmeldung" in q:
                sq.engines = searxng_engines().get("news", ["duckduckgo", "brave", "tagesschau"])
            elif is_current_state:
                sq.engines = searxng_engines().get("news", ["duckduckgo", "brave", "tagesschau"])
                sq.time_range = retrieval_cfg.current_state_time_range
            else:
                sq.engines = searxng_engines().get("web", ["duckduckgo", "brave", "qwant"])
            searxng_queries.append(sq)
            # Multi-Page: Top-2 Queries auch auf Seite 2 suchen
            # Adaptive RAG SIMPLE: kein Multi-Page (weniger Queries)
            if i < 2 and not (strategy == RetrievalStrategy.SIMPLE and not retrieval_cfg.adaptive_simple_searxng_multipage):
                sq2 = SearXNGQuery(
                    query=q,
                    categories=categories,
                    engines=sq.engines,
                    time_range=sq.time_range,
                    pageno=2,
                )
                searxng_queries.append(sq2)

        # Site-Hints aus ClaimRouter als zusätzliche SearXNG-Queries
        if route_result and route_result.site_hints and queries:
            for hint in route_result.site_hints[:2]:
                searxng_queries.append(SearXNGQuery(
                    query=f"{queries[0]} {hint}",
                    categories=categories,
                ))

        searxng_queries = _dedup_searxng_queries(searxng_queries)
        searxng_task = self._searxng.multi_search_async(
            searxng_queries, max_results=self.config.searxng.max_results,
        )
        langsearch_task = self._langsearch.multi_search_async(
            langsearch_queries, max_results=self.config.langsearch.max_results,
        )
        gfc_task = self._gfc_client.search_async(claim.text)

        # ── Source Client Tasks (NEW) ──────────────────────────────────────────
        source_tasks = []
        source_results_map = {}
        if route_result:
            source_tasks, source_results_map = await self._build_source_client_tasks(
                claim, route_result, queries
            )

        # ── Parallele Ausführung aller Tasks ───────────────────────────────────
        gather_tasks = [searxng_task, langsearch_task, gfc_task]
        gather_tasks.extend([task for _, task in source_tasks])

        all_results = await asyncio.gather(*gather_tasks, return_exceptions=False)

        # ── Ergebnisse auspacken ──────────────────────────────────────────────
        searxng_results = all_results[0] if len(all_results) > 0 else {}
        langsearch_results = all_results[1] if len(all_results) > 1 else {}
        gfc_raw = all_results[2] if len(all_results) > 2 else []

        # Source Client Results (NEW)
        source_client_results = {}
        if source_tasks:
            for idx, (source_id, _) in enumerate(source_tasks):
                result_idx = 3 + idx
                if result_idx < len(all_results):
                    source_client_results[source_id] = all_results[result_idx] or []

        # ── 3. LangSearch-Retry bei schwacher erster Evidenz ─────────────────
        # LangSearch ist die primäre semantische Suchquelle – bei schwacher
        # erster Runde wird LangSearch mit zusätzlichen Queries erweitert.
        # Adaptive RAG DEEP: niedrigere Retry-Schwelle für aggressiveres Retrieval
        weak_threshold = retrieval_cfg.weak_evidence_threshold
        if strategy == RetrievalStrategy.DEEP:
            weak_threshold = retrieval_cfg.adaptive_deep_langsearch_retry_threshold
        if (
            retrieval_cfg.langsearch_retry_on_weak
            and self.config.langsearch.enabled
            and ls_count < len(queries)
        ):
            ls_scores = [
                _relevance_score(r, claim.text, profile)
                for q_res in langsearch_results.values()
                for r in q_res
            ]
            avg_ls = sum(ls_scores) / len(ls_scores) if ls_scores else 0.0
            if avg_ls < weak_threshold:
                extra_queries = queries[ls_count:ls_count + 2]
                if extra_queries:
                    extra = await self._langsearch.multi_search_async(
                        extra_queries, max_results=self.config.langsearch.max_results,
                    )
                    langsearch_results.update({f"retry_{q}": v for q, v in extra.items()})
                    notes.append(
                        f"LangSearch-Retry: avg_relevance={avg_ls:.2f} < "
                        f"{retrieval_cfg.weak_evidence_threshold}, "
                        f"{len(extra_queries)} zusätzliche Queries"
                    )

        # ── 4. Ergebnisse zusammenführen + deduplizieren ─────────────────────
        # Reihenfolge: LangSearch (semantisch) → SearXNG (breit) → SourceClients (strukturiert)
        # _dedup_results() behält erstes Vorkommen → LangSearch hat Dedup-Priorität
        all_results: list[SearchResult] = []
        langsearch_count = sum(len(v) for v in langsearch_results.values())
        searxng_count = sum(len(v) for v in searxng_results.values())
        source_clients_count = 0

        for q_res in langsearch_results.values():
            all_results.extend(q_res)
        for q_res in searxng_results.values():
            all_results.extend(q_res)

        # Source Client Results (NEW) – konvertieren + hinzufügen
        if source_client_results:
            source_search_results = self._convert_official_evidence_to_search_results(
                source_client_results
            )
            source_clients_count = len(source_search_results)
            all_results.extend(source_search_results)

        unique_results = _dedup_results(all_results, retrieval_cfg.semantic_dedup_threshold)

        # ── Cross-Encoder Re-Ranking (Phase 2) ─────────────────────────────
        ce_scores: dict[str, float] = {}
        if reranker_available():
            ce_ranked = rerank(claim.text, unique_results, top_k=30)
            ce_scores = {r.url: float(s) for r, s in ce_ranked}
            notes.append(f"Cross-Encoder: {len(ce_scores)} Ergebnisse bewertet")

        # Logging mit Source Client Info (NEW)
        retrieval_log = (
            f"Retrieval: {len(all_results)} Treffer → {len(unique_results)} unique "
            f"(LangSearch: {langsearch_count} semantisch [{ls_count} Queries], "
            f"SearXNG: {searxng_count} breit"
        )
        if source_clients_count > 0:
            retrieval_log += f", SourceClients: {source_clients_count} strukturiert"
        retrieval_log += ")"
        notes.append(retrieval_log)

        # ── 6. Google Fact Check Matches aufbereiten ──────────────────────────
        gfc_matches = [
            GoogleFactCheckMatch(
                claim_reviewed=fc.claim_reviewed,
                rating=fc.rating,
                publisher=fc.publisher,
                url=fc.url,
                language=fc.language,
                title=fc.title,
            )
            for fc in gfc_raw
        ]
        if gfc_matches:
            notes.append(f"Google Fact Check: {len(gfc_matches)} Treffer")

        # ── 7. Candidate Selection + Scraping ────────────────────────────────
        # Profile + Low-Trust-Signale fließen VOR dem Scraping ein:
        # Nur die wirklich besten Kandidaten werden gescraped.
        # Adaptive RAG: Scrape-Tiefe an Strategie anpassen
        scrape_top_n_override = None
        if strategy == RetrievalStrategy.SIMPLE:
            scrape_top_n_override = retrieval_cfg.adaptive_simple_scrape_top_n
        elif strategy == RetrievalStrategy.DEEP:
            scrape_top_n_override = retrieval_cfg.adaptive_deep_scrape_top_n
        ranked, scraped = await self._rank_and_scrape(
            unique_results, claim,
            profile=profile,
            scrape_top_n_override=scrape_top_n_override,
        )

        # ── 7b. CRAG – Document Quality Gate ─────────────────────────────────
        if retrieval_cfg.crag_enabled:
            ranked, scraped, crag_notes = await self._crag_filter(
                ranked, scraped, claim.text, profile=profile,
            )
            notes.extend(crag_notes)

            # Nachabfrage bei hoher Irrelevanz-Rate
            incorrect_rate = 0.0
            for note in crag_notes:
                if "Rate=" in note:
                    try:
                        rate_str = note.split("Rate=")[1].split(")")[0].rstrip("%")
                        incorrect_rate = float(rate_str) / 100.0
                    except (ValueError, IndexError):
                        pass
            if incorrect_rate > retrieval_cfg.crag_incorrect_threshold:
                notes.append(
                    f"CRAG: Nachabfrage ausgelöst (Irrelevanz-Rate={incorrect_rate:.0%} > "
                    f"{retrieval_cfg.crag_incorrect_threshold:.0%})"
                )
                fallback_results = await self._fallback_retrieval(claim, queries)
                if fallback_results:
                    unique_results = _dedup_results(all_results + fallback_results, retrieval_cfg.semantic_dedup_threshold)
                    if reranker_available():
                        ce_ranked = rerank(claim.text, unique_results, top_k=30)
                        ce_scores = {r.url: float(s) for r, s in ce_ranked}
                    ranked, scraped = await self._rank_and_scrape(
                        unique_results, claim, profile=profile,
                        scrape_top_n_override=scrape_top_n_override,
                    )
                    notes.append(f"CRAG Nachabfrage: {len(fallback_results)} neue Ergebnisse")

        # ── 8. EvidenceItems aus gescrapten Quellen bauen ─────────────────────
        evidence_items = self._build_evidence_items(ranked, scraped, claim.text, gfc_matches, profile, is_current_state=is_current_state, ce_scores=ce_scores)
        notes.append(f"Evidence Items: {len(evidence_items)} (mit Scraping)")

        # ── 9. Qualität, Widersprüche, Pack zusammenstellen ───────────────────
        contradictions = _detect_contradictions(evidence_items[:6])
        quality = _compute_quality_signals(
            evidence_items, gfc_matches,
            low_trust_penalty_factor=retrieval_cfg.low_trust_confidence_penalty,
            stale_threshold=retrieval_cfg.stale_sources_freshness_threshold,
            stale_penalty_factor=retrieval_cfg.stale_sources_confidence_penalty,
            is_current_state=is_current_state,
        )

        # ── Iterative Search: Qualitäts-basierter Retry mit Relevanz-Feedback ──
        # Adaptive RAG: SIMPLE deaktiviert iterativen Search, DEEP passt Runden an
        iterative_enabled = retrieval_cfg.iterative_search_enabled
        iterative_max = retrieval_cfg.iterative_max_rounds
        if strategy == RetrievalStrategy.SIMPLE:
            iterative_enabled = retrieval_cfg.adaptive_simple_iterative_enabled
        elif strategy == RetrievalStrategy.DEEP:
            iterative_max = retrieval_cfg.adaptive_deep_iterative_max_rounds
        iterative_round = 0
        while (
            iterative_enabled
            and iterative_round < iterative_max
            and quality.overall_quality < retrieval_cfg.iterative_min_quality
        ):
            iterative_round += 1

            # Feedback-Terme aus Top-Ergebnissen extrahieren
            top_results = unique_results[:10]
            feedback_terms = extract_feedback_terms(claim.text, top_results)

            if feedback_terms:
                # Verfeinerte Queries generieren
                refined_queries = generate_refinement_queries(
                    claim.text, feedback_terms,
                    max_queries=retrieval_cfg.iterative_max_refinement_queries,
                )
                notes.append(
                    f"Iterative Runde {iterative_round}: Feedback-Terme={feedback_terms[:3]}, "
                    f"Queries={len(refined_queries)}"
                )
            else:
                # Kein Feedback → klassischer Fallback
                notes.append(f"Iterative Runde {iterative_round}: Kein Feedback, Fallback-Suche")
                refined_queries = []

            # Fallback + ggf. Refinement-Queries ausführen
            fallback_results = await self._fallback_retrieval(claim, queries)
            if refined_queries:
                # Refinement-Queries über SearXNG
                refinement_sq = [SearXNGQuery(query=q) for q in refined_queries]
                refinement_results = await self._searxng.multi_search_async(refinement_sq)
                for results_list in refinement_results.values():
                    fallback_results.extend(results_list)

            unique_results = _dedup_results(all_results + fallback_results, retrieval_cfg.semantic_dedup_threshold)
            # Re-rank mit Cross-Encoder (inkl. neue Ergebnisse)
            if reranker_available():
                ce_ranked = rerank(claim.text, unique_results, top_k=30)
                ce_scores = {r.url: float(s) for r, s in ce_ranked}
            ranked, scraped = await self._rank_and_scrape(
                unique_results, claim, profile=profile,
                scrape_top_n_override=scrape_top_n_override,
            )
            evidence_items = self._build_evidence_items(ranked, scraped, claim.text, gfc_matches, profile, is_current_state=is_current_state, ce_scores=ce_scores)
            quality = _compute_quality_signals(
                evidence_items, gfc_matches,
                low_trust_penalty_factor=retrieval_cfg.low_trust_confidence_penalty,
                stale_threshold=retrieval_cfg.stale_sources_freshness_threshold,
                stale_penalty_factor=retrieval_cfg.stale_sources_confidence_penalty,
                is_current_state=is_current_state,
            )
            notes.append(f"Iterative Runde {iterative_round}: {len(evidence_items)} Items, Qualität={quality.overall_quality:.2f}")

        selected_sources = [item.source for item in evidence_items[:5]]

        canonical_text = ""
        if isinstance(claim, ProcessedClaim):
            canonical_text = claim.canonical_text

        pack = EvidencePack(
            claim_id=claim.id,
            claim_text=claim.text,
            canonical_text=canonical_text,
            queries_used=queries,
            google_fact_check_matches=gfc_matches,
            web_results=evidence_items,
            selected_sources=selected_sources,
            contradictions=contradictions,
            extraction_confidence=quality.overall_quality,
            evidence_quality=quality,
            retrieval_notes=notes,
            source_count=len(unique_results),
        )

        # ── Retrieval-Metriken ────────────────────────────────────────────────
        scrape_attempted = len([r for r in ranked if r.should_scrape])
        scrape_succeeded = len(scraped)
        cache_stats = self._search_cache.stats() if self._search_cache else {}
        metrics = {
            "queries_generated": queries_before_dedup,
            "queries_after_dedup": len(queries),
            "searxng_results": searxng_count,
            "langsearch_results": langsearch_count,
            "gfc_matches": len(gfc_matches),
            "source_client_results": source_clients_count,
            "unique_after_dedup": len(unique_results),
            "scrape_attempted": scrape_attempted,
            "scrape_succeeded": scrape_succeeded,
            "iterative_rounds": iterative_round,
            "cache_hit_rate": cache_stats.get("hit_rate", 0.0),
        }
        metrics_line = " | ".join(f"{k}={v}" for k, v in metrics.items())
        self._log(f"Claim {claim.id}: {metrics_line}")
        notes.append(f"Retrieval-Metriken: {metrics_line}")

        return pack

    async def _build_queries_async(self, claim: Claim, context: str) -> list[str]:
        """Queries aufbauen: Profile-basiert → QueryExpansionEngine → erweitert zu 6–8.

        Priorität:
            1. QueryExpansionEngine (wenn enabled) – 6–8 diverse Queries
            2. Fallback: Profile-basierte Queries (3–4)
            3. LLM nur wenn Profil weniger als 3 Queries liefert

        QueryExpansionEngine nutzt ClaimRouter für domain/jurisdiction-aware Expansion.
        """
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()

        from agents.fact_checker import _build_search_queries, _optimize_queries_with_llm
        from tools.query_expansion import QueryExpansionEngine

        # Phase 1: Profile-basierte Queries (wie zuvor)
        profile_queries = _build_search_queries(claim, context)

        # Phase 2: QueryExpansionEngine (NEW – wenn Claim strukturiert und enabled)
        if self.config.evidence_retrieval.query_expansion_enabled:
            try:
                # Nur wenn claim eine ProcessedClaim mit SearchProfile ist
                if isinstance(claim, ProcessedClaim) and claim.search_profile:
                    # Route claim für Domain/Jurisdiction-aware expansion
                    route_result, _ = self._router.route_and_apply(claim)

                    # Expand queries via QueryExpansionEngine
                    expander = QueryExpansionEngine()
                    query_variants = expander.expand(claim, route_result, claim.search_profile)

                    # Extrahiere Text aus Variants (6–8 diverse Queries)
                    expanded_queries = [v.text for v in query_variants]

                    if expanded_queries:
                        self._log(
                            f"QueryExpansionEngine: {len(expanded_queries)} diverse Queries "
                            f"(domains: {[d.value for d in route_result.domains]}, "
                            f"jurisdiction: {route_result.jurisdiction})"
                        )
                        # Return expanded queries (6–8 instead of 4)
                        return expanded_queries
            except Exception as e:
                # Fallback zu Profile-Queries wenn Expansion fehlschlägt
                self._log(f"QueryExpansionEngine Fehler, fallback zu Profile-Queries: {type(e).__name__}")

        # Fallback Phase 3: Profile-Queries (wie zuvor)
        if len(profile_queries) >= 3:
            return profile_queries[:4]

        # Profil unvollständig (kein Frame/Profil) → LLM als Ergänzung
        llm_queries = await loop.run_in_executor(
            None, _optimize_queries_with_llm, claim, self.llm, context
        )
        if llm_queries:
            # Merge: Profile-Queries zuerst, dann eindeutige LLM-Queries anhängen
            seen: set[str] = set(profile_queries)
            for q in llm_queries:
                if q and q not in seen:
                    profile_queries.append(q)
                    seen.add(q)

        return profile_queries[:4] if profile_queries else [claim.text]

    async def _rank_and_scrape(
        self,
        results: list[SearchResult],
        claim: Claim,
        profile: ClaimSearchProfile | None = None,
        scrape_top_n_override: int | None = None,
    ) -> tuple[list[RankedSource], list[ScrapedSource]]:
        """Ranke und scrape – Profile/Low-Trust-Filter greifen VOR dem Scraping.

        Ablauf:
            1. Low-Trust-Seiten und klar off-topic Kandidaten entfernen (Pre-Filter)
            2. rank_sources() auf gefilterten Kandidaten
            3. Eigentliches Scraping der Top-K Kandidaten
        """
        retrieval_cfg = self.config.evidence_retrieval

        # ── 1. Pre-Scraping-Filter ────────────────────────────────────────────
        # Low-Trust-Seiten und klar off-topic Kandidaten BEVOR rank_sources() entfernen.
        # Fact-Checker sind immer durchgelassen (tier 4 Bonus bleibt erhalten).
        filtered: list[SearchResult] = []
        removed = 0
        for r in results:
            # Low-Trust-Seitentypen (Grammatik, Währungsrechner, Juraforen …)
            if _is_low_trust_site(r.url, r.title, r.snippet) and not _is_fact_check_org(r.url):
                removed += 1
                continue
            # Profil-basierte Off-topic-Filterung: nur bei starker Penalty + geringer Relevanz
            if profile:
                is_ot, penalty = _is_offtopic_content(r.title, r.snippet, profile)
                if (
                    is_ot
                    and penalty >= retrieval_cfg.pre_scrape_offtopic_penalty
                    and _relevance_score(r, claim.text, profile) < 0.25
                    and not _is_fact_check_org(r.url)
                ):
                    removed += 1
                    continue
            filtered.append(r)

        if removed:
            self._log(f"Pre-Scraping-Filter: {removed}/{len(results)} Low-Trust/Off-topic Kandidaten entfernt")

        # ── 2. Ranking (hybrid: BM25 + semantic + profile + low-trust) ─────
        effective_scrape_top_n = scrape_top_n_override or self.config.searxng.scrape_top_n
        results_by_query: dict[str, list[SearchResult]] = {"_all": filtered}
        ranked = rank_sources(
            results_by_query,
            claim.text,
            max_scrape=effective_scrape_top_n,
            profile=profile,
        )

        # ── 3. LangSearch-Content-Skip ────────────────────────────────────────
        # LangSearch liefert Summaries – bei ausreichendem Content kein Scraping nötig.
        content_skipped = 0
        for rs in ranked:
            if not rs.should_scrape or not rs.result.content:
                continue
            if len(rs.result.content) > 300:
                ls_rel = _relevance_score(rs.result, claim.text, profile)
                if ls_rel > 0.30:
                    rs.should_scrape = False
                    rs.skip_reason = "langsearch_content_sufficient"
                    content_skipped += 1
        if content_skipped:
            self._log(f"LangSearch-Content ausreichend: {content_skipped} Scraping-Requests eingespart")

        scrape_count = sum(1 for rs in ranked if rs.should_scrape)
        self._log(f"Scraping {scrape_count} von {len(ranked)} Quellen...")

        scraped = await scrape_sources(
            ranked, claim.text,
            max_concurrent=self.config.searxng.max_concurrent_searches,
            timeout=self.config.searxng.scrape_timeout,
        )
        success = sum(1 for s in scraped if s.fetch_success)
        self._log(f"Scraping: {success}/{len(scraped)} erfolgreich")
        return ranked, scraped

    async def _crag_filter(
        self,
        ranked: list[RankedSource],
        scraped: list[ScrapedSource],
        claim_text: str,
        profile: ClaimSearchProfile | None = None,
    ) -> tuple[list[RankedSource], list[ScrapedSource], list[str]]:
        """CRAG – Corrective RAG Document Quality Gate.

        Klassifiziert gescrapte Dokumente als CORRECT/AMBIGUOUS/INCORRECT
        per LLM-Batch-Call und filtert irrelevante Dokumente BEVOR
        Evidence-Items konstruiert werden.

        Returns:
            (filtered_ranked, filtered_scraped, crag_notes)
        """
        import asyncio as _asyncio
        import json as _json

        notes: list[str] = []
        scraped_by_url = {s.url: s for s in scraped if s.fetch_success}

        if not scraped_by_url:
            return ranked, scraped, notes

        # Batch-Input vorbereiten: Titel + erste 200 Zeichen pro Dokument
        doc_entries = []
        for rs in ranked:
            sc = scraped_by_url.get(rs.result.url)
            if not sc or not sc.passage:
                continue
            title = rs.result.title or ""
            preview = sc.passage[:200].strip()
            doc_entries.append({
                "url": rs.result.url,
                "title": title,
                "preview": preview,
            })

        if not doc_entries:
            return ranked, scraped, notes

        # LLM-Batch-Call: Alle Dokumente in einem Request klassifizieren
        system_prompt = (
            "Du bist ein Dokument-Relevanz-Klassifikator. Für jeden Eintrag in der "
            "Liste bewerte, ob das Dokument den angegebenen Claim DIREKT behandelt.\n\n"
            "Antwortformat: JSON-Array mit einem Objekt pro Dokument:\n"
            '[{"url": "...", "label": "CORRECT|AMBIGUOUS|INCORRECT"}]\n\n'
            "CORRECT = Dokument behandelt den Claim direkt und enthält relevante Informationen\n"
            "AMBIGUOUS = Dokument ist thematisch verwandt, aber der Claim-Bezug ist unklar\n"
            "INCORRECT = Dokument hat keinen relevanten Bezug zum Claim"
        )
        user_msg = (
            f"Claim: {claim_text}\n\n"
            f"Dokumente:\n{_json.dumps(doc_entries, ensure_ascii=False, indent=1)}"
        )

        try:
            loop = _asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None, self.llm.complete, system_prompt, user_msg, "json"
            )
            classifications = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception as e:
            self._log(f"CRAG Klassifikation fehlgeschlagen ({type(e).__name__}), überspringe Filter")
            return ranked, scraped, [f"CRAG: Klassifikation fehlgeschlagen ({type(e).__name__})"]

        # Parse Ergebnisse
        label_by_url: dict[str, str] = {}
        if isinstance(classifications, list):
            for entry in classifications:
                if isinstance(entry, dict) and "url" in entry and "label" in entry:
                    label_by_url[entry["url"]] = entry.get("label", "CORRECT").upper()

        # Filtern
        incorrect_urls: set[str] = set()
        ambiguous_urls: set[str] = set()
        for url, label in label_by_url.items():
            if label == "INCORRECT":
                incorrect_urls.add(url)
            elif label == "AMBIGUOUS":
                ambiguous_urls.add(url)

        total = len(doc_entries)
        incorrect_count = len(incorrect_urls)
        incorrect_rate = incorrect_count / total if total > 0 else 0.0

        notes.append(
            f"CRAG: {total} Dokumente klassifiziert → "
            f"CORRECT={total - incorrect_count - len(ambiguous_urls)}, "
            f"AMBIGUOUS={len(ambiguous_urls)}, INCORRECT={incorrect_count} "
            f"(Rate={incorrect_rate:.0%})"
        )

        # INCORRECT-Dokumente entfernen
        filtered_ranked = [
            rs for rs in ranked
            if rs.result.url not in incorrect_urls
        ]
        filtered_scraped = [
            sc for sc in scraped
            if sc.url not in incorrect_urls
        ]

        if incorrect_count > 0:
            self._log(f"CRAG: {incorrect_count}/{total} irrelevante Dokumente entfernt")

        return filtered_ranked, filtered_scraped, notes

    def _build_evidence_items(
        self,
        ranked: list[RankedSource],
        scraped: list[ScrapedSource],
        claim_text: str,
        gfc_matches: list[GoogleFactCheckMatch],
        profile: ClaimSearchProfile | None = None,
        is_current_state: bool = False,
        ce_scores: dict[str, float] | None = None,
    ) -> list[EvidenceItem]:
        """Baue EvidenceItems aus gescrapten Quellen.

        Trust Boundary: Nur strukturierte Excerpts (max. 800 Zeichen) werden
        in EvidenceItems übernommen – kein roher HTML-Inhalt.

        Profil wird durchgereicht für strukturiertes Anchor-Ranking.
        publication_date wird aus gescrapten Quellen in EvidenceSource übernommen.
        """
        scraped_by_url = {s.url: s for s in scraped}
        items: list[EvidenceItem] = []

        for rs in ranked:
            sc = scraped_by_url.get(rs.result.url)
            url = rs.result.url
            tier = _domain_tier(url)
            domain = _extract_domain(url)

            # Excerpt-Priorisierung:
            # 1. Gescrapte Passage (höchste Qualität)
            # 2. LangSearch-Volltext aus SearchResult.content (gut strukturiert)
            # 3. Snippet (Fallback)
            if sc and sc.fetch_success and sc.passage:
                raw_excerpt = sc.passage
                extraction_conf = 0.8
            elif rs.result.content:
                raw_excerpt = rs.result.content
                extraction_conf = 0.6
            else:
                raw_excerpt = rs.result.snippet
                extraction_conf = 0.3

            # Trust Boundary: relevante Passage statt stumpfem Cutoff
            excerpt = _extract_best_excerpt(raw_excerpt, claim_text, max_chars=800) if raw_excerpt else ""

            # publication_date aus gescraptem Inhalt übernehmen
            pub_date = ""
            if sc and sc.fetch_success:
                pub_date = getattr(sc, "publication_date", "") or ""

            source = EvidenceSource(
                url=url,
                title=rs.result.title,
                domain=domain,
                domain_tier=tier,
                is_fact_check_org=_is_fact_check_org(url),
                is_primary_source=(tier <= 2),
                publication_date=pub_date,
            )

            # Evidence-Typing: claim_scope_score + evidence_type
            rel_score = _relevance_score(rs.result, claim_text, profile)
            scope_text = excerpt if excerpt else f"{rs.result.title} {rs.result.snippet}"
            scope_score = _compute_claim_scope_score(scope_text, profile)
            is_low_trust = _is_low_trust_site(url, rs.result.title, rs.result.snippet)
            ev_type = _classify_evidence_type(
                item_relevance=rel_score,
                claim_scope=scope_score,
                domain_tier=tier,
                is_fact_check=_is_fact_check_org(url),
                is_low_trust=is_low_trust,
                min_direct_scope=self.config.evidence_retrieval.claim_scope_min_direct,
            )

            direction = _classify_source_direction(
                excerpt=excerpt,
                relevance_score=rel_score,
                evidence_type=ev_type,
                is_low_trust=is_low_trust,
            )
            # supports_claim wird aus source_direction abgeleitet (Rückwärtskompatibilität)
            supports_claim_derived: bool | None = (
                True if direction == SourceDirection.SUPPORTS
                else False if direction == SourceDirection.REFUTES
                else None
            )

            item = EvidenceItem(
                source=source,
                excerpt=excerpt,
                relevance_score=rel_score,
                extraction_confidence=extraction_conf,
                supports_claim=supports_claim_derived,
                source_direction=direction,
                evidence_type=ev_type,
                claim_scope_score=scope_score,
            )
            items.append(item)

        # Nach Ranking-Score sortieren (Tier + Relevanz + Profil-Anker + Off-topic)
        # Direktes Ranking auf EvidenceItems – Metadaten bleiben erhalten
        items = _rank_evidence_items(
            items,
            claim_text,
            gfc_matches,
            profile=profile,
            is_current_state=is_current_state,
            ce_scores=ce_scores,
        )

        # Phase 6: Perspektiv-Clustering für Evidenz-Diversität
        items = _cluster_by_perspective(items, target=8)

        return items

    async def _fallback_retrieval(
        self,
        claim: Claim,
        original_queries: list[str],
    ) -> list[SearchResult]:
        """Fallback-Suche mit alternativen Queries – LangSearch + SearXNG.

        Priorität im Fallback:
            1. LangSearch (semantisch stark, immer aktiv wenn enabled)
            2. SearXNG (breit, kostenlos)
        """
        from agents.fact_checker import _build_fallback_queries
        fallback_queries = _build_fallback_queries(claim, original_queries)
        if not fallback_queries:
            return []

        # LangSearch und SearXNG parallel starten
        tasks: list = [
            self._searxng.multi_search_async(
                fallback_queries, max_results=self.config.searxng.max_results,
            )
        ]
        if self.config.langsearch.enabled:
            tasks.append(
                self._langsearch.multi_search_async(
                    fallback_queries[:2],
                    max_results=self.config.langsearch.max_results,
                )
            )

        result_maps = await asyncio.gather(*tasks, return_exceptions=True)
        combined: list[SearchResult] = []
        for rm in result_maps:
            if isinstance(rm, dict):
                combined.extend(r for results in rm.values() for r in results)
        return combined
