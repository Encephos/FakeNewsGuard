"""Inline keyboard builders for the Telegram bot."""

from __future__ import annotations

from typing import Any

from telegram_formatting import fact_rating_emoji

TIER_LEVELS = {"lite": 0, "pro": 1, "max": 2}


def build_tier_keyboard(user_tier: str, msg_hash: str) -> dict[str, Any]:
    """Build an inline keyboard with available tier buttons."""
    user_level = TIER_LEVELS.get(user_tier, 0)
    row1 = []
    for tier in ("lite", "pro", "max"):
        if TIER_LEVELS[tier] <= user_level:
            row1.append({"text": tier.upper(), "callback_data": f"t:{tier}:{msg_hash}"})
    rows = [row1]
    # Commander tiers
    cmd_row = []
    if user_level >= TIER_LEVELS["pro"]:
        cmd_row.append({"text": "\U0001F9ED CMD PRO", "callback_data": f"t:commander-pro:{msg_hash}"})
    if user_level >= TIER_LEVELS["max"]:
        cmd_row.append({"text": "\U0001F9ED CMD MAX", "callback_data": f"t:commander-max:{msg_hash}"})
    if cmd_row:
        rows.append(cmd_row)
    return {"inline_keyboard": rows}


def build_result_keyboard(result: dict[str, Any], job_short: str) -> dict[str, Any]:
    """Build an inline keyboard for navigating analysis results."""
    rows: list[list[dict[str, str]]] = []

    # One button per claim
    claims = result.get("claims", [])
    for i, claim in enumerate(claims):
        text = claim.get("text", "")
        if len(text) > 25:
            text = text[:22] + "..."
        emoji = fact_rating_emoji(claim.get("rating", ""))
        rows.append([{"text": f"{emoji} #{i + 1} {text}", "callback_data": f"c:{job_short}:{i}"}])

    # Section buttons (only for non-empty sections)
    section_row: list[dict[str, str]] = []
    if result.get("rhetoric"):
        section_row.append({"text": "\U0001F3AD Rhetorik", "callback_data": f"s:{job_short}:rhet"})
    if result.get("sources"):
        section_row.append({"text": "\U0001F517 Quellen", "callback_data": f"s:{job_short}:src"})
    if section_row:
        rows.append(section_row)

    section_row2: list[dict[str, str]] = []
    if result.get("corrections"):
        section_row2.append({"text": "\u270F\uFE0F Korrektionen", "callback_data": f"s:{job_short}:corr"})
    if result.get("fairness"):
        section_row2.append({"text": "\u2705 Korrekt", "callback_data": f"s:{job_short}:fair"})
    if section_row2:
        rows.append(section_row2)

    # New analysis button
    rows.append([{"text": "\U0001F504 Neue Analyse", "callback_data": "new"}])

    return {"inline_keyboard": rows}


# Underscore aliases so tests importing from bot.keyboards still work
_build_tier_keyboard = build_tier_keyboard
_build_result_keyboard = build_result_keyboard
