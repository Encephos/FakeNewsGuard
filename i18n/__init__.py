"""Internationalisierung (i18n) – Zentrales Übersetzungssystem.

Nutzung:
    from i18n import t
    t("agents.fact_checker.system_prompt")           # Standardsprache (de)
    t("agents.fact_checker.system_prompt", "en")     # Englisch

Die Sprache wird aus AppConfig.language gelesen (Default: "de").
Neue Sprachen: Einfach eine neue Datei in i18n/locales/ anlegen.
"""

from __future__ import annotations

import importlib
from typing import Any

# Registry der geladenen Locales: { "de": { ... }, "en": { ... } }
_locales: dict[str, dict[str, Any]] = {}
_default_locale: str = "de"


def _load_locale(locale: str) -> dict[str, Any]:
    """Lade ein Locale-Modul dynamisch."""
    if locale in _locales:
        return _locales[locale]

    try:
        mod = importlib.import_module(f"i18n.locales.{locale}")
        data = getattr(mod, "STRINGS", {})
        _locales[locale] = data
        return data
    except ModuleNotFoundError:
        return {}


def set_default_locale(locale: str) -> None:
    """Setze die Standard-Sprache global."""
    global _default_locale
    _default_locale = locale


def get_default_locale() -> str:
    return _default_locale


def t(key: str, locale: str | None = None) -> str:
    """Hole einen übersetzten String anhand eines Punkt-separierten Keys.

    Beispiel:
        t("agents.fact_checker.system_prompt")
        t("api.errors.no_text", "en")

    Fallback-Kette: angefordertes Locale → Default-Locale → Key selbst.
    """
    lang = locale or _default_locale
    data = _load_locale(lang)

    result = _resolve(data, key)
    if result is not None:
        return result

    # Fallback auf Default-Locale
    if lang != _default_locale:
        data = _load_locale(_default_locale)
        result = _resolve(data, key)
        if result is not None:
            return result

    # Letzter Fallback: Key selbst
    return key


def _resolve(data: dict[str, Any], key: str) -> str | None:
    """Navigiere verschachtelte Dicts anhand eines Punkt-separierten Keys."""
    parts = key.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current if isinstance(current, str) else None


def available_locales() -> list[str]:
    """Liste aller verfügbaren Sprachen."""
    import os
    locale_dir = os.path.join(os.path.dirname(__file__), "locales")
    locales = []
    for f in os.listdir(locale_dir):
        if f.endswith(".py") and f != "__init__.py":
            locales.append(f.removesuffix(".py"))
    return sorted(locales)
