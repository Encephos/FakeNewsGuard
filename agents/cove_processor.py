"""Chain-of-Verification (CoVe) Prozessor.

Implementiert den CoVe-Ablauf pro Claim:
    1. Baseline-Einschätzung aus EvidencePack
    2. Verifikationsfragen generieren (2–5 Fragen)
    3. Fragen unabhängig von der Baseline beantworten
    4. Baseline + Verifikationsantworten reconcilien
    5. CoVeTrace zurückgeben

Budget-Kontrolle:
    - MAX_VERIFICATION_QUESTIONS (config.cove.max_verification_questions)
    - MAX_ADDITIONAL_VERIFICATION_SEARCHES (config.cove.max_additional_searches)
    - Budget=0 → CoVe übersprungen

Architekturentscheidung:
    CoVeProcessor ist kein eigenständiger Agent (kein BaseAgent), da er
    ausschließlich auf dem strukturierten EvidencePack arbeitet und kein
    eigenes Retrieval betreibt. Er wird vom VerdictAgent orchestriert.
    Für zusätzliche Retrieval-Runden nutzt er den übergebenen EvidenceBuilderAgent.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from config import CoVeConfig
from models.evidence_models import EvidencePack
from models.schemas import Claim, FactRating
from models.verdict_models import (
    BaselineAssessment,
    CoVeTrace,
    VerificationAnswer,
    VerificationCategory,
    VerificationQuestion,
)
from tools.llm import LLMClient


# ── Prompts ───────────────────────────────────────────────────────────────────

_BASELINE_PROMPT = """\
Du bist ein Fact-Checker. Erzeuge eine VORLÄUFIGE Einschätzung eines Claims
basierend auf den bereitgestellten Fakten.

WICHTIG: Dies ist eine ERSTE EINSCHÄTZUNG, kein endgültiges Urteil.
Sei konservativ: Wenn die Evidenz unklar ist, wähle UNVERIFIABLE.

## Bewertungsskala
TRUE | MOSTLY_TRUE | MISLEADING | MOSTLY_FALSE | FALSE | UNVERIFIABLE

## Output-Format (JSON)
{
  "rating": "MISLEADING",
  "reasoning": "Begründung der Einschätzung (2-3 Sätze)",
  "confidence": 0.6,
  "main_evidence_urls": ["url1", "url2"]
}
"""

_QUESTION_GENERATOR_PROMPT_TEMPLATE = """\
Du bist ein Verifikations-Stratege. Generiere gezielte Verifikationsfragen
für einen Claim, um die vorläufige Einschätzung zu überprüfen.

WICHTIG: Fragen sollen die SCHWACHSTELLEN der Baseline aufdecken, nicht bestätigen.

## Fragetypen
- number: Genaue Zahlen, Statistiken, Messgrößen
- timeframe: Zeitraum, Aktualität, Vergleichsjahr
- source: Primärquelle, Studienursprung, Zuständigkeit
- causality: Kausalität vs. Korrelation, Alternativerklärungen
- definition: Begriff-/Kategoriendefinition, Abgrenzung
- comparison: Vergleichsbasis, Referenzgröße, Normierung
- context: Fehlender Kontext, Einschränkungen, Ausnahmen

## Regeln
1. Generiere MAX_QUESTIONS_PLACEHOLDER Fragen (Priorität: 1=hoch, 3=niedrig)
2. Jede Frage soll eigenständig beantwortbar sein
3. Bevorzuge Fragen, bei denen die Antwort die Baseline widerlegen könnte

## Output-Format (JSON)
Antworte mit einem JSON-Objekt:
questions: Array von Fragen mit question_id, text, category, rationale, priority
"""

_ANSWER_PROMPT = """\
Du bist ein unabhängiger Fact-Checker. Beantworte die gegebene Frage
NUR anhand der bereitgestellten Evidenz.

WICHTIG:
- Paraphrasiere NICHT einfach die Baseline-Einschätzung
- Antworte nur mit dem, was du aus der Evidenz belegen kannst
- Wenn du die Frage nicht beantworten kannst: answer_found_in_evidence=false

## Output-Format (JSON)
{
  "answer": "Konkrete Antwort auf die Frage",
  "confidence": 0.7,
  "supporting_evidence_urls": ["url1"],
  "supporting_excerpt": "Relevanter Textauszug",
  "contradicts_baseline": false,
  "answer_found_in_evidence": true
}
"""

_RECONCILIATION_PROMPT = """\
Du bist ein Fact-Checker. Reconciliere die Baseline-Einschätzung mit den
unabhängig beantworteten Verifikationsfragen.

## Regeln
1. Wenn Verifikationsantworten die Baseline bestätigen → Konfidenz erhöhen
2. Wenn Verifikationsantworten widersprechen → Konfidenz senken, ggf. Rating ändern
3. Wenn mehrere Antworten widersprechen → Rating kann sich ändern
4. Unbeantwortete Fragen → Konfidenz senken

## Output-Format (JSON)
{
  "final_rating": "MISLEADING",
  "final_confidence": 0.7,
  "rating_changed": false,
  "confidence_delta": -0.1,
  "contradictions_found": ["Beschreibung erkannter Widersprüche"],
  "unanswered_questions": ["Q2", "Q3"]
}
"""


class CoVeProcessor:
    """Orchestriert den Chain-of-Verification Prozess.

    Arbeitet ausschließlich auf EvidencePack – kein direktes Retrieval.
    Für zusätzliche Retrieval-Runden (max. config.cove.max_additional_searches)
    kann optional ein EvidenceBuilderAgent übergeben werden.
    """

    def __init__(
        self,
        llm: LLMClient,
        config: CoVeConfig,
        llm_small: LLMClient | None = None,
        evidence_builder: Any | None = None,
    ) -> None:
        self.llm = llm
        self.llm_small = llm_small or llm
        self.config = config
        self._evidence_builder = evidence_builder

    def process(
        self,
        claim: Claim,
        evidence_pack: EvidencePack,
    ) -> CoVeTrace:
        """Führe den vollständigen CoVe-Prozess für einen Claim durch.

        Returns:
            CoVeTrace mit Baseline, Verifikationsfragen/-antworten und
            Reconciliation-Ergebnis.
        """
        if not self.config.enabled or self.config.max_verification_questions == 0:
            _log(f"CoVe für {claim.id} übersprungen (disabled oder Budget=0)")
            return self._empty_trace(claim, evidence_pack)

        # ── Phase 1: Baseline ─────────────────────────────────────────────────
        baseline = self._generate_baseline(claim, evidence_pack)
        _log(f"Baseline für {claim.id}: {baseline.rating} (conf={baseline.confidence:.2f})")

        # ── Phase 2: Verifikationsfragen ──────────────────────────────────────
        questions = self._generate_questions(claim, evidence_pack, baseline)
        _log(f"{len(questions)} Verifikationsfragen für {claim.id} generiert")

        if not questions:
            return CoVeTrace(
                claim_id=claim.id,
                baseline=baseline,
                verification_questions=[],
                verification_answers=[],
            )

        # ── Phase 3: Unabhängige Antworten ────────────────────────────────────
        answers = self._answer_questions(questions, evidence_pack, baseline)
        _log(f"{sum(1 for a in answers if a.answer_found_in_evidence)} von {len(answers)} Fragen beantwortet")

        # ── Phase 4: Reconciliation ───────────────────────────────────────────
        trace = self._reconcile(claim, baseline, questions, answers)
        _log(
            f"Reconciliation {claim.id}: {trace.baseline.rating} → "
            f"delta={trace.confidence_delta:.2f}, "
            f"changed={trace.final_rating_changed}"
        )
        return trace

    def _generate_baseline(
        self,
        claim: Claim,
        pack: EvidencePack,
    ) -> BaselineAssessment:
        user_msg = (
            f"## Claim\n\n{claim.text}\n\n"
            f"## Evidenz\n\n{pack.format_for_verdict()}"
        )
        raw = self._llm_json(_BASELINE_PROMPT, user_msg)
        try:
            rating = FactRating(raw.get("rating", "UNVERIFIABLE")).value
        except ValueError:
            rating = "UNVERIFIABLE"
        return BaselineAssessment(
            rating=rating,
            reasoning=raw.get("reasoning", ""),
            confidence=float(raw.get("confidence", 0.5)),
            main_evidence_used=raw.get("main_evidence_urls", []),
        )

    def _generate_questions(
        self,
        claim: Claim,
        pack: EvidencePack,
        baseline: BaselineAssessment,
    ) -> list[VerificationQuestion]:
        max_q = self.config.max_verification_questions
        prompt = _QUESTION_GENERATOR_PROMPT_TEMPLATE.replace(
            "MAX_QUESTIONS_PLACEHOLDER", str(max_q)
        )
        user_msg = (
            f"## Claim\n\n{claim.text}\n\n"
            f"## Baseline-Einschätzung\n\n"
            f"Rating: {baseline.rating}, Konfidenz: {baseline.confidence:.2f}\n"
            f"Begründung: {baseline.reasoning}\n\n"
            f"## Verfügbare Evidenz (Zusammenfassung)\n\n"
            f"{pack.format_for_verdict()[:1500]}"
        )
        raw = self._llm_json(prompt, user_msg, llm=self.llm_small)
        questions: list[VerificationQuestion] = []
        for q in raw.get("questions", [])[:max_q]:
            try:
                cat = VerificationCategory(q.get("category", "other"))
            except ValueError:
                cat = VerificationCategory.OTHER
            questions.append(VerificationQuestion(
                question_id=str(q.get("question_id", f"Q{len(questions)+1}")),
                text=q.get("text", ""),
                category=cat,
                rationale=q.get("rationale", ""),
                priority=int(q.get("priority", 2)),
            ))
        return [q for q in questions if q.text]

    def _answer_questions(
        self,
        questions: list[VerificationQuestion],
        pack: EvidencePack,
        baseline: BaselineAssessment,
    ) -> list[VerificationAnswer]:
        evidence_summary = pack.format_for_verdict()
        answers: list[VerificationAnswer] = []

        for q in questions:
            user_msg = (
                f"## Zu beantwortende Frage ({q.question_id})\n\n"
                f"{q.text}\n\n"
                f"## Baseline-Einschätzung (NUR als Kontext, NICHT paraphrasieren)\n\n"
                f"Rating: {baseline.rating}, Begründung: {baseline.reasoning[:200]}\n\n"
                f"## Evidenz\n\n{evidence_summary}"
            )
            raw = self._llm_json(_ANSWER_PROMPT, user_msg, llm=self.llm_small)
            answers.append(VerificationAnswer(
                question_id=q.question_id,
                answer=raw.get("answer", ""),
                confidence=float(raw.get("confidence", 0.5)),
                supporting_evidence_urls=raw.get("supporting_evidence_urls", []),
                supporting_excerpt=raw.get("supporting_excerpt", "")[:400],
                contradicts_baseline=bool(raw.get("contradicts_baseline", False)),
                answer_found_in_evidence=bool(raw.get("answer_found_in_evidence", True)),
            ))

        return answers

    def _reconcile(
        self,
        claim: Claim,
        baseline: BaselineAssessment,
        questions: list[VerificationQuestion],
        answers: list[VerificationAnswer],
    ) -> CoVeTrace:
        # Zusammenfassung für LLM
        qa_summary = "\n".join(
            f"Q{i+1} ({q.category.value}): {q.text}\n"
            f"  Antwort: {a.answer}\n"
            f"  Widerspricht Baseline: {a.contradicts_baseline}\n"
            f"  Beantwortet: {a.answer_found_in_evidence}"
            for i, (q, a) in enumerate(zip(questions, answers))
        )
        user_msg = (
            f"## Claim\n\n{claim.text}\n\n"
            f"## Baseline\n\nRating: {baseline.rating}, "
            f"Konfidenz: {baseline.confidence:.2f}\n"
            f"Begründung: {baseline.reasoning}\n\n"
            f"## Verifikations-Q&A\n\n{qa_summary}"
        )
        raw = self._llm_json(_RECONCILIATION_PROMPT, user_msg)

        try:
            final_rating = FactRating(raw.get("final_rating", baseline.rating)).value
        except ValueError:
            final_rating = baseline.rating

        final_confidence = float(raw.get("final_confidence", baseline.confidence))
        confidence_delta = final_confidence - baseline.confidence
        rating_changed = final_rating != baseline.rating
        contradictions = raw.get("contradictions_found", [])
        unanswered = raw.get("unanswered_questions", [])

        # Baseline mit finalem Rating aktualisieren (für Zugriff durch VerdictAgent)
        final_baseline = BaselineAssessment(
            rating=final_rating,
            reasoning=baseline.reasoning,
            confidence=final_confidence,
            main_evidence_used=baseline.main_evidence_used,
        )

        return CoVeTrace(
            claim_id=claim.id,
            baseline=final_baseline,
            verification_questions=questions,
            verification_answers=answers,
            contradictions_found=contradictions,
            confidence_delta=confidence_delta,
            final_rating_changed=rating_changed,
            unanswered_questions=unanswered,
        )

    def _empty_trace(self, claim: Claim, pack: EvidencePack) -> CoVeTrace:
        """Leerer Trace wenn CoVe deaktiviert."""
        return CoVeTrace(
            claim_id=claim.id,
            baseline=BaselineAssessment(
                rating="UNVERIFIABLE",
                reasoning="CoVe nicht aktiv",
                confidence=0.0,
            ),
        )

    def _llm_json(self, prompt: str, user_msg: str, llm: LLMClient | None = None) -> dict:
        _llm = llm or self.llm
        try:
            raw = _llm.complete(prompt, user_msg, response_format="json")
            if isinstance(raw, str):
                return json.loads(raw)
            return raw
        except Exception as e:
            _log(f"LLM-Fehler: {type(e).__name__}: {e}")
            return {}


def _log(msg: str) -> None:
    print(f"  [CoVe] {msg}", file=sys.stderr)
