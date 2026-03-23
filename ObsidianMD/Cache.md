# Cache

> Zurück: [[Tools]] | Siehe auch: [[Agent-FactChecker]], [[Agent-NumberAuditor]]

`tools/cache.py` implementiert einen **SQLite-basierten Claim-Cache** mit TTL. Er verhindert redundante LLM-Anfragen für identische Claims.

---

## Zweck

Wenn derselbe Claim erneut geprüft wird (z.B. virale Behauptung die viele Nutzer einreichen), muss das System nicht jedes Mal von vorne suchen und prüfen. Der Cache liefert das gespeicherte Ergebnis in Millisekunden.

---

## Wer nutzt den Cache?

| Agent | Cache genutzt? |
|---|---|
| ClaimExtractor | Nein |
| **FactChecker** | **Ja** |
| **NumberAuditor** | **Ja** |
| RhetoricAnalyzer | Nein |
| Synthesizer | Nein |
| ImageAnalyzer | Nein |

Synthesizer und RhetoricAnalyzer werden bewusst nicht gecacht – ihre Ergebnisse hängen von zu vielen Faktoren ab.

---

## Cache-Key

Der Schlüssel ist ein **SHA256-Hash** aus:

```python
key = sha256(f"{agent_name}::{claim.text.strip().lower()}::{context[:100]}")
```

- `agent_name`: `"fact_checker"` oder `"number_auditor"`
- Claim-Text wird normalisiert (lowercase, strip)
- Erste 100 Zeichen des Kontexts (verhindert Kollisionen bei identischen Claims in unterschiedlichem Kontext)

---

## TTL und Ablauf

Standard-TTL: **24 Stunden** (konfigurierbar via `CacheConfig.ttl_hours`).

TTL-Prüfung erfolgt **beim Lesen** (`get()`):

```python
def get(self, key: str) -> dict | None:
    row = db.execute("SELECT value, expires_at FROM cache WHERE key=?", (key,))
    if row and row.expires_at > now():
        return json.loads(row.value)
    elif row:
        db.execute("DELETE FROM cache WHERE key=?", (key,))  # Abgelaufen → löschen
    return None
```

---

## SQLite-Schema

```sql
CREATE TABLE cache (
    key       TEXT PRIMARY KEY,
    value     TEXT NOT NULL,        -- JSON
    agent     TEXT NOT NULL,
    expires_at INTEGER NOT NULL,    -- Unix-Timestamp
    created_at INTEGER NOT NULL
);
```

WAL-Modus für Thread-Sicherheit bei gleichzeitigen Schreibzugriffen:
```sql
PRAGMA journal_mode=WAL;
```

---

## Methoden

```python
class ClaimCache:
    def get(self, key: str) -> dict | None
    def set(self, key: str, value: dict, agent: str) -> None
    def delete(self, key: str) -> None
    def clear_expired(self) -> int    # Gibt Anzahl gelöschter Einträge zurück
    def stats(self) -> dict           # { total, expired, agents: {…} }
```

---

## CacheConfig

```python
@dataclass
class CacheConfig:
    enabled: bool = True
    db_path: str = ".fakeguard_cache.db"
    ttl_hours: int = 24
```

→ [[Konfiguration]]

---

## Verwandte Dokumente

- [[Agent-FactChecker]] – Cache-Nutzung und Key-Berechnung
- [[Agent-NumberAuditor]] – Cache-Nutzung
- [[Datenbank]] – Übersicht aller Datenbanken
- [[Konfiguration]] – CacheConfig
