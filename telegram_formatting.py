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


# ── Compact Overview (for inline-keyboard flow) ─────────────────

def format_result_overview(result: dict[str, Any]) -> str:
    """Compact result overview – shown with inline keyboard buttons."""
    parts: list[str] = []

    rating = result.get("overall_rating", "?")
    confidence = result.get("confidence", 0)
    emoji = rating_emoji(rating)

    # Hero Header
    parts.append(f"{emoji}  {bold(rating.upper())}")
    parts.append(confidence_bar(confidence))
    parts.append("")

    # Summary
    summary = result.get("summary", "")
    if summary:
        parts.append(f"\U0001F4DD {bold('Zusammenfassung')}")
        parts.append(escape_md(summary))
        parts.append("")

    # Claims – compact, one line each
    claims = result.get("claims", [])
    if claims:
        parts.append(divider())
        parts.append(f"\U0001F50D {bold('Behauptungen')}  _{escape_md(f'({len(claims)})')}_")
        parts.append("")
        for i, claim in enumerate(claims, 1):
            r = claim.get("rating", "")
            re_ = fact_rating_emoji(r)
            label = fact_rating_label(r)
            text = claim.get("text", "")
            if len(text) > 80:
                text = text[:77] + "..."
            parts.append(f"{re_} *{escape_md(f'#{i}')}*  {escape_md(text)}")
            parts.append(f"    \u2192 {italic(label)}")
        parts.append("")
        parts.append(f"_{escape_md('Details per Button abrufbar \u2193')}_")

    # Footer
    parts.append("")
    parts.append(escape_md("_____"))
    parts.append(f"_{escape_md('FakeNewsGuard \u00B7 KI-Faktencheck')}_")

    return "\n".join(parts)


def format_claim_detail(claim: dict[str, Any], index: int) -> str:
    """Full detail view for a single claim."""
    parts: list[str] = []

    r = claim.get("rating", "")
    re_ = fact_rating_emoji(r)
    label = fact_rating_label(r)

    parts.append(f"{re_} {bold(f'Behauptung #{index + 1}')}")
    parts.append(f"{escape_md(claim.get('text', ''))}")
    parts.append(f"\u2192 {italic(label)}")
    parts.append("")

    evidence = claim.get("evidence", "")
    if evidence:
        parts.append(f"\U0001F4CB {bold('Evidenz')}")
        parts.append(f"{escape_md(evidence)}")
        parts.append("")

    correction = claim.get("correction", "")
    if correction:
        parts.append(f"\u26A0\uFE0F {bold('Korrektur')}")
        parts.append(f"{escape_md(correction)}")
        parts.append("")

    missing = claim.get("missing_context", "")
    if missing:
        parts.append(f"\U0001F4AC {bold('Fehlender Kontext')}")
        parts.append(f"{escape_md(missing)}")
        parts.append("")

    na = claim.get("number_audit")
    if na and na.get("manipulation", "NONE") != "NONE":
        parts.append(f"\U0001F4CA {bold('Zahlenmanipulation')}")
        parts.append(f"{escape_md(na.get('manipulation', ''))}")
        if na.get("calculation"):
            parts.append(f"{escape_md(na['calculation'])}")
        if na.get("correct_value"):
            parts.append(f"\u2192 {escape_md(na['correct_value'])}")
        parts.append("")

    sources = claim.get("sources", [])
    if sources:
        parts.append(f"\U0001F517 {bold('Quellen')}")
        for src in sources[:5]:
            if src.startswith("http"):
                domain = re.sub(r"^https?://(?:www\.)?", "", src).split("/")[0]
                parts.append(f"  \u2023 {link(domain, src)}")
            else:
                parts.append(f"  \u2023 {escape_md(src)}")

    return "\n".join(parts)


def format_rhetoric_section(rhetoric: list[dict[str, Any]]) -> str:
    """Full rhetoric/manipulation techniques section."""
    parts: list[str] = []
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
    return "\n".join(parts)


def format_sources_section(sources: list[str]) -> str:
    """Full sources section."""
    parts: list[str] = []
    parts.append(f"\U0001F517 {bold('Quellen')}")
    parts.append("")
    for src in sources[:12]:
        if src.startswith("http"):
            domain = re.sub(r"^https?://(?:www\.)?", "", src).split("/")[0]
            parts.append(f"  \u2023 {link(domain, src)}")
        else:
            parts.append(f"  \u2023 {escape_md(src)}")
    return "\n".join(parts)


def format_corrections_section(corrections: list[str]) -> str:
    """Full corrections section."""
    parts: list[str] = []
    parts.append(f"\u270F\uFE0F {bold('Korrekturen')}")
    parts.append("")
    for i, corr in enumerate(corrections, 1):
        parts.append(f"  {escape_md(str(i))}\\. {escape_md(corr)}")
    return "\n".join(parts)


def format_fairness_section(fairness: list[str]) -> str:
    """Full fairness section."""
    parts: list[str] = []
    parts.append(f"\u2705 {bold('Was korrekt war')}")
    parts.append("")
    for note in fairness:
        parts.append(f"  \u2022 {escape_md(note)}")
    return "\n".join(parts)


def format_tier_selection() -> str:
    """Short prompt for tier selection keyboard."""
    return (
        f"\U0001F9E0 {bold('Analyse-Stufe wählen')}\n\n"
        f"{escape_md('Wähle die gewünschte Analyse-Tiefe:')}"
    )


# ── Progress Display ─────────────────────────────────────────────

_PHASES = [
    {"id": "Phase 0",   "label": "Inhalte extrahieren",   "optional": True},
    {"id": "Phase 0.5", "label": "Bildanalyse",           "optional": True},
    {"id": "Phase 1",   "label": "Behauptungen erkennen", "optional": False},
    {"id": "Phase 2",   "label": "Faktencheck",           "optional": False},
    {"id": "Phase 3",   "label": "Rhetorik-Analyse",      "optional": False},
    {"id": "Phase 4",   "label": "Synthese",              "optional": False},
]

_REQUIRED_PHASE_COUNT = sum(1 for p in _PHASES if not p["optional"])

_STATUS_EMOJI = {
    "pending": "\u23F3",       # ⏳
    "running": "\U0001F504",   # 🔄
    "done":    "\u2705",       # ✅
    "error":   "\u26A0\uFE0F", # ⚠️
}


def _get_phase_status(phase_id: str, steps: list[dict[str, Any]]) -> str:
    """Return 'pending', 'running', 'done', or 'error' for a phase."""
    phase_steps = [s for s in steps if s.get("phase") == phase_id]
    if not phase_steps:
        return "pending"
    if any(s.get("status") == "running" for s in phase_steps):
        return "running"
    if any(s.get("status") == "error" for s in phase_steps):
        return "error"
    return "done"


def _get_phase_detail(phase_id: str, steps: list[dict[str, Any]]) -> str:
    """Get the latest message for a running or errored phase."""
    phase_steps = [s for s in steps if s.get("phase") == phase_id]
    if not phase_steps:
        return ""
    last = phase_steps[-1]
    msg = last.get("message", "")
    return msg[:80] if msg else ""


def format_steps_progress(steps: list[dict[str, Any]], tier: str = "") -> str:
    """Format current progress as a stable phase checklist."""
    if not steps:
        return f"\U0001F9E0 {escape_md('Analyse wird vorbereitet...')}"

    # Count completed required phases
    done_count = 0
    for phase in _PHASES:
        if not phase["optional"] and _get_phase_status(phase["id"], steps) == "done":
            done_count += 1

    # Header
    tier_display = tier.upper().replace("-", " ") if tier else ""
    tier_label = f" \\({escape_md(tier_display)}\\)" if tier_display else ""
    header = f"\U0001F9E0 {bold('Analyse')}{tier_label}  {escape_md(f'{done_count}/{_REQUIRED_PHASE_COUNT}')}"

    lines = [header, ""]

    for phase in _PHASES:
        status = _get_phase_status(phase["id"], steps)

        # Hide optional phases that haven't started
        if phase["optional"] and status == "pending":
            continue

        emoji = _STATUS_EMOJI.get(status, "\u23F3")
        lines.append(f"{emoji}  {escape_md(phase['label'])}")

        # Show detail for running or error phases
        if status in ("running", "error"):
            detail = _get_phase_detail(phase["id"], steps)
            if detail:
                lines.append(f"      \u21B3 {escape_md(detail)}")

    return "\n".join(lines)


# ── /start Message ───────────────────────────────────────────────

def format_start_message() -> str:
    """Build the /start welcome message."""
    return "\n".join([
        f"\U0001F9E0 {bold('FakeNewsGuard')}",
        f"_{escape_md('KI-gestützter Faktencheck')}_",
        "",
        f"{escape_md('Willkommen! Sende mir einen Text, eine Behauptung')}",
        f"{escape_md('oder einen Link')} \u2013 {escape_md('ich prüfe den Inhalt auf:')}",
        "",
        f"  \U0001F50D {escape_md('Faktentreue der Behauptungen')}",
        f"  \U0001F4CA {escape_md('Zahlen- & Statistikmanipulation')}",
        f"  \U0001F3AD {escape_md('Rhetorische Manipulationstechniken')}",
        f"  \U0001F5BC {escape_md('Bildmanipulation & Kontext')}",
        "",
        divider(),
        "",
        f"{bold('Unterstützte Plattformen')}",
        f"  \U0001D54F {escape_md('Twitter/X')}  \u2022  "
        f"\U0001F9F5 {escape_md('Threads')}  \u2022  "
        f"\U0001F4F7 {escape_md('Instagram')}",
        f"  \U0001F4D8 {escape_md('Facebook')}  \u2022  "
        f"\u25B6\uFE0F {escape_md('YouTube')}  \u2022  "
        f"\U0001F4F0 {escape_md('Nachrichtenartikel')}",
        f"  \U0001F310 {escape_md('Beliebige Webseiten mit Text')}",
        "",
        divider(),
        "",
        f"{bold('Analyse-Stufen')}",
        "",
        f"  {code('/lite')}  \u2013  {escape_md('Schnellcheck')}",
        f"  {escape_md('Kostenlose Modelle. Ideal für einfache Behauptungen.')}",
        f"  {escape_md('Ergebnis in ca. 30 Sekunden.')}",
        "",
        f"  {code('/pro')}   \u2013  {escape_md('Standardanalyse')}",
        f"  {escape_md('Stärkere Modelle, ausgewogene Tiefe.')}",
        f"  {escape_md('Ergebnis in ca. 1\u20132 Minuten.')}",
        "",
        f"  {code('/max')}   \u2013  {escape_md('Tiefenanalyse')}",
        f"  {escape_md('Beste Modelle, mehrere Quellen, Bildanalyse.')}",
        f"  {escape_md('Ergebnis in ca. 2\u20133 Minuten.')}",
        "",
        f"  {code('/commander-pro')}  \u2013  {escape_md('Iterative Suche (Pro)')}",
        f"  {code('/commander-max')}  \u2013  {escape_md('Iterative Suche (Max)')}",
        f"  {escape_md('Commander verfeinert Suchanfragen iterativ für')}",
        f"  {escape_md('maximale Evidenzqualität.')}",
        "",
        f"  _{escape_md('Ohne Angabe wird dein Standard-Tier verwendet.')}_",
        "",
        divider(),
        "",
        f"{bold('Schnellstart')}",
        f"  {escape_md('Einfach Text oder Link senden')} \u2192 {escape_md('los gehts!')}",
        f"  {escape_md('Oder:')} {code('/max https://x.com/user/status/123')}",
        "",
        f"{bold('Befehle')}",
        f"  {code('/help')}       \u2013  {escape_md('Hilfe & Beispiele')}",
        f"  {code('/link')}       \u2013  {escape_md('Telegram mit Webkonto verknüpfen')}",
        f"  {code('/zustimmen')}  \u2013  {escape_md('Datenverarbeitung zustimmen')}",
        "",
        divider(),
        "",
        f"\u2139\uFE0F {bold('Hinweis zur Datenverarbeitung')}",
        f"{escape_md('Alle Anfragen werden protokolliert, um das Modell')}",
        f"{escape_md('und die Architektur zu verbessern.')}",
        f"{escape_md('Sende')} {code('/zustimmen')}{escape_md(', um zuzustimmen und loszulegen.')}",
    ])


# ── /help Message ────────────────────────────────────────────────

def format_help_message() -> str:
    """Build the /help message."""
    return "\n".join([
        f"\U0001F4D6 {bold('Hilfe \u2013 FakeNewsGuard')}",
        "",
        f"{bold('So funktioniert es')}",
        f"  *1\\.* {escape_md('Sende einen Text, eine Behauptung oder einen Link')}",
        f"  *2\\.* {escape_md('Die KI extrahiert alle prüfbaren Behauptungen')}",
        f"  *3\\.* {escape_md('Jede Behauptung wird mit aktuellen Quellen verifiziert')}",
        f"  *4\\.* {escape_md('Zahlen & Statistiken werden auf Manipulation geprüft')}",
        f"  *5\\.* {escape_md('Rhetorische Tricks werden identifiziert')}",
        f"  *6\\.* {escape_md('Du erhältst eine Gesamtbewertung mit Belegen & Quellen')}",
        "",
        divider(),
        "",
        f"{bold('Beispiele')}",
        "",
        f"\U0001F4AC {italic('Text senden:')}",
        f"  {escape_md('\"Deutschland hat die höchste Inflationsrate in Europa\"')}",
        "",
        f"\U0001F517 {italic('Link senden:')}",
        f"  {escape_md('https://x.com/user/status/123')}",
        f"  {escape_md('https://www.spiegel.de/artikel/...')}",
        "",
        f"\U0001F3AF {italic('Tier wählen:')}",
        f"  {code('/max')} {escape_md('https://x.com/user/status/123')}",
        f"  {code('/lite')} {escape_md('Deutschland hat 80 Millionen Einwohner')}",
        f"  {code('/commander-pro')} {escape_md('Ein komplexer Artikel...')}",
        "",
        divider(),
        "",
        f"{bold('Alle Befehle')}",
        f"  {code('/start')}               \u2013  {escape_md('Willkommensnachricht')}",
        f"  {code('/help')}                \u2013  {escape_md('Diese Hilfe anzeigen')}",
        f"  {code('/link <CODE>')}         \u2013  {escape_md('Telegram mit Webkonto verknüpfen')}",
        f"  {code('/zustimmen')}           \u2013  {escape_md('Datenverarbeitung zustimmen')}",
        f"  {code('/lite <Text>')}         \u2013  {escape_md('Schnellcheck')}",
        f"  {code('/pro <Text>')}          \u2013  {escape_md('Standardanalyse')}",
        f"  {code('/max <Text>')}          \u2013  {escape_md('Tiefenanalyse')}",
        f"  {code('/commander-pro <Text>')}  \u2013  {escape_md('Iterative Suche (Pro)')}",
        f"  {code('/commander-max <Text>')}  \u2013  {escape_md('Iterative Suche (Max)')}",
        "",
        divider(),
        "",
        f"{bold('Analyse-Stufen im Detail')}",
        "",
        f"  {bold('LITE')} \u2013 {escape_md('Schneller Basischeck mit kostenlosen Modellen.')}",
        f"  {escape_md('Gut für einfache, einzelne Behauptungen.')}",
        f"  {escape_md('Ergebnis in ca. 30 Sekunden.')}",
        "",
        f"  {bold('PRO')} \u2013 {escape_md('Ausgewogene Analyse mit stärkeren Modellen.')}",
        f"  {escape_md('Empfohlen für die meisten Fälle.')}",
        f"  {escape_md('Ergebnis in ca. 1\u20132 Minuten.')}",
        "",
        f"  {bold('MAX')} \u2013 {escape_md('Umfassende Tiefenanalyse mit den besten Modellen.')}",
        f"  {escape_md('Mehrere Quellen, Zahlenaudit, Bildanalyse.')}",
        f"  {escape_md('Ergebnis in ca. 2\u20133 Minuten.')}",
        "",
        f"  {bold('COMMANDER PRO / MAX')} \u2013 {escape_md('Wie Pro/Max, aber mit iterativer')}",
        f"  {escape_md('Suchverfeinerung. Commander generiert Suchanfragen,')}",
        f"  {escape_md('prüft ob der Kontext ausreicht und sucht bei Bedarf erneut.')}",
        f"  {escape_md('Bis zu 3 Suchrunden für maximale Evidenzqualität.')}",
        "",
        f"  _{escape_md('Ohne Tier-Angabe wird automatisch dein Standard-Tier verwendet.')}_",
        "",
        divider(),
        "",
        f"{bold('Bewertungsskala')}",
        f"  \u2705 {escape_md('Wahr')}  \u2022  "
        f"\U0001F7E2 {escape_md('Größtenteils wahr')}  \u2022  "
        f"\U0001F7E1 {escape_md('Irreführend')}",
        f"  \U0001F7E0 {escape_md('Größtenteils falsch')}  \u2022  "
        f"\U0001F534 {escape_md('Falsch')}",
        "",
        divider(),
        f"_{escape_md('Die Analyse dauert je nach Stufe ca. 30 Sek. \u2013 3 Min.')}_",
    ])
