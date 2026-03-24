"""Claim Extractor – Dünne Fassade über die mehrstufige ClaimProcessingPipeline.

Diese Klasse behält die bisherige öffentliche API für Abwärtskompatibilität:
    execute(text) -> ClaimExtractionResult

Intern delegiert sie an den ClaimProcessorAgent, der die vollständige
6-Stufen-Pipeline ausführt. Das Ergebnis (ClaimProcessingResult) wird
als ClaimExtractionResult zurückgegeben, enthält aber alle neuen Felder
(canonical_text, priority_score etc.) in den ProcessedClaim-Objekten.

Für neuen Code: ClaimProcessorAgent direkt nutzen um ClaimProcessingResult
zu erhalten.
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from agents.claim_processor import ClaimProcessorAgent
from models.schemas import ClaimExtractionResult, ClaimProcessingResult


class ClaimExtractorAgent(BaseAgent):
    """Fassade über ClaimProcessorAgent für Abwärtskompatibilität.

    Gibt ClaimExtractionResult zurück (Typ-Alias für ClaimProcessingResult
    mit identischem claims-Feld).
    """

    name = "Claim Extractor"
    emoji = "🔍"

    def __init__(self, *args, **kwargs) -> None:
        llm_small = kwargs.pop("llm_small", None)
        super().__init__(*args, **kwargs)
        self._processor = ClaimProcessorAgent(*args, llm_small=llm_small, **kwargs)

    def execute(self, input_data: Any, context: str = "") -> ClaimProcessingResult:
        """Extrahiere und verarbeite Claims aus dem Text.

        Gibt ClaimProcessingResult zurück (ist Obermenge von
        ClaimExtractionResult – alle Felder kompatibel).
        """
        result: ClaimProcessingResult = self._processor.execute(input_data, context)
        self._log(
            f"{len(result.claims)} Claims extrahiert, "
            f"{len(result.implicit_claims)} implizite"
        )
        return result
