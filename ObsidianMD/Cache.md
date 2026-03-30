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
    def get(self, claim_text: str, agent_name: str, context: str = "",
            canonical_text: str | None = None, use_canonical: bool = False) -> dict | None
    def set(self, claim_text: str, agent_name: str, result: dict, context: str = "",
            canonical_text: str | None = None, use_canonical: bool = False) -> None
    def delete(self, claim_text: str, agent_name: str, context: str = "") -> None
    def clear_expired(self) -> int    # Gibt Anzahl gelöschter Einträge zurück
    def stats(self) -> dict           # { enabled, total_entries, valid_entries, expired_entries }
```

---

## Semantic Cache (Optional)

Wenn `sentence-transformers` installiert ist und `CacheConfig.semantic_cache=True`:

### Embedding-basierte Similarity-Suche

Bei einem **exakten Cache-Miss** führt das System einen **Fallback** durch:

```python
# get() – bei exaktem Key-Miss:
if cache_miss:
    if self._semantic_enabled:
        # Fallback: Suche semantisch ähnliche Claims
        return self._semantic_lookup(claim_text, agent_name)
    return None
```

**Ablauf:**
1. **Baseline-Lookup:** Exakter SHA256-Hash-Match (schnell)
2. **Fallback bei Miss:** Embedding-ähnliche Claims suchen (optional, nur wenn Modell verfügbar)
3. **Ähnlichkeits-Schwellenwert:** 0.92 Cosine-Similarity

### Embedding-Storage

Embeddings werden bei `set()` berechnet und in der `claim_embeddings`-Tabelle gespeichert:

```sql
CREATE TABLE claim_embeddings (
    cache_key TEXT PRIMARY KEY,
    embedding BLOB NOT NULL,  -- Binäre Vektoren (float32)
    FOREIGN KEY (cache_key) REFERENCES claim_cache(cache_key) ON DELETE CASCADE
);
```

Das Modell `all-MiniLM-L6-v2` wird **lazy-loaded** beim ersten Aufruf, danach im Memory gecacht.

### Graceful Degradation

- Wenn `sentence-transformers` nicht installiert ist → nur exakte Key-Matches (wie vorher)
- Wenn `semantic_cache=False` in der Config → nur exakte Key-Matches
- Keine Performance-Penalty für exakte Matches – Embeddings nur beim Fallback genutzt

**Anwendungsfall:** Trend-Claims die in vielen Variationen auftauchen:
- "Inflation um 10% gestiegen" (exakt)
- "Preissteigerungen von etwa 10 Prozent" (Paraphrase)
- "Die Inflationsrate betrug ~10%" (weitere Variante)

Alle drei erhalten verschiedene exakte Keys, aber bei Cache-Miss werden die ersten beiden via Semantic Cache gefunden.

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
