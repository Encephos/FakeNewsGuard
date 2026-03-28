"""Admin endpoints: user management, usage stats, metrics, logs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from tools.logger import get_metrics_snapshot, get_recent_logs

from .dependencies import (
    UpdateTierRequest,
    get_archive,
    get_user_db,
    jobs,
    require_admin,
)

router = APIRouter()


@router.get("/api/admin/users")
async def admin_list_users(request: Request) -> dict:
    """List all users with usage stats. Admin only."""
    require_admin(request)
    user_db = get_user_db()
    users = user_db.list_users()
    return {"users": users}


@router.patch("/api/admin/users/{user_id}/tier")
async def admin_update_tier(user_id: str, req: UpdateTierRequest, request: Request) -> dict:
    """Change a user's tier. Admin only."""
    require_admin(request)
    if req.tier not in ("lite", "pro", "max"):
        raise HTTPException(status_code=400, detail="Ungueltiger Tier. Erlaubt: lite, pro, max")
    user_db = get_user_db()
    if not user_db.update_tier(user_id, req.tier):
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    return {"ok": True, "tier": req.tier}


@router.get("/api/admin/users/{user_id}/usage")
async def admin_user_usage(user_id: str, request: Request) -> dict:
    """Get usage log for a specific user. Admin only."""
    require_admin(request)
    user_db = get_user_db()
    user = user_db.get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    usage = user_db.get_user_usage(user_id, days=30)
    return {"user_id": user_id, "usage": usage}


@router.get("/api/admin/stats")
async def admin_stats(request: Request) -> dict:
    """Get overall platform stats. Admin only."""
    require_admin(request)
    user_db = get_user_db()
    users = user_db.list_users()
    # Analyse-Zahlen aus dem Archiv (erfasst auch unauthentifizierte Analysen)
    archive = get_archive()
    archive_counts = archive.count_analyses()
    total_analyses = archive_counts["total"]
    month_analyses = archive_counts["last_30_days"]
    tier_counts = {}
    for u in users:
        t = u.get("tier", "lite")
        tier_counts[t] = tier_counts.get(t, 0) + 1
    return {
        "total_users": len(users),
        "total_analyses": total_analyses,
        "month_analyses": month_analyses,
        "tier_distribution": tier_counts,
    }


@router.get("/api/admin/metrics")
async def admin_metrics(request: Request) -> dict:
    """Echtzeit-Systemmetriken (Requests, Latenzen, Auth-Stats). Admin only."""
    require_admin(request)
    snapshot = get_metrics_snapshot()
    snapshot["active_jobs"] = sum(
        1 for j in jobs.values() if j["status"] in ("pending", "running")
    )
    return snapshot


@router.get("/api/admin/logs")
async def admin_logs(
    request: Request, limit: int = 100, level: str | None = None
) -> dict:
    """Letzte Log-Eintraege aus dem In-Memory-Puffer. Admin only."""
    require_admin(request)
    return {"logs": get_recent_logs(limit=limit, level=level)}
