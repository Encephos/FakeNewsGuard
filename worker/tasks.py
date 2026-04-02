"""Celery tasks — analysis pipeline execution.

The ``run_analysis`` task is the direct replacement for the former
``api.analysis._run_job()`` async function.  It runs **synchronously**
inside a Celery worker process, writing progress to Redis via
:class:`worker.job_store.JobStore`.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from worker.celery_app import celery_app
from worker.job_store import get_job_store

from config import AppConfig, ScoutTier
from config.infrastructure import JobConfig
from i18n import t
from orchestrator import Orchestrator
from tools.content_extractor import ContentExtractor, detect_platform
from tools.logger import get_logger

logger = get_logger("fng-worker")

_job_config = JobConfig()
_JOB_INACTIVITY_TIMEOUT = _job_config.inactivity_timeout


def _format_image_analysis(result: Any) -> str:
    """Konvertiert ImageAnalysisResult in lesbaren Text-Block fuer LLM-Kontext."""
    parts: list[str] = []
    for item in result.items:
        idx = item.image_index + 1
        parts.append(f"### Bild {idx}")
        if item.ocr_text:
            parts.append(f"**Sichtbarer Text:** {item.ocr_text}")
        if item.visible_elements:
            parts.append(f"**Erkannte Elemente:** {', '.join(item.visible_elements)}")
        if item.manipulation_signs:
            parts.append(f"**Manipulationsanzeichen:** {', '.join(item.manipulation_signs)}")
        if item.emotional_framing:
            parts.append(f"**Emotionales Framing:** {item.emotional_framing}")
        if item.infographic_data:
            parts.append(f"**Infografik-Daten:** {item.infographic_data}")
        if item.context_clues:
            parts.append(f"**Kontexthinweise:** {', '.join(item.context_clues)}")
        parts.append("")
    if result.cross_image_observations:
        parts.append(f"**Zusammenspiel der Bilder:** {result.cross_image_observations}")
    if result.overall_assessment:
        parts.append(f"**Gesamteinschaetzung:** {result.overall_assessment}")
    return "\n".join(parts).strip()


@celery_app.task(bind=True, name="fakenewsguard.analyze")
def run_analysis(self, job_id: str, text: str, url: str = "", tier: str = "max") -> None:  # noqa: C901
    """Run the full analysis pipeline, writing progress to Redis."""
    store = get_job_store()
    step_counter = 0

    def push_step(phase: str, agent: str, message: str, status: str = "done") -> None:
        nonlocal step_counter
        step_counter += 1
        store.push_step(job_id, {
            "id": f"step-{step_counter}",
            "phase": phase,
            "agent": agent,
            "emoji": "",
            "message": message,
            "status": status,
            "timestamp": int(time.time() * 1000),
        })

    try:
        store.update(job_id, status="running")
        scout_tier = ScoutTier(tier)
        config = AppConfig(verbose=True, tier=scout_tier)
        orchestrator = Orchestrator(config)
        claims_map: dict[str, Any] = {}

        # ── Phase 0: URL Content Extraction ───────────────────────
        content = None
        if url:
            platform = detect_platform(url)
            push_step("Phase 0", "Content Extractor",
                       t("api.steps.extracting_content").format(platform=platform.capitalize()), "running")
            try:
                extractor = ContentExtractor(media_config=config.media)
                content = extractor.extract(url)
                extracted_text = extractor.format_for_analysis(content)
                push_step(
                    "Phase 0", "Content Extractor",
                    f"Inhalt extrahiert: {content.title[:80] or content.text[:80]}..."
                    + (f" ({len(content.images)} Bilder)" if content.images else ""),
                )
                store.update(job_id, extracted_content={
                    "platform": content.platform,
                    "title": content.title,
                    "author": content.author,
                    "images": content.images[:5],
                    "url": content.url,
                })
                if text:
                    text = f"{text}\n\n--- Extrahierter Inhalt von {url} ---\n\n{extracted_text}"
                else:
                    text = extracted_text
            except Exception as e:
                push_step("Phase 0", "Content Extractor", f"Extraktion fehlgeschlagen: {e}", "error")
                if not text:
                    store.set_error(job_id, f"Inhalt konnte nicht extrahiert werden: {e}")
                    return

        # ── Phase 0.5: Bildanalyse (Social Media) ────────────────
        _VISION_PLATFORMS = {"twitter", "instagram", "threads"}
        image_analysis_text = ""

        if content is not None and content.images and content.platform in _VISION_PLATFORMS:
            img_urls = content.images[:5]
            push_step("Phase 0.5", "Image Analyzer",
                       t("api.steps.analyzing_images").format(count=len(img_urls)), "running")
            img_input = {"image_urls": img_urls, "post_text": content.text}
            img_result, img_error = orchestrator.image_analyzer.run_safe(img_input)
            if img_error:
                push_step("Phase 0.5", "Image Analyzer",
                           t("api.steps.image_analysis_failed").format(error=img_error), "error")
            elif img_result is not None:
                image_analysis_text = _format_image_analysis(img_result)
                push_step("Phase 0.5", "Image Analyzer",
                           t("api.steps.images_analyzed").format(count=len(img_result.items)))
                text += f"\n\n--- Bildanalyse ---\n\n{image_analysis_text}"
                job_data = store.get(job_id)
                extracted = job_data.get("extracted_content") if job_data else None
                if extracted and isinstance(extracted, dict):
                    extracted["image_analysis"] = {
                        "items": [item.model_dump() for item in img_result.items],
                        "overall_assessment": img_result.overall_assessment,
                        "cross_image_observations": img_result.cross_image_observations,
                    }
                    store.update(job_id, extracted_content=extracted)

        # ── Phase 1: Claim Extraction ─────────────────────────────
        push_step("Phase 1", "Claim Extractor", t("api.steps.extracting_claims"), "running")
        extraction = orchestrator.claim_extractor.run(text)
        claims_map = {
            c.id: {"text": c.text, "type": c.type.value}
            for c in extraction.claims
        }
        push_step("Phase 1", "Claim Extractor",
                   f"{len(extraction.claims)} Claims extrahiert, {len(extraction.implicit_claims)} implizite")

        if not extraction.claims:
            from models.schemas import OverallRating, SynthesisResult
            result = SynthesisResult(
                overall_rating=OverallRating.RELIABLE,
                confidence=0.3,
                summary=t("api.steps.no_claims_found"),
                sources=[],
            )
            from api.dependencies import transform_result
            store.set_result(job_id, transform_result(result, claims_map))
            return

        for claim in extraction.claims:
            prio_info = f" prio={claim.priority_score:.2f}" if hasattr(claim, "priority_score") else ""
            push_step("Phase 1", "Claim Extractor",
                       f"{claim.id} [{claim.type.value}]{prio_info}: {claim.text}")

        # ── Phase 2 + 3: Fact-check + Rhetoric (parallel) ────────
        from models.schemas import ClaimType

        _CLAIM_BATCH_SIZE = _job_config.claim_batch_size
        fact_checks: list[Any] = []
        number_audits: list[Any] = []
        analysis_errors: list[str] = []

        checkable = orchestrator._select_top_claims(extraction)
        if len(checkable) < len([c for c in extraction.claims if c.type != ClaimType.OPINION]):
            push_step("Phase 1", "Claim Extractor",
                       f"Top-{len(checkable)} von {len(extraction.claims)} Claims ausgewaehlt (konfigurierbar via CLAIM_TOP_N)")

        # Scale inactivity timeout for large inputs
        if len(checkable) > 4:
            store.update(job_id, inactivity_timeout=_JOB_INACTIVITY_TIMEOUT + len(checkable) * 30)

        def _check_claim(claim: Any) -> None:
            """Fact-check + optional number-audit for a single claim."""
            push_step("Phase 2", "Fact Checker",
                       t("api.steps.checking_claim").format(text=claim.text[:80]), "running")
            route_result, routed_claim = orchestrator._router.route_and_apply(claim)
            fc_result, fc_error = orchestrator.fact_checker.run_safe(routed_claim, context=text)
            if fc_error:
                analysis_errors.append(fc_error)
                push_step("Phase 2", "Fact Checker", f"Fehler bei {claim.id}: {fc_error}", "error")
            elif fc_result is not None:
                fact_checks.append(fc_result)
                push_step("Phase 2", "Fact Checker", f"Claim {claim.id}: {fc_result.rating.value}")

            if "number_auditor" in routed_claim.requires_agents or routed_claim.type == ClaimType.STATISTICAL:
                push_step("Phase 2", "Number Auditor",
                           t("api.steps.number_audit").format(id=claim.id), "running")
                fc_context = (
                    f"Fact-Check Ergebnis: {fc_result.rating.value}\nEvidenz: {fc_result.evidence}"
                    if fc_result is not None else ""
                )
                na_result, na_error = orchestrator.number_auditor.run_safe(routed_claim, context=fc_context)
                if na_error:
                    analysis_errors.append(na_error)
                elif na_result is not None:
                    number_audits.append(na_result)
                    push_step("Phase 2", "Number Auditor",
                               f"{claim.id}: {na_result.manipulation_type.value}")

        def _run_rhetoric() -> tuple[Any, str | None]:
            push_step("Phase 3", "Rhetoric Analyzer", t("api.steps.rhetoric_started"), "running")
            return orchestrator.rhetoric_analyzer.run_safe(text, context=image_analysis_text)

        # Process claims in batches + rhetoric in parallel via thread pool
        with ThreadPoolExecutor(max_workers=_CLAIM_BATCH_SIZE + 1) as pool:
            rhetoric_future = pool.submit(_run_rhetoric)

            for batch_start in range(0, len(checkable), _CLAIM_BATCH_SIZE):
                batch = checkable[batch_start:batch_start + _CLAIM_BATCH_SIZE]
                batch_num = batch_start // _CLAIM_BATCH_SIZE + 1
                total_batches = (len(checkable) + _CLAIM_BATCH_SIZE - 1) // _CLAIM_BATCH_SIZE
                if total_batches > 1:
                    push_step("Phase 2", "Fact Checker",
                               f"Batch {batch_num}/{total_batches} ({len(batch)} Claims)...", "running")
                futures = [pool.submit(_check_claim, c) for c in batch]
                for f in as_completed(futures):
                    exc = f.exception()
                    if exc is not None:
                        analysis_errors.append(f"ClaimCheck: {exc}")

            # Wait for rhetoric
            rhetoric_result = None
            try:
                rhet_result, rhet_error = rhetoric_future.result()
                if rhet_error:
                    analysis_errors.append(rhet_error)
                    push_step("Phase 3", "Rhetoric Analyzer", f"Fehler: {rhet_error}", "error")
                elif rhet_result is not None:
                    rhetoric_result = rhet_result
                    push_step("Phase 3", "Rhetoric Analyzer",
                               f"{len(rhet_result.techniques)} Techniken erkannt")
            except Exception as exc:
                analysis_errors.append(f"RhetoricAnalyzer: {exc}")
                push_step("Phase 3", "Rhetoric Analyzer", f"Fehler: {exc}", "error")

        # ── Phase 4: Synthesis ────────────────────────────────────
        push_step("Phase 4", "Synthesizer", t("api.steps.synthesizing"), "running")
        synthesis_input = {
            "original_text": text,
            "fact_checks": fact_checks,
            "number_audits": number_audits,
            "rhetoric": rhetoric_result,
            "image_analysis": image_analysis_text,
        }
        final_result = orchestrator.synthesizer.run(synthesis_input)
        if analysis_errors:
            final_result.analysis_errors.extend(analysis_errors)

        push_step("Phase 4", "Synthesizer", t("api.steps.analysis_done"))

        from api.dependencies import transform_result
        transformed = transform_result(final_result, claims_map)
        store.set_result(job_id, transformed)

        # ── Auto-Archive ──────────────────────────────────────────
        cost = getattr(final_result, "cost_summary", None)
        try:
            from api.dependencies import get_archive, get_graph
            archive = get_archive()
            job_data = store.get(job_id)
            extracted = job_data.get("extracted_content") if job_data else {}
            if not isinstance(extracted, dict):
                extracted = {}
            archive_id = archive.save(
                result=transformed,
                input_text=text,
                source_url=url or extracted.get("url"),
                platform=extracted.get("platform"),
                title=extracted.get("title"),
                cost_summary=cost.model_dump() if cost else None,
            )
            store.update(job_id, archive_id=archive_id)

            # Cross-Reference Graph
            try:
                graph = get_graph()
                graph.populate_from_result(
                    analysis_id=archive_id,
                    claims_analysis=transformed.get("claims", []),
                    original_text=text,
                )
            except Exception:
                logger.warning("Cross-Reference Graph fehlgeschlagen fuer Job %s", job_id, exc_info=True)
        except Exception:
            logger.exception("Auto-Archive fehlgeschlagen fuer Job %s", job_id)

        # ── Usage-Tracking ────────────────────────────────────────
        try:
            job_data = store.get(job_id)
            user_id = job_data.get("user_id") if job_data else None
            if user_id:
                from api.dependencies import get_user_db
                user_db = get_user_db()
                user_db.log_usage(
                    user_id=user_id,
                    tier_used=tier,
                    claims=len(transformed.get("claims", [])),
                    rating=transformed.get("overall_rating"),
                    source="web",
                    total_tokens=cost.total_tokens if cost else 0,
                    estimated_cost_usd=cost.estimated_cost_usd if cost else 0.0,
                    analysis_id=getattr(final_result, "analysis_id", None),
                )
        except Exception:
            pass  # Usage-Tracking darf Analyse nicht brechen

    except SoftTimeLimitExceeded:
        logger.warning("Job %s: Soft time limit exceeded", job_id)
        store.set_error(job_id, t("api.errors.timeout_hard"))
    except Exception as exc:
        logger.exception("Job %s fehlgeschlagen: %s", job_id, exc)
        store.set_error(job_id, f"{type(exc).__name__}: {exc}")


@celery_app.task(name="fakenewsguard.check_stale_jobs")
def check_stale_jobs() -> None:
    """Periodic watchdog: scan running jobs for inactivity and mark them as error."""
    store = get_job_store()
    r = store._r
    cursor = 0
    now = time.time()
    while True:
        cursor, keys = r.scan(cursor, match="fng:job:*", count=100)
        for key in keys:
            k = key.decode() if isinstance(key, bytes) else key
            # Skip step lists
            if k.endswith(":steps"):
                continue
            job_id = k.replace("fng:job:", "")
            status = r.hget(key, "status")
            if status is None:
                continue
            status_str = status.decode() if isinstance(status, bytes) else status
            if status_str not in ("pending", "running"):
                continue
            # Check inactivity
            raw_la = r.hget(key, "last_activity")
            raw_ca = r.hget(key, "created_at")
            if raw_la is None and raw_ca is None:
                continue
            last_act = float(raw_la.decode() if isinstance(raw_la, bytes) else raw_la) if raw_la else 0
            created = float(raw_ca.decode() if isinstance(raw_ca, bytes) else raw_ca) if raw_ca else 0
            raw_it = r.hget(key, "inactivity_timeout")
            inactivity_limit = (
                int(raw_it.decode() if isinstance(raw_it, bytes) else raw_it)
                if raw_it else _JOB_INACTIVITY_TIMEOUT
            )
            idle = now - (last_act or created)
            if idle > inactivity_limit:
                store.set_error(job_id, t("api.errors.timeout_inactivity").format(seconds=int(idle)))
        if cursor == 0:
            break
