# Retry-Logik

> Zurück: [[Tools]] | Siehe auch: [[LLM-Abstraktion]], [[Websuche]]

`tools/retry.py` implementiert **Exponential Backoff mit Jitter** für HTTP-Anfragen an LLM-APIs und Suchmaschinen.

---

## Grundprinzip

Bei transienten Fehlern (Rate-Limits, Server-Überlastung) wird die Anfrage nach einer Wartezeit erneut versucht. Die Wartezeit wächst exponentiell, um überlastete Server nicht weiter zu belasten.

---

## Funktionen

```python
# Synchron:
result = retry_call(fn, *args, config=retry_config, **kwargs)

# Asynchron:
result = await retry_call_async(async_fn, *args, config=retry_config, **kwargs)
```

Kein Decorator-Pattern – standalone Funktionen die beliebige callables wrappen.

---

## Retry-Strategie

### Welche Fehler werden wiederholt?

| HTTP-Status / Fehler | Retry? | Grund |
|---|---|---|
| 429 Too Many Requests | **Ja** | Rate-Limit, wartet ab |
| 500 Internal Server Error | **Ja** | Transient |
| 502 Bad Gateway | **Ja** | Transient |
| 503 Service Unavailable | **Ja** | Transient |
| 504 Gateway Timeout | **Ja** | Transient |
| Kein Response (Netzwerkfehler) | **Ja** | Immer retry |
| 400 Bad Request | Nein | Fehler in der Anfrage selbst |
| 401 Unauthorized | Nein | Fehlender/falscher API-Key |
| 403 Forbidden | Nein | Keine Berechtigung |
| 404 Not Found | Nein | Ressource existiert nicht |

### Wartezeit-Berechnung

```python
delay = base_delay * (backoff_factor ** attempt)
delay = delay * (0.5 + random.random())   # ±50% Jitter
delay = min(delay, max_delay)
```

**Beispiel** (Standard-Konfiguration: base=1s, factor=2):
- Versuch 1: ~1s (0.5–1.5s)
- Versuch 2: ~2s (1–3s)
- Versuch 3: ~4s (2–6s) → dann Fehler

### Jitter

Der ±50%-Jitter verhindert **Thundering Herd**: Wenn viele Clients gleichzeitig einen 429 bekommen und alle nach exakt derselben Zeit erneut anfragen, treffen sie wieder gemeinsam ein. Mit Jitter verteilen sich die Anfragen zeitlich.

---

## RetryConfig

```python
@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0     # Sekunden
    backoff_factor: float = 2.0
    max_delay: float = 60.0     # Sekunden
```

→ [[Konfiguration]]

---

## Verwendung

RetryConfig wird dem `LLMClient` und `AsyncWebSearchClient` beim Erstellen übergeben:

```python
# orchestrator.py
retry_config = RetryConfig(max_attempts=3, base_delay=1.0)
llm_client = LLMClient(config.llm, retry_config=retry_config)
search_client = AsyncWebSearchClient(config.search, retry_config=retry_config)
```

---

## Verwandte Dokumente

- [[LLM-Abstraktion]] – nutzt Retry für alle API-Anfragen
- [[Websuche]] – nutzt Retry für Such-Anfragen
- [[Konfiguration]] – RetryConfig
