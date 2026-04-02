"""Zentrales Telemetry-Modul – OpenTelemetry Tracing + Prometheus Metriken.

Wenn OTEL_ENABLED != true, werden NoOp-Provider verwendet (kein Overhead).
Prometheus-Metriken sind immer aktiv (Mikrosekunden pro Inkrement).
"""

from __future__ import annotations

import os
import re

from opentelemetry import trace, metrics
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from config.infrastructure import TelemetryConfig

_initialized = False

# ── Path-Normalisierung fuer Label-Kardinalitaet ─────────────────────────
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_HEX_ID_RE = re.compile(r"(?<=/)[0-9a-f]{8,}(?=/|$)", re.IGNORECASE)


def normalize_path(path: str) -> str:
    """Ersetzt UUID- und Hex-ID-Segmente in Pfaden durch {id}."""
    path = _UUID_RE.sub("{id}", path)
    path = _HEX_ID_RE.sub("{id}", path)
    return path


# ── OpenTelemetry Setup ──────────────────────────────────────────────────

def setup_telemetry(config: TelemetryConfig) -> None:
    """Initialisiert TracerProvider + OTLP-Exporter.

    Bei otel_enabled=False bleibt der Default-NoOp-Provider aktiv.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    if not config.otel_enabled:
        return

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({
        "service.name": config.service_name,
        "service.version": os.getenv("APP_VERSION", "dev"),
        "deployment.environment": os.getenv("DEPLOYMENT_ENV", "development"),
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=config.otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)


def get_tracer(name: str) -> trace.Tracer:
    """Gibt einen OTEL-Tracer zurueck (NoOp wenn OTEL deaktiviert)."""
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    """Gibt einen OTEL-Meter zurueck (NoOp wenn OTEL deaktiviert)."""
    return metrics.get_meter(name)


# ── Prometheus Metriken (Modul-Level Singletons) ─────────────────────────

REQUEST_COUNT = Counter(
    "fng_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)
REQUEST_DURATION = Histogram(
    "fng_requests_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)
ACTIVE_JOBS = Gauge(
    "fng_active_jobs",
    "Currently active analysis jobs",
)
CACHE_HITS = Counter(
    "fng_cache_hits_total",
    "Total cache hits",
)
CACHE_MISSES = Counter(
    "fng_cache_misses_total",
    "Total cache misses",
)
LLM_REQUEST_COUNT = Counter(
    "fng_llm_requests_total",
    "Total LLM API calls",
    ["model", "agent"],
)
LLM_DURATION = Histogram(
    "fng_llm_duration_seconds",
    "LLM call duration in seconds",
    ["model", "agent"],
)
