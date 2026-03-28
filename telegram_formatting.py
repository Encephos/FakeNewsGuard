"""FakeNewsGuard – Telegram MarkdownV2 formatting helpers.

Extracted from telegram_bot.py. Contains all formatting-related functions
for rendering analysis results as Telegram MarkdownV2 messages.
"""

from __future__ import annotations

import re
from typing import Any


# ── Telegram MarkdownV2 Helpers ──────────────────────────────────

def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!\\"
    result = []
    for ch in text:
        if ch in special:
            result.append("\\")
        result.append(ch)
    return "".join(result)


def bold(text: str) -> str:
    return f"*{escape_md(text)}*"


def italic(text: str) -> str:
    return f"_{escape_md(text)}_"


def code(text: str) -> str:
    return f"`{text}`"


def link(text: str, url: str) -> str:
    return f"[{escape_md(text)}]({url})"


def rating_emoji(rating: str) -> str:
    """Map overall rating to emoji."""
    emojis = {
        "Wahr": "\u2705",                    # ✅
        "Größtenteils wahr": "\U0001F7E2",   # 🟢
        "Irreführend": "\U0001F7E1",         # 🟡
        "Größtenteils falsch": "\U0001F7E0", # 🟠
        "Falsch": "\U0001F534",              # 🔴
    }
    return emojis.get(rating, "\u2753")       # ❓


def fact_rating_emoji(rating: str) -> str:
    """Map fact rating to emoji."""
    emojis = {
        "TRUE": "\u2705",
        "MOSTLY_TRUE": "\U0001F7E2",
        "MISLEADING": "\U0001F7E1",
        "MOSTLY_FALSE": "\U0001F7E0",
        "FALSE": "\U0001F534",
        "UNVERIFIABLE": "\u2753",
    }
    return emojis.get(rating, "\u2022")


def fact_rating_label(rating: str) -> str:
    labels = {
        "TRUE": "Wahr",
        "MOSTLY_TRUE": "Größtenteils wahr",
        "MISLEADING": "Irreführend",
        "MOSTLY_FALSE": "Größtenteils falsch",
        "FALSE": "Falsch",
        "UNVERIFIABLE": "Nicht verifizierbar",
    }
    return labels.get(rating, rating)


def severity_emoji(severity: str) -> str:
    return {"LOW": "\U0001F7E2", "MEDIUM": "\U0001F7E1", "HIGH": "\U0001F534"}.get(severity, "\u2022")


def severity_label(severity: str) -> str:
    return {"LOW": "Niedrig", "MEDIUM": "Mittel", "HIGH": "Hoch"}.get(severity, severity)


def confidence_bar(confidence: int) -> str:
    """Create a visual confidence meter: ████████░░ 80%"""
    filled = round(confidence / 10)
    empty = 10 - filled
    bar = "\u2588" * filled + "\u2591" * empty
    return f"`{bar}` {escape_md(str(confidence))}%"


def divider() -> str:
    return escape_md("─" * 28)


# ── Format Analysis Result ───────────────────────────────────────

def format_result(result: dict[str, Any]) -> str:
    """Format the analysis result as Telegram MarkdownV2 message."""
    parts: list[str] = []

    rating = result.get("overall_rating", "?")
    confidence = result.get("confidence", 0)
    emoji = rating_emoji(rating)

    # ── Hero Header ──
    parts.append(f"{emoji}  {bold(rating.upper())}")
    parts.append(confidence_bar(confidence))
    parts.append("")

    # ── Summary ──
    summary = result.get("summary", "")
    if summary:
        parts.append(f"\U0001F4DD {bold('Zusammenfassung')}")
        parts.append(escape_md(summary))
        parts.append("")

    # ── Claims ──
    claims = result.get("claims", [])
    if claims:
        parts.append(divider())
        parts.append(f"\U0001F50D {bold('Behauptungen')}  _{escape_md(f'({len(claims)})')}_")
        parts.append("")

        for i, claim in enumerate(claims, 1):
            r = claim.get("rating", "")
            re_ = fact_rating_emoji(r)
            label = fact_rating_label(r)

            # Claim header line
            parts.append(f"{re_} *{escape_md(f'#{i}')}*  {escape_md(claim.get('text', ''))}")
            parts.append(f"    \u2192 {italic(label)}")

            evidence = claim.get("evidence", "")
            if evidence:
                parts.append(f"    {escape_md(evidence)}")

            correction = claim.get("correction", "")
            if correction:
                parts.append(f"    \u26A0\uFE0F {italic('Korrektur:')} {escape_md(correction)}")

            missing = claim.get("missing_context", "")
            if missing:
                parts.append(f"    \U0001F4AC {italic('Kontext:')} {escape_md(missing)}")

            # Number audit
            na = claim.get("number_audit")
            if na and na.get("manipulation", "NONE") != "NONE":
                parts.append(f"    \U0001F4CA {italic('Zahlenmanipulation:')} {escape_md(na.get('manipulation', ''))}")
                if na.get("correct_value"):
                    parts.append(f"    \u2192 {escape_md(na['correct_value'])}")

            # Add spacing between claims
            if i < len(claims):
                parts.append("")

    # ── Rhetoric / Manipulation Techniques ──
    rhetoric = result.get("rhetoric", [])
    if rhetoric:
        parts.append("")
        parts.append(divider())
        parts.append(f"\U0001F3AD {bold('Manipulationstechniken')}  _{escape_md(f'({len(rhetoric)})')}_")
        parts.append("")
        for tech in rhetoric:
            sev = tech.get("severity", "")
            sev_e = severity_emoji(sev)
            sev_l = severity_label(sev)
            parts.append(f"{sev_e} {bold(tech.get('name', ''))}  \u00B7  {italic(sev_l)}")
            if tech.get("description"):
                parts.append(f"    {escape_md(tech['description'])}")
            if tech.get("example"):
                parts.append(f"    \u00AB{escape_md(tech['example'])}\u00BB")
            parts.append("")

    # ── Corrections ──
    corrections = result.get("corrections", [])
    if corrections:
        parts.append(divider())
        parts.append(f"\u270F\uFE0F {bold('Korrekturen')}")
        parts.append("")
        for i, corr in enumerate(corrections, 1):
            parts.append(f"  {escape_md(str(i))}\\. {escape_md(corr)}")
        parts.append("")

    # ── Fairness ──
    fairness = result.get("fairness", [])
    if fairness:
        parts.append(divider())
        parts.append(f"\u2705 {bold('Was korrekt war')}")
        parts.append("")
        for note in fairness:
            parts.append(f"    \u2022 {escape_md(note)}")
        parts.append("")

    # ── Sources ──
    sources = result.get("sources", [])
    if sources:
        parts.append(divider())
        parts.append(f"\U0001F517 {bold('Quellen')}")
        parts.append("")
        for src in sources[:8]:
            if src.startswith("http"):
                domain = re.sub(r"^https?://(?:www\.)?", "", src).split("/")[0]
                parts.append(f"    \u2023 {link(domain, src)}")
            else:
                parts.append(f"    \u2023 {escape_md(src)}")

    # Footer
    parts.append("")
    parts.append(escape_md("_____"))
    parts.append(f"_{escape_md('FakeNewsGuard \u00B7 KI-Faktencheck')}_")

    return "\n".join(parts)


_MAIN_PHASES = ["Phase 1", "Phase 2", "Phase 3", "Phase 4"]


def format_steps_progress(steps: list[dict[str, Any]]) -> str:
    """Format current progress steps as a compact status message."""
    if not steps:
        return f"\U0001F9E0 {escape_md('Analyse wird vorbereitet...')}"

    # Phase-based counting: count how many main phases are fully done
    phases_done = sum(
        1 for phase_id in _MAIN_PHASES
        if any(s.get("phase") == phase_id for s in steps)
        and all(
            s.get("status") != "running"
            for s in steps
            if s.get("phase") == phase_id
        )
    )
    total_phases = len(_MAIN_PHASES)

    last = steps[-1]
    agent = last.get("agent", "")
    msg = last.get("message", "")
    status = last.get("status", "")

    # Progress dots (one dot per main phase)
    dots = "\u25C9" * phases_done + "\u25CB" * (total_phases - phases_done)

    icon = "\U0001F9E0" if status == "running" else "\u2705"
    header = f"{icon} {bold('Analyse')}  {escape_md(dots)}  {escape_md(f'{phases_done}/{total_phases}')}"
    detail = escape_md(msg[:120]) if msg else ""

    lines = [header]
    if agent:
        lines.append(f"    \u2192 {italic(agent)}")
    if detail:
        lines.append(f"    {detail}")
    return "\n".join(lines)
