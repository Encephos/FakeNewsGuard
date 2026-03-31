# Testing

> Zurück: [[README]] | Siehe auch: [[Agenten]], [[Orchestrator]]

FakeNewsGuard verwendet **pytest** mit asyncio-Support für Unit- und Integrationstests.

---

## Test-Suite-Struktur

```
tests/
├── conftest.py                 # Globale Fixtures
├── unit/                       # 45 Module
│   ├── test_orchestrator_v2.py
│   ├── test_cove_processor.py
│   ├── test_calibration_tracker.py
│   ├── test_evidence_builder.py
│   ├── test_verdict_agent.py
│   ├── test_verdict_rating_calibration.py
│   ├── test_adaptive_search.py
│   ├── test_image_analyzer.py
│   ├── test_claim_processor.py
│   ├── test_fact_checker.py
│   ├── test_number_auditor.py
│   ├── test_rhetoric_analyzer.py
│   ├── test_synthesizer_aggregation.py
│   ├── test_confidence_calibration.py
│   ├── test_claim_router.py
│   ├── test_input_validation.py
│   ├── test_api.py
│   ├── test_evidence_quality.py
│   ├── test_source_policy_enforcement.py
│   ├── test_hint_generation.py
│   ├── test_retrieval_efficiency.py
│   ├── test_current_state_claims.py
│   ├── test_regulatory_claim_handling.py
│   ├── test_regression_disinfo.py
│   ├── test_regression_retrieval_quality.py
│   ├── test_regression_structured_claims.py
│   ├── test_gdelt_client.py
│   ├── test_wikipedia_client.py
│   ├── test_wikidata_client.py
│   ├── test_domain_trust.py
│   ├── test_factcheck_local.py
│   └── ... (weitere)
└── tools/
    ├── test_cache.py
    ├── test_retry.py
    └── test_web_search.py
```

**Gesamt:** 1050+ Tests, alle mock-basiert (keine Live-API-Aufrufe)

**Ausgeschlossen per `pytest.ini`:**
- `tests/unit/test_orchestrator.py` – veraltete Legacy-Version, ignoriert via `--ignore`
- Tests mit Marker `integration` und `eval_replay` laufen nicht im Standard-Run

---

## pytest-Konfiguration

```ini
# pytest.ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

`asyncio_mode = auto` bedeutet: alle `async def test_*`-Funktionen werden automatisch als asyncio-Tests erkannt – kein `@pytest.mark.asyncio`-Decorator nötig.

---

## conftest.py – Globale Fixtures

```python
@pytest.fixture
def minimal_config():
    """AppConfig mit Cache deaktiviert, 1 Retry, CoVe deaktiviert."""
    return AppConfig(
        cache=CacheConfig(enabled=False),
        retry=RetryConfig(max_attempts=1),
        cove=CoVeConfig(enabled=False),
    )

@pytest.fixture
def cache_config(tmp_path):
    return CacheConfig(
        enabled=True,
        db_path=str(tmp_path / "test_cache.db"),
        ttl_hours=1
    )

@pytest.fixture
def mock_llm_client(mocker):
    client = mocker.MagicMock(spec=LLMClient)
    client.complete.return_value = '{"rating": "true", "evidence": "Test"}'
    return client

@pytest.fixture
def mock_search_client(mocker):
    client = mocker.MagicMock(spec=WebSearchClient)
    client.multi_search_async.return_value = [
        SearchResult(title="Test", url="https://example.com", snippet="Test snippet")
    ]
    return client

# Einzel-Claim-Fixtures (nicht als Liste):
@pytest.fixture
def sample_factual_claim():
    return Claim(id=1, text="Kriminalität ist seit 2015 um 50% gestiegen", type=ClaimType.FACTUAL, context="")

@pytest.fixture
def sample_statistical_claim():
    return Claim(id=2, text="40% der Einbrüche von Ausländern", type=ClaimType.STATISTICAL, context="")

@pytest.fixture
def sample_processed_claim():
    """ProcessedClaim mit allen erweiterten Feldern (canonical_hash, frame, etc.)"""
    ...

@pytest.fixture
def sample_evidence_pack():
    """Minimales EvidencePack mit Google Fact Check Match + Web-Ergebnis."""
    ...
```

---

## Test-Abhängigkeiten

```
# requirements-dev.txt
pytest
pytest-asyncio
pytest-mock
respx            # Mock für httpx/aiohttp
freezegun        # Zeit einfrieren für TTL-Tests
```

---

## Beispiel-Tests

### Orchestrator

```python
# test_orchestrator.py
async def test_analyze_returns_synthesis_result(minimal_config, mock_llm_client):
    orchestrator = Orchestrator(minimal_config)
    orchestrator.llm_client = mock_llm_client

    result = await orchestrator.analyze_async("Die Erde ist flach.")

    assert isinstance(result, SynthesisResult)
    assert result.overall_rating in OverallRating.__members__.values()
    assert 0.0 <= result.confidence <= 1.0

async def test_analyze_graceful_degradation_on_agent_failure(minimal_config, mock_llm_client):
    # FactChecker wirft Fehler
    mock_llm_client.complete.side_effect = [
        valid_extraction_response,
        Exception("LLM timeout"),   # FactChecker schlägt fehl
        valid_synthesis_response,
    ]

    result = await orchestrator.analyze_async("Text...")
    assert len(result.analysis_errors) > 0   # Fehler gesammelt
    assert result.overall_rating is not None  # Trotzdem Ergebnis
```

### Adaptive Search

```python
# test_adaptive_search.py
async def test_statistical_claim_gets_more_queries(mock_llm_client, mock_search_client):
    fact_checker = FactCheckerAgent(config, mock_llm_client, mock_search_client)

    statistical_claim = Claim(id=1, text="BIP wuchs um 2%", type=ClaimType.STATISTICAL)
    factual_claim = Claim(id=2, text="Scholz wurde 1958 geboren", type=ClaimType.FACTUAL)

    await fact_checker.run_async(statistical_claim)
    stat_calls = mock_search_client.multi_search_async.call_count

    mock_search_client.reset_mock()

    await fact_checker.run_async(factual_claim)
    fact_calls = mock_search_client.multi_search_async.call_count

    # Statistischer Claim → mehr Suchanfragen
    assert stat_calls > fact_calls
```

### Cache-Tests

```python
# test_cache.py
def test_cache_set_and_get(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("key1", {"rating": "true"}, agent="fact_checker")
    result = cache.get("key1")
    assert result == {"rating": "true"}

def test_cache_ttl_expiry(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("key1", {"rating": "true"}, agent="fact_checker")

    with freeze_time(datetime.now() + timedelta(hours=25)):
        result = cache.get("key1")
        assert result is None   # Abgelaufen
```

---

## Tests ausführen

```bash
# Alle Tests:
pytest

# Nur Unit-Tests:
pytest tests/unit/

# Mit Ausgabe:
pytest -v

# Mit Coverage:
pytest --cov=. --cov-report=html

# Einzelne Datei:
pytest tests/unit/test_orchestrator.py -v
```

---

## Verwandte Dokumente

- [[Agenten]] – Getestete Agenten
- [[Orchestrator]] – Haupttestgegenstand
- [[Cache]] – Cache-Logik (getestet mit tmp_path)
- [[Retry]] – Retry-Logik (getestet mit freeze_time)
