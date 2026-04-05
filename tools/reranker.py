"""Cross-Encoder Re-Ranking für semantische Relevanz-Bewertung.

Nutzt ein multilinguales Cross-Encoder-Modell (mmarco-mMiniLMv2) um
Suchergebnisse nach semantischer Relevanz zum Claim zu sortieren.
Das Modell wird lazy geladen und als Singleton gehalten.

Graceful Degradation: Wenn sentence-transformers nicht installiert ist,
gibt ``rerank()`` die Ergebnisse unverändert mit neutralem Score (0.5) zurück.

Modell-Konfiguration:
    CROSS_ENCODER_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
    (Default, multilingual, gute deutsche Abdeckung, ~50ms/Paar CPU)
"""

from __future__ import annotations

import os
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.web_search import SearchResult

logger = logging.getLogger(__name__)

# Sentinel: None = nicht geladen, False = Laden fehlgeschlagen
_model: object | None = None


def _get_model():
    """Lade Cross-Encoder beim ersten Aufruf. Gibt None zurück bei Fehler."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import CrossEncoder
            name = os.getenv(
                "CROSS_ENCODER_MODEL",
                "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
            )
            logger.info("Lade Cross-Encoder: %s", name)
            _model = CrossEncoder(name)
            logger.info("Cross-Encoder geladen.")
        except (ImportError, Exception) as e:
            logger.warning("Cross-Encoder nicht verfügbar: %s", e)
            _model = False  # Sentinel: Laden versucht, fehlgeschlagen
    return _model if _model is not False else None


def reranker_available() -> bool:
    """Prüfe ob der Cross-Encoder verfügbar ist."""
    return _get_model() is not None


def rerank(
    claim_text: str,
    results: list["SearchResult"],
    top_k: int = 30,
    topic_context: str = "",
) -> list[tuple["SearchResult", float]]:
    """Re-ranke Suchergebnisse nach semantischer Relevanz.

    Args:
        claim_text: Der zu prüfende Claim-Text.
        results: Liste von SearchResult-Objekten.
        top_k: Maximale Anzahl zu bewertender Ergebnisse.
        topic_context: Optionaler Artikelthema-Kontext für Disambiguierung.
            Wird als " | Kontext: {topic_context}" an den Claim angehängt.

    Returns:
        Liste von (SearchResult, score) Tupeln, sortiert nach Score absteigend.
        Score ist auf [0, 1] normalisiert (Sigmoid).
    """
    model = _get_model()
    candidates = results[:top_k]

    if model is None or not candidates:
        return [(r, 0.5) for r in candidates]

    # Topic-Kontext für Disambiguierung generischer Claims anhängen
    query = claim_text
    if topic_context:
        query = f"{claim_text} | Kontext: {topic_context}"

    # Paare bilden: (Query, Titel + Snippet)
    pairs = [
        (query, f"{r.title} {r.snippet}")
        for r in candidates
    ]

    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as e:
        logger.warning("Cross-Encoder predict fehlgeschlagen: %s", e)
        return [(r, 0.5) for r in candidates]

    # Normalisierung auf [0, 1] via Sigmoid
    try:
        import numpy as np
        normalized = 1.0 / (1.0 + np.exp(-scores))
    except ImportError:
        import math
        normalized = [1.0 / (1.0 + math.exp(-float(s))) for s in scores]

    ranked = sorted(
        zip(candidates, normalized),
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked
