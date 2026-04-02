"""PDF export endpoints."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from i18n import t

from .dependencies import get_archive

router = APIRouter()


@router.get("/export/pdf/{archive_id}")
async def export_pdf(archive_id: str) -> Response:
    """Exportiere einen Archiv-Eintrag als PDF-Report.

    Gibt das PDF als Download zurueck (Content-Disposition: attachment).
    """
    archive = get_archive()
    entry = archive.get(archive_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=t("api.errors.archive_not_found"))

    from tools.pdf_export import generate_pdf

    result = entry.get("result", {})
    title = entry.get("title", "Faktencheck-Report")
    source_url = entry.get("source_url", "")

    pdf_bytes = generate_pdf(result, title=title, source_url=source_url or "")

    # Dateiname aus Titel ableiten (sanitized)
    safe_title = re.sub(r"[^\w\s-]", "", title or "report")[:50].strip().replace(" ", "_")
    filename = f"faktencheck_{safe_title}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/export/pdf")
async def export_pdf_from_result(req: dict) -> Response:
    """Exportiere ein Analyse-Ergebnis direkt als PDF (ohne Archiv).

    Erwartet im Body: { result: {...}, title?: str, source_url?: str }
    Nuetzlich fuer den Export direkt aus einem laufenden Job.
    """
    result = req.get("result")
    if not result:
        raise HTTPException(status_code=400, detail=t("api.errors.no_result"))

    from tools.pdf_export import generate_pdf

    title = req.get("title", "Faktencheck-Report")
    source_url = req.get("source_url", "")

    pdf_bytes = generate_pdf(result, title=title, source_url=source_url)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="faktencheck_report.pdf"',
        },
    )
