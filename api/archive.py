"""Archive endpoints: list, get, delete, stats."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from i18n import t

from .dependencies import get_archive

router = APIRouter()


@router.get("/api/archive")
async def list_archive(
    limit: int = 50,
    offset: int = 0,
    rating: str | None = None,
    search: str | None = None,
) -> dict:
    """Liste vergangene Analysen auf (neueste zuerst).

    Query-Parameter:
        limit:  Max. Eintraege pro Seite (1-100, default 50)
        offset: Ueberspringe N Eintraege (Pagination)
        rating: Filter nach Bewertung ("Wahr", "Irrefuehrend", etc.)
        search: Volltextsuche in Titel, Zusammenfassung, URL
    """
    archive = get_archive()
    return archive.list(
        limit=limit,
        offset=offset,
        rating_filter=rating,
        search=search,
    )


@router.get("/api/archive/{archive_id}")
async def get_archive_entry(archive_id: str) -> dict:
    """Hole einen vollstaendigen Archiv-Eintrag mit allen Details."""
    archive = get_archive()
    entry = archive.get(archive_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=t("api.errors.archive_not_found"))
    return entry


@router.delete("/api/archive/{archive_id}")
async def delete_archive_entry(archive_id: str) -> dict:
    """Loesche einen einzelnen Archiv-Eintrag."""
    archive = get_archive()
    deleted = archive.delete(archive_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=t("api.errors.archive_not_found"))
    return {"deleted": True}


@router.get("/api/archive-stats")
async def archive_stats() -> dict:
    """Statistiken ueber das Archiv (Anzahl, Verteilung, etc.)."""
    archive = get_archive()
    return archive.stats()
