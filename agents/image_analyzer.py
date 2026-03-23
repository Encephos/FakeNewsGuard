"""Image Analyzer – Analysiert Bilder aus Social-Media-Posts auf Fake-News-relevante Inhalte."""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from i18n import t
from models.schemas import ImageAnalysisItem, ImageAnalysisResult


class ImageAnalyzerAgent(BaseAgent):
    name = "Image Analyzer"
    emoji = "🖼️"

    def execute(self, input_data: Any, context: str = "") -> ImageAnalysisResult:
        """Analysiere Bilder aus einem Social-Media-Post.

        Args:
            input_data: dict mit Schlüsseln:
                - "image_urls": list[str] – Bild-URLs (max. 5)
                - "post_text": str – Begleittext des Posts (Kontext)
            context: Optionaler zusätzlicher Kontext.

        Returns:
            ImageAnalysisResult mit Analyse aller Bilder.
        """
        image_urls: list[str] = input_data.get("image_urls", [])
        post_text: str = input_data.get("post_text", "")

        if not image_urls:
            return ImageAnalysisResult(
                overall_assessment=t("agents.image_analyzer.no_items")
            )

        # Max. 5 Bilder pro Call
        image_urls = image_urls[:5]

        system_prompt = t("agents.image_analyzer.system_prompt")
        user_message = t("agents.image_analyzer.analyze_prefix").format(
            post_text=post_text[:500] if post_text else "(kein Begleittext)",
            count=len(image_urls),
        )
        if context:
            user_message += f"\n\n## Zusätzlicher Kontext\n\n{context}"

        raw = self._llm_vision(system_prompt, user_message, image_urls)

        items: list[ImageAnalysisItem] = []
        for raw_item in raw.get("items", []):
            try:
                items.append(
                    ImageAnalysisItem(
                        image_index=int(raw_item.get("image_index", 0)),
                        ocr_text=raw_item.get("ocr_text", ""),
                        visible_elements=raw_item.get("visible_elements", []),
                        manipulation_signs=raw_item.get("manipulation_signs", []),
                        emotional_framing=raw_item.get("emotional_framing", ""),
                        infographic_data=raw_item.get("infographic_data", ""),
                        context_clues=raw_item.get("context_clues", []),
                    )
                )
            except Exception as e:
                self._log(f"Überspringe ungültiges Bild-Item: {e}")

        result = ImageAnalysisResult(
            items=items,
            cross_image_observations=raw.get("cross_image_observations", ""),
            overall_assessment=raw.get("overall_assessment", ""),
        )

        self._log(t("agents.image_analyzer.analyzed").format(count=len(items)))
        return result
