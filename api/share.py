"""Share endpoints: create, get, embed, delete public share links."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .dependencies import get_archive, get_current_user

router = APIRouter()

# ── Public-safe fields extracted from result_json ──────────────────────────

_PUBLIC_RESULT_KEYS = {
    "claims",
    "rhetoric",
    "key_corrections",
    "fairness_notes",
    "sources",
}


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    """Filtere result_json auf öffentlich sichere Felder."""
    out: dict[str, Any] = {}
    for key in _PUBLIC_RESULT_KEYS:
        if key in result:
            out[key] = result[key]
    # Aus Claims den rohen Input-Text entfernen, falls vorhanden
    if "claims" in out:
        cleaned = []
        for claim in out["claims"]:
            c = dict(claim)
            c.pop("original_text", None)
            cleaned.append(c)
        out["claims"] = cleaned
    return out


def _build_public_entry(entry: dict[str, Any], share: dict[str, Any]) -> dict[str, Any]:
    """Baue die öffentlich-sichere API-Antwort für einen geteilten Eintrag."""
    result = entry.get("result") or {}
    if isinstance(result, str):
        result = json.loads(result)

    return {
        "token": share["token"],
        "title": entry.get("title"),
        "overall_rating": entry.get("overall_rating"),
        "confidence": entry.get("confidence"),
        "summary": entry.get("summary"),
        "claims_count": entry.get("claims_count"),
        "techniques_count": entry.get("techniques_count"),
        "source_url": entry.get("source_url"),
        "platform": entry.get("platform"),
        "created_at": entry.get("created_at"),
        "allow_embed": bool(share.get("allow_embed")),
        "view_count": share.get("view_count", 0),
        **_public_result(result),
    }


# ── Request models ─────────────────────────────────────────────────────────


class CreateShareRequest(BaseModel):
    expires_days: int | None = None
    allow_embed: bool = False


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post("/archive/{archive_id}/share")
async def create_share(
    archive_id: str,
    body: CreateShareRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Erstelle einen öffentlichen Share-Link für einen Archiv-Eintrag."""
    archive = get_archive()
    entry = archive.get(archive_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Archiv-Eintrag nicht gefunden.")

    share = archive.create_share(
        archive_id=archive_id,
        created_by=current_user["id"],
        expires_days=body.expires_days,
        allow_embed=body.allow_embed,
    )

    base_url = str(request.base_url).rstrip("/")
    return {
        "token": share["token"],
        "share_url": f"/share/{share['token']}",
        "api_url": f"{base_url}/api/v1/share/{share['token']}",
        "embed_url": f"/share/{share['token']}/embed" if body.allow_embed else None,
        "expires_at": share["expires_at"],
        "allow_embed": share["allow_embed"],
    }


@router.get("/archive/{archive_id}/shares")
async def list_shares(
    archive_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Liste alle Share-Links für einen Archiv-Eintrag (nur für eingeloggten User)."""
    archive = get_archive()
    shares = archive.list_shares_for_archive(archive_id)
    return {"shares": shares}


@router.get("/share/{token}")
async def get_share(token: str) -> Response:
    """Öffentlicher Endpunkt: Hole geteilten Archiv-Eintrag per Token."""
    archive = get_archive()
    share = archive.get_share_by_token(token)
    if share is None:
        raise HTTPException(status_code=404, detail="Share-Link nicht gefunden oder abgelaufen.")

    entry = archive.get(share["archive_id"])
    if entry is None:
        raise HTTPException(status_code=404, detail="Archiv-Eintrag nicht mehr vorhanden.")

    allow_embed = bool(share.get("allow_embed"))
    data = _build_public_entry(entry, share)

    headers = {
        "X-Frame-Options": "ALLOWALL" if allow_embed else "SAMEORIGIN",
        "Cache-Control": "no-store",
    }
    return JSONResponse(content=data, headers=headers)


@router.get("/share/{token}/embed")
async def get_share_embed(token: str) -> Response:
    """Minimale Embed-Ansicht (nur wenn allow_embed=true)."""
    archive = get_archive()
    share = archive.get_share_by_token(token)
    if share is None:
        raise HTTPException(status_code=404, detail="Share-Link nicht gefunden oder abgelaufen.")
    if not share.get("allow_embed"):
        raise HTTPException(status_code=403, detail="Einbetten ist für diesen Link nicht erlaubt.")

    entry = archive.get(share["archive_id"])
    if entry is None:
        raise HTTPException(status_code=404, detail="Archiv-Eintrag nicht mehr vorhanden.")

    # Minimale Felder für Embed
    data = {
        "token": share["token"],
        "title": entry.get("title"),
        "overall_rating": entry.get("overall_rating"),
        "confidence": entry.get("confidence"),
        "summary": (entry.get("summary") or "")[:200],
        "claims_count": entry.get("claims_count"),
        "source_url": entry.get("source_url"),
        "share_url": f"/share/{share['token']}",
    }

    headers = {
        "X-Frame-Options": "ALLOWALL",
        "Cache-Control": "no-store",
    }
    return JSONResponse(content=data, headers=headers)


@router.delete("/share/{token}", status_code=204)
async def delete_share(
    token: str,
    current_user: dict = Depends(get_current_user),
) -> None:
    """Lösche einen Share-Link (nur für den Ersteller)."""
    archive = get_archive()
    deleted = archive.delete_share(token=token, user_id=current_user["id"])
    if not deleted:
        # Entweder nicht gefunden oder kein Eigentümer
        archive2 = get_archive()
        share = archive2.get_share_by_token(token)
        if share is None:
            raise HTTPException(status_code=404, detail="Share-Link nicht gefunden.")
        raise HTTPException(status_code=403, detail="Nur der Ersteller kann diesen Link löschen.")
