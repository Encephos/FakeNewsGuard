"""Unit-Tests für Source-Adapter: Normalisierung, Policies, Caching, Rate-Limits.

Tests decken ab:
1. Adapter-Interface-Konsistenz (search, fetch_details, normalize)
2. OfficialEvidenceItem Normalisierung und Validierung
3. SourceCache und Circuit-Breaker
4. Rate-Limiting pro Source
5. Policy-Durchsetzung (Storage, Display, Commercial Use)
6. Mock-Responses für 2 Beispieladapter (WorldBank, ClinicalTrials)
"""

from __future__ import annotations

import json
import tempfile
from datetime import date
from unittest.mock import MagicMock, Mock, patch

import pytest

from models.source_evidence import (
    FactType,
    NormalizedFact,
    OfficialEvidenceItem,
    compute_recency_score,
)
from tools.sources.adapter_guardian import (
    AdapterGuardian,
    CircuitBreaker,
    CircuitBreakerError,
    CircuitBreakerState,
    SourceCache,
    SourceRateLimiter,
)
from tools.sources.clients import (
    ArXivClient,
    WorldBankClient,
    ClinicalTrialsClient,
    OpenAlexClient,
)
from tools.sources.registry import SourceRegistry
from tools.sources.types import AllowedDisplay, AllowedStorage, ClaimDomain, CommercialUsePolicy


# ── Interface-Konsistenz Tests ──────────────────────────────────────────────────

class TestAdapterInterface:
    """Verifiziere, dass alle Adapter das Interface implementieren."""

    def test_all_adapters_have_required_methods(self):
        """Alle Adapter müssen search, fetch_details, normalize haben."""
        adapters = [
            WorldBankClient,
            ClinicalTrialsClient,
            OpenAlexClient,
        ]

        for adapter_cls in adapters:
            client = adapter_cls()
            assert hasattr(client, 'search'), f"{adapter_cls.__name__} missing search()"
            assert hasattr(client, 'fetch_details'), f"{adapter_cls.__name__} missing fetch_details()"
            assert hasattr(client, 'normalize'), f"{adapter_cls.__name__} missing normalize()"
            assert hasattr(client, 'get_policy'), f"{adapter_cls.__name__} missing get_policy()"

    def test_all_adapters_have_config(self):
        """Jeder Adapter hat config aus SourceRegistry."""
        adapters = [WorldBankClient, ClinicalTrialsClient, OpenAlexClient]
        for adapter_cls in adapters:
            client = adapter_cls()
            assert client.config is not None
            assert client.config.source_id
            assert client.config.authority_weight > 0

    def test_get_policy_default_implementation(self):
        """get_policy() sollte default SourceConfig zurückgeben."""
        client = WorldBankClient()
        policy = client.get_policy()
        assert policy.source_id == "world_bank"

    def test_policy_kwargs_contains_all_fields(self):
        """_policy_kwargs() muss alle notwendigen Felder haben."""
        client = ClinicalTrialsClient()
        kwargs = client._policy_kwargs()

        required_keys = {
            "source_id",
            "source_class",
            "authority_score",
            "license_status",
            "storage_policy",
            "display_policy",
            "domains",
        }
        assert required_keys.issubset(kwargs.keys())

        # Typenprüfung
        assert isinstance(kwargs["source_id"], str)
        assert isinstance(kwargs["authority_score"], float)
        assert isinstance(kwargs["domains"], list)


# ── Normalisierung & OfficialEvidenceItem Tests ──────────────────────────────────

class TestOfficialEvidenceItemNormalization:
    """Teste die Normalisierung von Adapter-Ausgaben."""

    def test_evidence_item_confidence_calculation(self):
        """compute_confidence() nutzt authority_score, claim_relevance, recency_score."""
        item = OfficialEvidenceItem(
            source_id="test",
            source_class="test.TestClient",
            record_id="123",
            title="Test",
            url="http://test.com",
            abstract="Test abstract",
            authority_score=0.90,
            claim_relevance=0.65,
            recency_score=0.80,
        )

        confidence = item.compute_confidence()
        # 0.90 * 0.4 + 0.65 * 0.4 + 0.80 * 0.2 = 0.36 + 0.26 + 0.16 = 0.78
        assert 0.77 < confidence < 0.79

    def test_evidence_item_recency_scoring(self):
        """compute_recency_score() exponentieller Verfall."""
        today = date.today()

        # Heute: Score ~1.0
        score_today = compute_recency_score(today, half_life_years=1.0)
        assert score_today > 0.99

        # Vor 1 Jahr (= half-life): Score ~0.5
        import datetime
        one_year_ago = today - datetime.timedelta(days=365)
        score_year = compute_recency_score(one_year_ago, half_life_years=1.0)
        assert 0.45 < score_year < 0.55

    def test_evidence_item_to_legacy_evidence_item(self):
        """to_evidence_item() Konvertierung für Abwärtskompatibilität."""
        item = OfficialEvidenceItem(
            source_id="clinicaltrials",
            source_class="tools.sources.clients.clinicaltrials.ClinicalTrialsClient",
            record_id="NCT04788511",
            title="Study Title",
            url="http://ct.gov/study",
            abstract="Abstract text",
            authority_score=0.91,
            claim_relevance=0.70,
            display_policy=AllowedDisplay.EXCERPT,
            normalized_facts=[
                NormalizedFact(
                    fact_type=FactType.TRIAL_STATUS,
                    subject="NCT",
                    predicate="Status",
                    value="COMPLETED",
                    source_snippet="Study completed.",
                )
            ],
        )

        legacy = item.to_evidence_item()
        assert legacy.source.domain_tier == 1  # 0.91 → tier 1
        assert legacy.relevance_score == 0.70
        assert len(legacy.excerpt) > 0

    def test_normalized_fact_creation(self):
        """NormalizedFact muss alle Felder korrekt setzen."""
        fact = NormalizedFact(
            fact_type=FactType.CLINICAL_OUTCOME,
            subject="Drug Name",
            predicate="Clinical Result",
            value="90% efficacy",
            numeric_value=0.90,
            unit="%",
            reference_period="2023",
            qualifier="n=1000, RCT",
            source_snippet="90% of patients showed improvement.",
            confidence=0.95,
        )

        assert fact.fact_type == FactType.CLINICAL_OUTCOME
        assert fact.numeric_value == 0.90
        assert fact.confidence == 0.95


# ── SourceCache Tests ──────────────────────────────────────────────────────────

class TestSourceCache:
    """Test SQLite-basiertes Caching für Source-Ergebnisse."""

    @pytest.fixture
    def cache(self):
        """Temp-DB für Tests."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        cache = SourceCache(db_path, default_ttl_hours=24)
        yield cache
        # Cleanup
        import os
        try:
            os.unlink(db_path)
        except:
            pass

    def test_cache_set_and_get(self, cache):
        """Cache set/get mit OfficialEvidenceItem."""
        items = [
            OfficialEvidenceItem(
                source_id="test",
                source_class="test.TestClient",
                record_id="1",
                title="Item 1",
                url="http://test.com/1",
                abstract="Abstract 1",
            ),
            OfficialEvidenceItem(
                source_id="test",
                source_class="test.TestClient",
                record_id="2",
                title="Item 2",
                url="http://test.com/2",
                abstract="Abstract 2",
            ),
        ]

        cache.set("test_source", items, query="test query")
        cached = cache.get("test_source", query="test query")

        assert cached is not None
        assert len(cached) == 2
        assert cached[0].title == "Item 1"
        assert cached[1].title == "Item 2"

    def test_cache_ttl_expiration(self, cache):
        """Cache-Einträge mit TTL sollten ablaufen."""
        items = [
            OfficialEvidenceItem(
                source_id="test",
                source_class="test.TestClient",
                record_id="1",
                title="Item",
                url="http://test.com",
                abstract="Abstract",
            ),
        ]

        cache.set("test", items, query="q", ttl_hours=0.0001)  # Sehr kurz

        # Sofort abruf sollte funktionieren
        assert cache.get("test", query="q") is not None

        # Nach kurzer Zeit abgelaufen
        import time
        time.sleep(0.5)
        assert cache.get("test", query="q") is None

    def test_cache_delete(self, cache):
        """Manuelles Cache-Löschen."""
        items = [
            OfficialEvidenceItem(
                source_id="test",
                source_class="test.TestClient",
                record_id="1",
                title="Item",
                url="http://test.com",
                abstract="Abstract",
            ),
        ]

        cache.set("test", items, record_id="123")
        assert cache.get("test", record_id="123") is not None

        cache.delete("test", record_id="123")
        assert cache.get("test", record_id="123") is None

    def test_cache_stats(self, cache):
        """Cache-Statistiken."""
        items = [
            OfficialEvidenceItem(
                source_id="test",
                source_class="test.TestClient",
                record_id="1",
                title="Item",
                url="http://test.com",
                abstract="Abstract",
            ),
        ]

        cache.set("source_a", items, query="q1")
        cache.set("source_b", items, query="q2")

        stats = cache.stats()
        assert stats["total_entries"] == 2
        assert "source_a" in stats["entries_per_source"]
        assert "source_b" in stats["entries_per_source"]


# ── Rate Limiter Tests ──────────────────────────────────────────────────────────

class TestSourceRateLimiter:
    """Token-Bucket Rate-Limiting pro Source."""

    def test_rate_limiter_allows_requests_within_limit(self):
        """Anfragen innerhalb des Limits sollten durchgehen."""
        limiter = SourceRateLimiter()

        # 2 RPS Limit
        allowed, retry = limiter.acquire("test_source", rate_rps=2.0)
        assert allowed is True
        assert retry == 0.0

    def test_rate_limiter_blocks_excess_requests(self):
        """Zu viele Anfragen sollten blockiert werden."""
        limiter = SourceRateLimiter()

        # 1 RPS Limit
        allowed1, _ = limiter.acquire("test_source", rate_rps=1.0)
        assert allowed1 is True

        # Zweite sofort sollte blockiert sein
        allowed2, retry = limiter.acquire("test_source", rate_rps=1.0)
        assert allowed2 is False
        assert retry > 0.0

    def test_rate_limiter_unbounded_when_none(self):
        """rate_rps=None sollte unbegrenzt sein."""
        limiter = SourceRateLimiter()

        for _ in range(100):
            allowed, _ = limiter.acquire("test", rate_rps=None)
            assert allowed is True

    def test_rate_limiter_per_source_isolation(self):
        """Verschiedene Quellen haben separate Limits."""
        limiter = SourceRateLimiter()

        # Source A: 1 RPS
        allowed_a1, _ = limiter.acquire("source_a", rate_rps=1.0)
        assert allowed_a1 is True

        allowed_a2, _ = limiter.acquire("source_a", rate_rps=1.0)
        assert allowed_a2 is False  # Limit erreicht für A

        # Source B: sollte unabhängig sein
        allowed_b1, _ = limiter.acquire("source_b", rate_rps=1.0)
        assert allowed_b1 is True


# ── Circuit Breaker Tests ──────────────────────────────────────────────────────

class TestCircuitBreaker:
    """Circuit-Breaker Zustandsmaschine."""

    def test_circuit_breaker_initial_state_closed(self):
        """Circuit-Breaker startet CLOSED."""
        cb = CircuitBreaker("test_source")
        assert cb.state == CircuitBreakerState.CLOSED

    def test_circuit_breaker_opens_on_failures(self):
        """Nach N Fehlern → OPEN."""
        cb = CircuitBreaker("test", failure_threshold=3)

        def failing_func():
            raise ValueError("Test error")

        for i in range(3):
            with pytest.raises(ValueError):
                cb.call(failing_func)

        assert cb.state == CircuitBreakerState.OPEN

    def test_circuit_breaker_rejects_when_open(self):
        """OPEN-Zustand lehnt neue Anfragen ab."""
        cb = CircuitBreaker("test", failure_threshold=1)

        def failing_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            cb.call(failing_func)

        assert cb.state == CircuitBreakerState.OPEN

        # Neue Anfrage sollte CircuitBreakerError werfen
        with pytest.raises(CircuitBreakerError):
            cb.call(lambda: "success")

    def test_circuit_breaker_half_open_after_timeout(self):
        """Nach Timeout wechselt zu HALF_OPEN."""
        cb = CircuitBreaker("test", failure_threshold=1, timeout_seconds=0.1)

        def failing_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            cb.call(failing_func)

        assert cb.state == CircuitBreakerState.OPEN

        # Warte bis timeout
        import time
        time.sleep(0.15)

        # Nächster Aufruf sollte HALF_OPEN sein
        try:
            cb.call(lambda: "success")
        except Exception:
            pass

        assert cb.state == CircuitBreakerState.HALF_OPEN

    def test_circuit_breaker_closes_after_success(self):
        """Nach N Erfolgen in HALF_OPEN → CLOSED."""
        cb = CircuitBreaker(
            "test",
            failure_threshold=1,
            success_threshold=2,
            timeout_seconds=0.01,
        )

        # Öffne den Breaker
        def failing_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            cb.call(failing_func)

        assert cb.state == CircuitBreakerState.OPEN

        # Warte bis timeout
        import time
        time.sleep(0.02)

        # Erfolgreiche Aufrufe in HALF_OPEN
        cb.call(lambda: "success 1")
        assert cb.state == CircuitBreakerState.HALF_OPEN

        cb.call(lambda: "success 2")
        assert cb.state == CircuitBreakerState.CLOSED


# ── AdapterGuardian Integration Tests ──────────────────────────────────────────

class TestAdapterGuardian:
    """Integration aller Schutzmaßnahmen."""

    @pytest.fixture
    def guardian(self):
        """AdapterGuardian mit Temp-DB."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        guardian = AdapterGuardian(cache_db=db_path)
        yield guardian
        import os
        try:
            os.unlink(db_path)
        except:
            pass

    def test_guardian_search_with_cache(self, guardian):
        """Guardian cached search-Ergebnisse."""
        mock_adapter = Mock()
        mock_adapter.config.source_id = "test_source"
        mock_adapter.config.rate_limit_rps = 10.0

        mock_item = OfficialEvidenceItem(
            source_id="test_source",
            source_class="test.TestClient",
            record_id="1",
            title="Result",
            url="http://test.com",
            abstract="Abstract",
        )
        mock_adapter.search.return_value = [mock_item]

        # Erste Anfrage → sucht
        results1 = guardian.search(mock_adapter, "test query")
        assert len(results1) == 1
        assert mock_adapter.search.call_count == 1

        # Zweite Anfrage gleiche Query → aus Cache
        results2 = guardian.search(mock_adapter, "test query")
        assert len(results2) == 1
        assert mock_adapter.search.call_count == 1  # Nicht aufgerufen!

    def test_guardian_respects_rate_limits(self, guardian):
        """Guardian blockt wenn Rate-Limit überschritten."""
        mock_adapter = Mock()
        mock_adapter.config.source_id = "test_source"
        mock_adapter.config.rate_limit_rps = 1.0  # 1 RPS

        mock_item = OfficialEvidenceItem(
            source_id="test_source",
            source_class="test.TestClient",
            record_id="1",
            title="Result",
            url="http://test.com",
            abstract="Abstract",
        )
        mock_adapter.search.return_value = [mock_item]

        # Erste Anfrage
        results1 = guardian.search(mock_adapter, "query1", use_cache=False)
        assert len(results1) == 1

        # Zweite Anfrage sofort sollte blockiert sein (kein Cache)
        results2 = guardian.search(mock_adapter, "query2", use_cache=False)
        assert len(results2) == 0  # Blockiert durch Rate-Limit

    def test_guardian_circuit_breaker_opens_on_failures(self, guardian):
        """Guardian öffnet Circuit-Breaker nach mehreren Fehlern."""
        mock_adapter = Mock()
        mock_adapter.config.source_id = "failing_source"
        mock_adapter.config.rate_limit_rps = None  # Kein Rate-Limit

        # Adapter schlägt fehl
        mock_adapter.search.side_effect = Exception("API down")

        # 2 aufeinanderfolgende Fehler (default threshold ist 5, aber wir können ändern)
        cb = guardian.get_circuit_breaker("failing_source")
        cb.failure_threshold = 2  # Lower threshold for testing

        for _ in range(2):
            result = guardian.search(mock_adapter, "query", use_cache=False)
            assert len(result) == 0

        # Circuit-Breaker sollte jetzt offen sein
        assert cb.state == CircuitBreakerState.OPEN

        # Weitere Anfrage sollte sofort blockiert sein (CircuitBreakerError)
        result = guardian.search(mock_adapter, "query", use_cache=False)
        assert len(result) == 0  # Returns [] on CircuitBreakerError

    def test_guardian_fetch_details_with_cache(self, guardian):
        """Guardian cached fetch_details."""
        mock_adapter = Mock()
        mock_adapter.config.source_id = "test_source"
        mock_adapter.config.rate_limit_rps = 10.0

        mock_item = OfficialEvidenceItem(
            source_id="test_source",
            source_class="test.TestClient",
            record_id="NCT123",
            title="Detail",
            url="http://test.com",
            abstract="Abstract",
        )
        mock_adapter.fetch_details.return_value = mock_item

        # Erste Anfrage
        result1 = guardian.fetch_details(mock_adapter, "NCT123")
        assert result1.record_id == "NCT123"
        assert mock_adapter.fetch_details.call_count == 1

        # Zweite Anfrage gleiche ID → aus Cache
        result2 = guardian.fetch_details(mock_adapter, "NCT123")
        assert result2.record_id == "NCT123"
        assert mock_adapter.fetch_details.call_count == 1  # Nicht aufgerufen!

    def test_guardian_stats(self, guardian):
        """Guardian gibt Statistiken zurück."""
        stats = guardian.stats()
        assert "cache" in stats
        assert "circuit_breakers" in stats
        assert stats["cache"]["total_entries"] == 0


# ── Policy-Durchsetzung Tests ──────────────────────────────────────────────────

class TestSourcePolicies:
    """Test Storage, Display, Commercial Use Policies."""

    def test_source_config_storage_policy(self):
        """SourceConfig hat storage_policy."""
        config = SourceRegistry.get("world_bank")
        assert config.allowed_storage == AllowedStorage.CACHE

        # Check one source has a defined storage policy
        for source_id in ["pubmed", "arxiv", "eur_lex"]:
            config = SourceRegistry.get(source_id)
            assert config.allowed_storage is not None

    def test_source_config_display_policy(self):
        """SourceConfig hat display_policy."""
        config = SourceRegistry.get("world_bank")
        assert config.allowed_display == AllowedDisplay.FULL

    def test_source_config_commercial_use(self):
        """SourceConfig hat commercial_reuse_ok."""
        config = SourceRegistry.get("world_bank")
        assert config.commercial_reuse_ok == CommercialUsePolicy.ALLOWED

    def test_evidence_item_inherits_policies(self):
        """OfficialEvidenceItem erbt Policies von SourceConfig."""
        client = WorldBankClient()
        kwargs = client._policy_kwargs()

        item = OfficialEvidenceItem(
            **kwargs,
            record_id="test",
            title="Test",
            url="http://test.com",
            abstract="Abstract",
        )

        assert item.license_status == CommercialUsePolicy.ALLOWED
        assert item.storage_policy == AllowedStorage.CACHE
        assert item.display_policy == AllowedDisplay.FULL


# ── Mock Response Tests (WorldBank & ClinicalTrials) ────────────────────────────

class TestWorldBankMockResponses:
    """Mock-Tests für WorldBankClient mit realistischen API-Responses."""

    def test_worldbank_normalize_valid_record(self):
        """WorldBank normalize() mit gültigem Datenpunkt."""
        client = WorldBankClient()

        mock_record = {
            "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
            "country": {"id": "DE", "value": "Germany"},
            "countryiso3code": "DEU",
            "date": "2023",
            "value": 4082669200000.0,
            "unit": "",
            "decimal": 0,
        }

        item = client.normalize(mock_record)
        assert item.record_id == "DEU/NY.GDP.MKTP.CD/2023"
        assert item.title == "GDP (current US$) – Germany (2023)"
        assert item.authority_score == 0.88
        assert len(item.normalized_facts) == 1

    def test_worldbank_fact_type(self):
        """WorldBank erzeugt INDICATOR_VALUE Facts."""
        client = WorldBankClient()

        mock_record = {
            "indicator": {"id": "SP.URB.TOTL.IN.ZS", "value": "Urban population (% of total)"},
            "country": {"id": "FR", "value": "France"},
            "countryiso3code": "FRA",
            "date": "2022",
            "value": 82.5,
            "unit": "%",
            "decimal": 1,
        }

        item = client.normalize(mock_record)
        assert len(item.normalized_facts) > 0
        assert item.normalized_facts[0].fact_type == FactType.INDICATOR_VALUE
        assert item.normalized_facts[0].numeric_value == 82.5


class TestClinicalTrialsMockResponses:
    """Mock-Tests für ClinicalTrialsClient."""

    def test_clinicaltrials_normalize_valid_study(self):
        """ClinicalTrials normalize() mit realistischer Studie."""
        client = ClinicalTrialsClient()

        mock_study = {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT04788511",
                    "briefTitle": "SURMOUNT-1: Semaglutide vs Placebo",
                },
                "statusModule": {
                    "overallStatus": "COMPLETED",
                    "primaryCompletionDateStruct": {"date": "2023-06"},
                    "startDateStruct": {"date": "2021-01"},
                },
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Novo Nordisk"}},
                "descriptionModule": {"briefSummary": "Phase 3 RCT on obesity."},
                "conditionsModule": {"conditions": ["Obesity"]},
                "armsInterventionsModule": {"interventions": [{"name": "Semaglutid 2.4mg"}]},
                "outcomesModule": {"primaryOutcomes": [{"measure": "Weight reduction"}]},
                "designModule": {
                    "studyType": "INTERVENTIONAL",
                    "phases": ["PHASE3"],
                    "enrollmentInfo": {"count": 2539},
                },
            }
        }

        item = client.normalize(mock_study)
        assert item.record_id == "NCT04788511"
        assert "SURMOUNT-1" in item.title
        assert item.authority_score == 0.91
        assert len(item.normalized_facts) >= 2  # Status + Enrollment

    def test_clinicaltrials_trial_status_fact(self):
        """ClinicalTrials erzeugt TRIAL_STATUS Facts."""
        client = ClinicalTrialsClient()

        mock_study = {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT99999999",
                    "briefTitle": "Test Study",
                },
                "statusModule": {
                    "overallStatus": "RECRUITING",
                },
                "designModule": {"studyType": "INTERVENTIONAL"},
            }
        }

        item = client.normalize(mock_study)
        # Finde TRIAL_STATUS fact
        status_facts = [f for f in item.normalized_facts if f.fact_type == FactType.TRIAL_STATUS]
        assert len(status_facts) > 0
        assert "Rekrutierend" in status_facts[0].value


class TestArXivMockResponses:
    """Mock-Tests für ArXivClient.normalize() und _normalize_entry()."""

    _SAMPLE_RECORD = {
        "id": "http://arxiv.org/abs/2301.12345v2",
        "title": "Attention Is All You Need",
        "summary": "We propose a new simple network architecture, the Transformer.",
        "published": "2023-01-15T12:34:56Z",
        "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
        "categories": ["cs.AI", "cs.LG"],
    }

    def test_arxiv_normalize_valid_record(self):
        client = ArXivClient()
        item = client.normalize(self._SAMPLE_RECORD)

        assert item.record_id == "2301.12345v2"
        assert item.title == "Attention Is All You Need"
        assert item.authority_score == 0.70
        assert len(item.normalized_facts) == 1

    def test_arxiv_normalize_research_finding_fact_type(self):
        client = ArXivClient()
        item = client.normalize(self._SAMPLE_RECORD)

        assert item.normalized_facts[0].fact_type == FactType.RESEARCH_FINDING

    def test_arxiv_normalize_confidence_computed(self):
        client = ArXivClient()
        item = client.normalize(self._SAMPLE_RECORD)

        # authority_score=0.70 → 0.70 * 0.40 = 0.28 Minimum
        assert item.confidence > 0

    def test_arxiv_normalize_entry_xml(self):
        """_normalize_entry() mit manuell konstruiertem ET.Element."""
        import xml.etree.ElementTree as ET

        ATOM = "http://www.w3.org/2005/Atom"
        ARXIV_NS = "http://arxiv.org/schemas/atom"

        entry = ET.Element(f"{{{ATOM}}}entry")

        id_elem = ET.SubElement(entry, f"{{{ATOM}}}id")
        id_elem.text = "http://arxiv.org/abs/2301.12345v2"

        title_elem = ET.SubElement(entry, f"{{{ATOM}}}title")
        title_elem.text = "Test Paper Title"

        summary_elem = ET.SubElement(entry, f"{{{ATOM}}}summary")
        summary_elem.text = "Test abstract text"

        published_elem = ET.SubElement(entry, f"{{{ATOM}}}published")
        published_elem.text = "2023-01-15T12:34:56Z"

        author_elem = ET.SubElement(entry, f"{{{ATOM}}}author")
        name_elem = ET.SubElement(author_elem, f"{{{ATOM}}}name")
        name_elem.text = "Author One"

        cat_elem = ET.SubElement(entry, f"{{{ARXIV_NS}}}primary-category")
        cat_elem.set("term", "cs.AI")

        client = ArXivClient()
        item = client._normalize_entry(entry)

        assert isinstance(item, OfficialEvidenceItem)
        assert item.record_id == "2301.12345v2"

    def test_arxiv_normalize_missing_fields(self):
        """normalize() mit leerem Dict – kein Exception, Item zurückgegeben."""
        client = ArXivClient()
        item = client.normalize({})

        assert isinstance(item, OfficialEvidenceItem)
        assert item.record_id == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
