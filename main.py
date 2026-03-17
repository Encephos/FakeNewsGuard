#!/usr/bin/env python3
"""FakeNewsGuard – CLI Entry Point.

Nutzung:
    python main.py "Zu prüfender Text hier..."
    python main.py --file input.txt
    python main.py --interactive

Umgebungsvariablen (in .env):
    ANTHROPIC_API_KEY    – Für Claude als LLM
    OPENAI_API_KEY       – Für GPT als LLM
    TAVILY_API_KEY       – Für Web-Suche (empfohlen)
    SERPER_API_KEY       – Alternative Web-Suche
    BRAVE_API_KEY        – Alternative Web-Suche
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import AppConfig, LLMConfig, SearchConfig
from orchestrator import Orchestrator
from models.schemas import SynthesisResult


# ── Formatierung ─────────────────────────────────────────────────

RATING_DISPLAY = {
    "RELIABLE": ("✅", "Zuverlässig"),
    "MOSTLY_RELIABLE": ("✅", "Überwiegend zuverlässig"),
    "MIXED": ("⚠️", "Gemischt"),
    "MISLEADING": ("⚠️", "Irreführend"),
    "HIGHLY_MISLEADING": ("❌", "Stark irreführend"),
    "FABRICATED": ("❌", "Frei erfunden"),
}

FACT_RATING_EMOJI = {
    "TRUE": "✅",
    "MOSTLY_TRUE": "✅",
    "MISLEADING": "⚠️",
    "MOSTLY_FALSE": "❌",
    "FALSE": "❌",
    "UNVERIFIABLE": "🔍",
}


def format_result(result: SynthesisResult) -> str:
    """Formatiere das Ergebnis als lesbaren Text."""
    emoji, label = RATING_DISPLAY.get(
        result.overall_rating.value, ("❓", result.overall_rating.value)
    )

    lines: list[str] = []
    lines.append("")
    lines.append("╔══════════════════════════════════════════════════════════╗")
    lines.append(f"║  {emoji}  GESAMTBEWERTUNG: {label:<38} ║")
    lines.append(f"║     Confidence: {result.confidence:.0%:<42} ║")
    lines.append("╚══════════════════════════════════════════════════════════╝")
    lines.append("")

    # Zusammenfassung
    lines.append("📝 ZUSAMMENFASSUNG")
    lines.append("─" * 58)
    lines.append(result.summary)
    lines.append("")

    # Einzelne Claims
    if result.claims_analysis:
        lines.append("🔍 CLAIMS IM DETAIL")
        lines.append("─" * 58)
        for fc in result.claims_analysis:
            e = FACT_RATING_EMOJI.get(fc.rating.value, "❓")
            lines.append(f"  {e} [{fc.claim_id}] {fc.rating.value}")
            if fc.evidence:
                lines.append(f"     Evidenz: {fc.evidence[:200]}")
            if fc.correction:
                lines.append(f"     Korrektur: {fc.correction[:200]}")
            if fc.missing_context:
                lines.append(f"     Fehlender Kontext: {fc.missing_context[:200]}")
            lines.append("")

    # Number Audits
    if result.number_audits:
        lines.append("🔢 ZAHLEN-PRÜFUNG")
        lines.append("─" * 58)
        for na in result.number_audits:
            lines.append(f"  [{na.claim_id}] Manipulation: {na.manipulation_type.value}")
            if na.calculation_check:
                lines.append(f"     Rechnung: {na.calculation_check[:200]}")
            if na.correct_interpretation:
                lines.append(f"     Korrekt: {na.correct_interpretation[:200]}")
            lines.append("")

    # Korrekturen
    if result.key_corrections:
        lines.append("🔧 KERNKORREKTUREN")
        lines.append("─" * 58)
        for i, corr in enumerate(result.key_corrections, 1):
            lines.append(f"  {i}. {corr}")
        lines.append("")

    # Manipulationstechniken
    if result.manipulation_techniques:
        lines.append("🎭 MANIPULATIONSTECHNIKEN")
        lines.append("─" * 58)
        for t in result.manipulation_techniques:
            lines.append(f"  • {t.technique} [{t.severity.value}]")
            lines.append(f"    {t.explanation}")
            if t.example:
                lines.append(f"    Beispiel: \"{t.example}\"")
        lines.append("")

    # Fairness-Check
    if result.fairness_notes:
        lines.append("⚖️  FAIRNESS-CHECK (Was stimmt)")
        lines.append("─" * 58)
        for note in result.fairness_notes:
            lines.append(f"  ✓ {note}")
        lines.append("")

    # Quellen
    if result.sources:
        lines.append("📚 QUELLEN")
        lines.append("─" * 58)
        for url in result.sources:
            lines.append(f"  • {url}")
        lines.append("")

    # Analyse-Fehler (partielle Ergebnisse)
    if result.analysis_errors:
        lines.append("⚠️  ANALYSE-HINWEISE (partielle Ergebnisse)")
        lines.append("─" * 58)
        for err in result.analysis_errors:
            lines.append(f"  • {err}")
        lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────


def build_config(args: argparse.Namespace) -> AppConfig:
    """Baue AppConfig aus CLI-Argumenten."""
    llm = LLMConfig(
        provider=args.llm_provider,
        model=args.model,
    )
    if args.llm_base_url:
        llm.base_url = args.llm_base_url

    search = SearchConfig(provider=args.search_provider)

    return AppConfig(
        llm=llm,
        search=search,
        verbose=not args.quiet,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FakeNewsGuard – Multi-Agent Faktencheck-System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Input
    parser.add_argument("text", nargs="?", help="Zu prüfender Text")
    parser.add_argument("--file", "-f", type=Path, help="Text aus Datei lesen")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interaktiver Modus")

    # LLM
    parser.add_argument(
        "--llm-provider", default="anthropic",
        choices=["anthropic", "openai", "ollama"],
        help="LLM Provider (default: anthropic)",
    )
    parser.add_argument("--model", default=None, help="Modellname überschreiben")
    parser.add_argument("--llm-base-url", default=None, help="Base URL für Ollama/lokale Modelle")

    # Search
    parser.add_argument(
        "--search-provider", default="tavily",
        choices=["tavily", "serper", "brave"],
        help="Web-Search Provider (default: tavily)",
    )

    # Output
    parser.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    parser.add_argument("--quiet", "-q", action="store_true", help="Keine Zwischenlogs")

    args = parser.parse_args()

    # Default-Modelle pro Provider
    if args.model is None:
        defaults = {
            "anthropic": "claude-sonnet-4-20250514",
            "openai": "gpt-4o",
            "ollama": "llama3.1",
        }
        args.model = defaults[args.llm_provider]

    config = build_config(args)
    orchestrator = Orchestrator(config)

    # Input bestimmen
    if args.interactive:
        _interactive_mode(orchestrator, args)
        return

    if args.file:
        text = args.file.read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        parser.error("Kein Input.  Nutze: text-argument, --file, --interactive, oder stdin-pipe.")
        return

    text = text.strip()

    # Leeren Input abfangen
    if not text:
        parser.error("Der eingegebene Text ist leer.")
        return

    # Input-Länge begrenzen
    if len(text) > config.max_input_chars:
        print(
            f"⚠️  Warnung: Text wurde auf {config.max_input_chars} Zeichen gekürzt "
            f"(war {len(text)} Zeichen).",
            file=sys.stderr,
        )
        text = text[: config.max_input_chars]

    result = orchestrator.analyze(text)

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(format_result(result))


def _interactive_mode(orchestrator: Orchestrator, args: argparse.Namespace) -> None:
    """Interaktiver Modus – liest Texte von stdin."""
    print("🛡️  FakeNewsGuard – Interaktiver Modus")
    print("Gib einen Text ein (leere Zeile = analysieren, 'exit' = beenden):\n")

    while True:
        lines: list[str] = []
        try:
            while True:
                line = input("│ " if lines else "┌ ")
                if line.strip().lower() == "exit":
                    print("\n👋 Auf Wiedersehen!")
                    return
                if line == "" and lines:
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Auf Wiedersehen!")
            return

        if not lines:
            continue

        text = "\n".join(lines).strip()
        if not text:
            continue

        # Input-Länge begrenzen
        max_chars = orchestrator.config.max_input_chars
        if len(text) > max_chars:
            print(f"⚠️  Text auf {max_chars} Zeichen gekürzt.", file=sys.stderr)
            text = text[:max_chars]

        print()
        result = orchestrator.analyze(text)

        if args.json:
            print(result.model_dump_json(indent=2))
        else:
            print(format_result(result))

        print("\n" + "=" * 58 + "\n")


if __name__ == "__main__":
    main()
