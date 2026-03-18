"""Tests für die zentrale Input-Validierung im Orchestrator."""

from __future__ import annotations

import pytest

from orchestrator import InputValidationError


# ── _validate_input ──────────────────────────────────────────────


def test_validate_input_strips_whitespace(minimal_config):
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    result = orch._validate_input("  Hello World  ")
    assert result == "Hello World"


def test_validate_input_rejects_empty_string(minimal_config):
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    with pytest.raises(InputValidationError, match="Kein Text"):
        orch._validate_input("")


def test_validate_input_rejects_whitespace_only(minimal_config):
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    with pytest.raises(InputValidationError, match="Kein Text"):
        orch._validate_input("   \n\t  ")


def test_validate_input_truncates_long_text(minimal_config):
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config
    orch.config.max_input_chars = 100

    long_text = "a" * 500
    result = orch._validate_input(long_text)
    assert len(result) == 100


def test_validate_input_passes_normal_text(minimal_config):
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    text = "Die Kriminalität ist gestiegen."
    result = orch._validate_input(text)
    assert result == text


def test_analyze_raises_on_empty_input(minimal_config):
    """analyze() sollte InputValidationError bei leerem Input werfen."""
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    with pytest.raises(InputValidationError):
        orch.analyze("")


@pytest.mark.asyncio
async def test_analyze_async_raises_on_empty_input(minimal_config):
    """analyze_async() sollte InputValidationError bei leerem Input werfen."""
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    with pytest.raises(InputValidationError):
        await orch.analyze_async("")
