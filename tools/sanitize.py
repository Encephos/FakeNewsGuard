"""Input sanitization for LLM prompt injection defense.

Provides functions to wrap and sanitize user-provided text before it is
injected into LLM prompts. Does NOT modify the semantic content — only
neutralizes known injection patterns and applies structural boundaries.

Defense-in-depth:
    1. Structural: XML-style <user_input> tags to delimit untrusted content
    2. Pattern-based: Strip known injection prefixes/commands
    3. Length: Enforce per-field character limits
"""

from __future__ import annotations

import re

# ── Known injection patterns ────────────────────────────────────────────────
# Patterns that attempt to override system instructions or inject new roles.
# Case-insensitive, applied to the raw text before LLM submission.

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    # English injection attempts
    re.compile(
        r"(?:ignore|disregard|forget|override|bypass)\s+"
        r"(?:all\s+)?(?:(?:previous|prior|above|earlier|system)\s+)+"
        r"(?:instructions?|prompts?|rules?|context)",
        re.IGNORECASE,
    ),
    re.compile(
        r"you\s+are\s+now\s+(?:a|an|in)\s+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:new\s+)?(?:system\s*[:>]|<<\s*SYS\s*>>|<\|system\|>)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:assistant|user|human|system)\s*:\s*\n",
        re.IGNORECASE,
    ),
    # German injection attempts
    re.compile(
        r"(?:ignoriere|vergiss|überschreibe|umgehe)\s+"
        r"(?:alle\s+)?(?:vorherigen?|bisherigen?|obigen?)\s+"
        r"(?:anweisungen?|instruktionen?|regeln?|prompts?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"du\s+bist\s+(?:jetzt|nun|ab\s+sofort)\s+(?:ein|eine)\s+",
        re.IGNORECASE,
    ),
]

# Replacement marker (visible in logs, neutral for LLM)
_SANITIZED_MARKER = "[SANITIZED]"


def sanitize_user_text(text: str, max_length: int = 100_000) -> str:
    """Sanitize user-provided text for safe LLM prompt inclusion.

    1. Enforces max_length
    2. Strips known injection patterns
    3. Does NOT wrap in tags (use wrap_user_input for that)

    Returns sanitized text. Original semantics preserved where possible.
    """
    if not text:
        return text

    # Length limit
    if len(text) > max_length:
        text = text[:max_length]

    # Strip injection patterns
    for pattern in _INJECTION_PATTERNS:
        text = pattern.sub(_SANITIZED_MARKER, text)

    return text


def wrap_user_input(text: str, label: str = "user_input") -> str:
    """Wrap text in XML-style tags for structural prompt boundary.

    The tags signal to the LLM that everything inside is untrusted
    user content and should only be analyzed, not executed.
    """
    return f"<{label}>\n{text}\n</{label}>"


def sanitize_and_wrap(
    text: str,
    label: str = "user_input",
    max_length: int = 100_000,
) -> str:
    """Convenience: sanitize + wrap in one call."""
    return wrap_user_input(sanitize_user_text(text, max_length), label)
