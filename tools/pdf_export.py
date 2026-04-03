"""PDF-Export für FakeNewsGuard Analyse-Ergebnisse.

Generiert einen druckfertigen PDF-Report aus einem Analyse-Ergebnis
(dem Frontend-Dict-Format aus api._transform_result).
"""

from __future__ import annotations

import io
import time
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


# ── Farben ────────────────────────────────────────────────────────

_RATING_COLORS: dict[str, colors.Color] = {
    # Localized labels (legacy)
    "Wahr": colors.HexColor("#22c55e"),
    "Größtenteils wahr": colors.HexColor("#84cc16"),
    "Irreführend": colors.HexColor("#f59e0b"),
    "Größtenteils falsch": colors.HexColor("#ef4444"),
    "Falsch": colors.HexColor("#dc2626"),
    # Enum keys
    "RELIABLE": colors.HexColor("#22c55e"),
    "MOSTLY_RELIABLE": colors.HexColor("#84cc16"),
    "MIXED": colors.HexColor("#f59e0b"),
    "MISLEADING": colors.HexColor("#f59e0b"),
    "HIGHLY_MISLEADING": colors.HexColor("#ef4444"),
    "FABRICATED": colors.HexColor("#dc2626"),
}

_CLAIM_RATING_COLORS: dict[str, colors.Color] = {
    "TRUE": colors.HexColor("#22c55e"),
    "MOSTLY_TRUE": colors.HexColor("#84cc16"),
    "MISLEADING": colors.HexColor("#f59e0b"),
    "MOSTLY_FALSE": colors.HexColor("#ef4444"),
    "FALSE": colors.HexColor("#dc2626"),
    "UNVERIFIABLE": colors.HexColor("#6b7280"),
}

_CLAIM_RATING_LABELS: dict[str, str] = {
    "TRUE": "Wahr",
    "MOSTLY_TRUE": "Größtenteils wahr",
    "MISLEADING": "Irreführend",
    "MOSTLY_FALSE": "Größtenteils falsch",
    "FALSE": "Falsch",
    "UNVERIFIABLE": "Nicht verifizierbar",
}

_SEVERITY_COLORS: dict[str, colors.Color] = {
    "LOW": colors.HexColor("#fbbf24"),
    "MEDIUM": colors.HexColor("#f59e0b"),
    "HIGH": colors.HexColor("#ef4444"),
}


# ── Styles ────────────────────────────────────────────────────────

def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PDFTitle",
            parent=base["Title"],
            fontSize=22,
            leading=26,
            spaceAfter=6 * mm,
            textColor=colors.HexColor("#1e293b"),
        ),
        "subtitle": ParagraphStyle(
            "PDFSubtitle",
            parent=base["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#64748b"),
            spaceAfter=8 * mm,
            alignment=TA_CENTER,
        ),
        "h2": ParagraphStyle(
            "PDFH2",
            parent=base["Heading2"],
            fontSize=14,
            leading=18,
            spaceBefore=6 * mm,
            spaceAfter=3 * mm,
            textColor=colors.HexColor("#1e293b"),
        ),
        "h3": ParagraphStyle(
            "PDFH3",
            parent=base["Heading3"],
            fontSize=11,
            leading=14,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
            textColor=colors.HexColor("#334155"),
        ),
        "body": ParagraphStyle(
            "PDFBody",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            spaceAfter=3 * mm,
            alignment=TA_JUSTIFY,
        ),
        "small": ParagraphStyle(
            "PDFSmall",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#64748b"),
        ),
        "rating_big": ParagraphStyle(
            "PDFRatingBig",
            parent=base["Normal"],
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            spaceAfter=2 * mm,
        ),
        "bullet": ParagraphStyle(
            "PDFBullet",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            leftIndent=8 * mm,
            bulletIndent=2 * mm,
            spaceAfter=2 * mm,
        ),
        "source": ParagraphStyle(
            "PDFSource",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#2563eb"),
            leftIndent=4 * mm,
        ),
    }


# ── Header / Footer ──────────────────────────────────────────────

def _header_footer(canvas, doc):
    """Zeichne Header-Linie und Footer mit Seitenzahl."""
    canvas.saveState()

    # Header
    canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, A4[1] - 1.8 * cm, A4[0] - 2 * cm, A4[1] - 1.8 * cm)

    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    canvas.drawString(2 * cm, A4[1] - 1.6 * cm, "FakeNewsGuard – Faktencheck-Report")

    # Footer
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, f"Seite {doc.page}")
    canvas.line(2 * cm, 1.6 * cm, A4[0] - 2 * cm, 1.6 * cm)

    canvas.restoreState()


# ── PDF Generation ────────────────────────────────────────────────

def generate_pdf(result: dict[str, Any], title: str = "", source_url: str = "") -> bytes:
    """Generiere einen PDF-Report aus einem Analyse-Ergebnis.

    Args:
        result: Das Analyse-Ergebnis im Frontend-Dict-Format
                (wie von api._transform_result zurückgegeben).
        title: Optionaler Titel für den Report.
        source_url: Optionale Quell-URL.

    Returns:
        PDF als bytes.
    """
    buf = io.BytesIO()
    styles = _build_styles()

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2.2 * cm,
        bottomMargin=2 * cm,
    )

    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        id="main",
    )
    doc.addPageTemplates([
        PageTemplate(id="main", frames=[frame], onPage=_header_footer),
    ])

    story: list[Any] = []

    # ── Titel ─────────────────────────────────────────────────────
    report_title = title or "Faktencheck-Report"
    story.append(Paragraph(report_title, styles["title"]))

    meta_parts = []
    if source_url:
        meta_parts.append(f"Quelle: {source_url}")
    meta_parts.append(f"Erstellt: {time.strftime('%d.%m.%Y %H:%M')}")
    story.append(Paragraph(" | ".join(meta_parts), styles["subtitle"]))

    # ── Gesamtbewertung ──────────────────────────────────────────
    overall = result.get("overall_rating", "?")
    confidence = result.get("confidence", 0)
    rating_color = _RATING_COLORS.get(overall, colors.HexColor("#6b7280"))

    rating_table = Table(
        [[
            Paragraph(
                f'<font color="{rating_color.hexval()}">{overall}</font>',
                styles["rating_big"],
            ),
            Paragraph(f"Confidence: {confidence}%", styles["body"]),
        ]],
        colWidths=[10 * cm, 6 * cm],
    )
    rating_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(rating_table)
    story.append(Spacer(1, 4 * mm))

    # ── Zusammenfassung ──────────────────────────────────────────
    summary = result.get("summary", "")
    if summary:
        story.append(Paragraph("Zusammenfassung", styles["h2"]))
        story.append(Paragraph(summary, styles["body"]))

    # ── Claims ───────────────────────────────────────────────────
    claims = result.get("claims", [])
    if claims:
        story.append(Paragraph("Claims im Detail", styles["h2"]))
        story.append(HRFlowable(
            width="100%", thickness=0.5,
            color=colors.HexColor("#e2e8f0"), spaceAfter=3 * mm,
        ))

        for claim in claims:
            rating_val = claim.get("rating", "UNVERIFIABLE")
            rating_label = _CLAIM_RATING_LABELS.get(rating_val, rating_val)
            rc = _CLAIM_RATING_COLORS.get(rating_val, colors.HexColor("#6b7280"))

            story.append(Paragraph(
                f'<b>[{claim.get("id", "?")}]</b> '
                f'<font color="{rc.hexval()}">{rating_label}</font>',
                styles["h3"],
            ))
            story.append(Paragraph(claim.get("text", ""), styles["body"]))

            if claim.get("evidence"):
                story.append(Paragraph(
                    f'<b>Evidenz:</b> {claim["evidence"]}',
                    styles["bullet"],
                ))
            if claim.get("correction"):
                story.append(Paragraph(
                    f'<b>Korrektur:</b> {claim["correction"]}',
                    styles["bullet"],
                ))
            if claim.get("missing_context"):
                story.append(Paragraph(
                    f'<b>Fehlender Kontext:</b> {claim["missing_context"]}',
                    styles["bullet"],
                ))

            # Number Audit
            na = claim.get("number_audit")
            if na:
                story.append(Paragraph(
                    f'<b>Zahlenprüfung:</b> {na.get("manipulation", "")} – {na.get("correct_value", "")}',
                    styles["bullet"],
                ))

            # Quellen
            for src in claim.get("sources", [])[:5]:
                story.append(Paragraph(src, styles["source"]))

            story.append(Spacer(1, 3 * mm))

    # ── Manipulationstechniken ───────────────────────────────────
    rhetoric = result.get("rhetoric", [])
    if rhetoric:
        story.append(Paragraph("Manipulationstechniken", styles["h2"]))
        for tech in rhetoric:
            sev = tech.get("severity", "LOW")
            sc = _SEVERITY_COLORS.get(sev, colors.HexColor("#f59e0b"))
            story.append(Paragraph(
                f'<b>{tech.get("name", "?")}</b> '
                f'<font color="{sc.hexval()}">[{sev}]</font>',
                styles["h3"],
            ))
            story.append(Paragraph(tech.get("description", ""), styles["body"]))
            if tech.get("example"):
                story.append(Paragraph(
                    f'<i>Beispiel: "{tech["example"]}"</i>',
                    styles["bullet"],
                ))

    # ── Korrekturen ──────────────────────────────────────────────
    corrections = result.get("corrections", [])
    if corrections:
        story.append(Paragraph("Kernkorrekturen", styles["h2"]))
        for i, corr in enumerate(corrections, 1):
            story.append(Paragraph(f"{i}. {corr}", styles["bullet"]))

    # ── Fairness ─────────────────────────────────────────────────
    fairness = result.get("fairness", [])
    if fairness:
        story.append(Paragraph("Was korrekt dargestellt wurde", styles["h2"]))
        for note in fairness:
            story.append(Paragraph(f"+ {note}", styles["bullet"]))

    # ── Quellen ──────────────────────────────────────────────────
    sources = result.get("sources", [])
    if sources:
        story.append(Paragraph("Quellen", styles["h2"]))
        for src in sources:
            story.append(Paragraph(src, styles["source"]))

    # ── Footer-Hinweis ───────────────────────────────────────────
    story.append(Spacer(1, 10 * mm))
    story.append(HRFlowable(
        width="100%", thickness=0.5,
        color=colors.HexColor("#e2e8f0"), spaceBefore=4 * mm,
    ))
    story.append(Paragraph(
        "Dieser Report wurde automatisch von FakeNewsGuard generiert. "
        "Die Analyse basiert auf KI-gestützter Auswertung öffentlich "
        "verfügbarer Quellen und ersetzt keine professionelle journalistische Recherche.",
        styles["small"],
    ))

    doc.build(story)
    return buf.getvalue()
