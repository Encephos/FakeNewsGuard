# LLM-Abstraktion

> Zurück: [[Tools]] | Siehe auch: [[Scout-Tiers]], [[Konfiguration]]

`tools/llm.py` stellt eine **provider-agnostische LLM-Schnittstelle** bereit. Alle Agenten kommunizieren ausschliesslich über diese Abstraktion mit Sprachmodellen – nie direkt mit provider-spezifischen SDKs.

---

## Unterstützte Provider

| Provider | Env-Var | Besonderheit |
|---|---|---|
| **Anthropic** | `ANTHROPIC_API_KEY` | Native Tool-Use für Structured Output |
| **OpenAI** | `OPENAI_API_KEY` | JSON Schema für Structured Output |
| **OpenRouter** | `OPENROUTER_API_KEY` | Multi-Modell-Router, sortiert nach Preis |
| **Ollama** | – (lokal) | Kein API-Key, lokale Ausführung |

---

## LLMClient-Interface

```python
class LLMClient:
    def __init__(self, config: LLMConfig)

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        response_format: str = "text"  # "text" | "json"
    ) -> str

    async def complete_structured(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,         # JSON Schema
        tool_name: str        # Name des Tools (Anthropic) / Schema-Name (OpenAI)
    ) -> dict

    async def complete_vision(
        self,
        system_prompt: str,
        user_message: str,
        image_urls: list[str]
    ) -> str
```

---

## Structured Output

Die wichtigste Methode ist `complete_structured()`. Sie nutzt provider-native Mechanismen:

### Anthropic (Tool-Use)
```python
# Intern: tool_use mit input_schema
{
    "type": "tool",
    "name": tool_name,
    "input_schema": schema
}
```

### OpenAI / OpenRouter (JSON Schema)
```python
# Intern: response_format mit json_schema
{
    "type": "json_schema",
    "json_schema": { "name": tool_name, "schema": schema }
}
```

### Fallback
Bei jedem Fehler mit `complete_structured()` → automatischer Fallback auf `complete_json()`:
```python
try:
    return await self.complete_structured(...)
except Exception:
    return await self.complete(... , response_format="json")
```

---

## OpenRouter-Besonderheiten

OpenRouter-Anfragen bekommen automatisch Provider-Einstellungen:

```python
"provider": {
    "sort": "price",           # Günstigster Provider zuerst
    "allow_fallbacks": true    # Auf andere Provider ausweichen wenn nötig
}
```

Das minimiert Kosten im [[Scout-Tiers|LITE-Tier]] ohne Konfigurationsaufwand.

---

## Modelle ohne System-Prompt

Einige Modelle (Gemma, Free-Tier-Router) unterstützen keinen separaten System-Prompt. Der Client erkennt das automatisch und **faltet den System-Prompt in die User-Message**:

```python
# Falls kein system_prompt unterstützt:
user_message = f"<system>{system_prompt}</system>\n\n{user_message}"
```

---

## Timeout

Jede LLM-Anfrage hat einen **Timeout von 120 Sekunden**. Bei Überschreitung wird ein `asyncio.TimeoutError` geworfen, den `run_safe_async()` als graceful degradation behandelt.

---

## Vision / Multimodal

`complete_vision()` übergibt Bild-URLs als multimodalen Content:

```python
# Anthropic-Format intern:
{
    "type": "image",
    "source": { "type": "url", "url": image_url }
}
```

Nur für [[Agent-ImageAnalyzer]] verwendet. Erfordert Vision-fähiges Modell.

---

## LLMConfig

```python
@dataclass
class LLMConfig:
    provider: str = "openrouter"
    model: str = "auto"
    temperature: float = 0.1
    max_tokens: int = 4096
    api_key: str = ""
    base_url: str = ""
```

→ [[Konfiguration]]

---

## Verwandte Dokumente

- [[Scout-Tiers]] – Welche Modelle pro Tier
- [[Konfiguration]] – LLMConfig im Detail
- [[Retry]] – Retry-Logik für LLM-Anfragen
- [[Agent-ImageAnalyzer]] – nutzt complete_vision()
- [[Datenmodelle]] – JSON-Schemas für Structured Output
