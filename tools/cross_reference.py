"""Cross-Reference Graph – Beziehungen zwischen Claims, Quellen und Akteuren.

Baut über Zeit einen persistenten Wissensgraphen auf:

  Knoten:
    - CLAIM   : Eine geprüfte Behauptung (text, rating, analysis_id)
    - SOURCE  : Eine Quelle/URL (domain, tier)
    - ACTOR   : Ein erwähnter Akteur/Organisation (name, type)

  Kanten:
    - CLAIM → SOURCE     : "supported_by" / "contradicted_by"
    - CLAIM → ACTOR      : "mentions"
    - CLAIM → CLAIM      : "related_to" (gleicher Akteur/Thema)
    - SOURCE → SOURCE    : "cites" (falls Quellen aufeinander verweisen)

Persistenz: SQLite (gleicher Ansatz wie Archive/Cache).
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphNode:
    """Ein Knoten im Cross-Reference Graph."""
    id: str
    type: str          # "CLAIM" | "SOURCE" | "ACTOR"
    label: str         # Anzeigename (Claim-Text, Domain, Akteur-Name)
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """Eine Kante im Cross-Reference Graph."""
    source_id: str
    target_id: str
    relation: str      # "supported_by" | "contradicted_by" | "mentions" | "related_to" | "cites"
    properties: dict[str, Any] = field(default_factory=dict)


class CrossReferenceGraph:
    """Persistenter Cross-Reference Graph in SQLite."""

    def __init__(self, db_path: str = ".fakeguard_graph.db") -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id          TEXT PRIMARY KEY,
                    type        TEXT NOT NULL,
                    label       TEXT NOT NULL,
                    properties  TEXT DEFAULT '{}',
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);

                CREATE TABLE IF NOT EXISTS edges (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id   TEXT NOT NULL,
                    target_id   TEXT NOT NULL,
                    relation    TEXT NOT NULL,
                    properties  TEXT DEFAULT '{}',
                    created_at  REAL NOT NULL,
                    UNIQUE(source_id, target_id, relation)
                );
                CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
                CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ── Knoten ────────────────────────────────────────────────────

    def add_node(self, node: GraphNode) -> None:
        """Füge einen Knoten hinzu oder aktualisiere ihn."""
        now = time.time()
        props = json.dumps(node.properties, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO nodes (id, type, label, properties, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       label = excluded.label,
                       properties = excluded.properties,
                       updated_at = excluded.updated_at""",
                (node.id, node.type, node.label, props, now, now),
            )

    def get_node(self, node_id: str) -> GraphNode | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        return GraphNode(
            id=row["id"], type=row["type"], label=row["label"],
            properties=json.loads(row["properties"]),
        )

    # ── Kanten ────────────────────────────────────────────────────

    def add_edge(self, edge: GraphEdge) -> None:
        """Füge eine Kante hinzu (ignoriert Duplikate)."""
        props = json.dumps(edge.properties, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO edges (source_id, target_id, relation, properties, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (edge.source_id, edge.target_id, edge.relation, props, time.time()),
            )

    def get_edges(
        self,
        node_id: str,
        direction: str = "both",
        relation: str | None = None,
    ) -> list[GraphEdge]:
        """Alle Kanten eines Knotens (outgoing, incoming, oder both)."""
        clauses = []
        params: list[Any] = []

        if direction in ("out", "both"):
            clauses.append("source_id = ?")
            params.append(node_id)
        if direction in ("in", "both"):
            clauses.append("target_id = ?")
            params.append(node_id)

        where = " OR ".join(clauses)
        if relation:
            where = f"({where}) AND relation = ?"
            params.append(relation)

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM edges WHERE {where} ORDER BY created_at DESC",
                params,
            ).fetchall()

        return [
            GraphEdge(
                source_id=r["source_id"], target_id=r["target_id"],
                relation=r["relation"],
                properties=json.loads(r["properties"]),
            )
            for r in rows
        ]

    # ── Abfragen ──────────────────────────────────────────────────

    def get_neighbors(self, node_id: str, relation: str | None = None) -> list[GraphNode]:
        """Alle Nachbarknoten (via beliebige Kante)."""
        edges = self.get_edges(node_id, relation=relation)
        neighbor_ids = set()
        for e in edges:
            neighbor_ids.add(e.target_id if e.source_id == node_id else e.source_id)

        nodes = []
        with self._conn() as conn:
            for nid in neighbor_ids:
                row = conn.execute("SELECT * FROM nodes WHERE id = ?", (nid,)).fetchone()
                if row:
                    nodes.append(GraphNode(
                        id=row["id"], type=row["type"], label=row["label"],
                        properties=json.loads(row["properties"]),
                    ))
        return nodes

    def find_nodes(
        self,
        node_type: str | None = None,
        label_search: str | None = None,
        limit: int = 50,
    ) -> list[GraphNode]:
        """Suche nach Knoten."""
        clauses = []
        params: list[Any] = []

        if node_type:
            clauses.append("type = ?")
            params.append(node_type)
        if label_search:
            clauses.append("label LIKE ?")
            params.append(f"%{label_search}%")

        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM nodes WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()

        return [
            GraphNode(
                id=r["id"], type=r["type"], label=r["label"],
                properties=json.loads(r["properties"]),
            )
            for r in rows
        ]

    def get_actor_claims(self, actor_name: str) -> list[GraphNode]:
        """Alle Claims, in denen ein Akteur erwähnt wird."""
        actor_nodes = self.find_nodes(node_type="ACTOR", label_search=actor_name)
        if not actor_nodes:
            return []

        claims = []
        for actor in actor_nodes:
            neighbors = self.get_neighbors(actor.id, relation="mentions")
            claims.extend(n for n in neighbors if n.type == "CLAIM")
        return claims

    def get_source_history(self, domain: str) -> dict[str, Any]:
        """Wie oft und in welchem Kontext wurde eine Quelle verwendet?"""
        source_nodes = self.find_nodes(node_type="SOURCE", label_search=domain)
        if not source_nodes:
            return {"domain": domain, "total_references": 0, "claims": []}

        all_claims = []
        for src in source_nodes:
            edges = self.get_edges(src.id, direction="in")
            for edge in edges:
                claim_node = self.get_node(edge.source_id)
                if claim_node and claim_node.type == "CLAIM":
                    all_claims.append({
                        "claim": claim_node.label[:100],
                        "relation": edge.relation,
                        "rating": claim_node.properties.get("rating", ""),
                    })

        return {
            "domain": domain,
            "total_references": len(all_claims),
            "claims": all_claims,
        }

    def stats(self) -> dict[str, Any]:
        """Graph-Statistiken."""
        with self._conn() as conn:
            nodes_by_type = dict(conn.execute(
                "SELECT type, COUNT(*) FROM nodes GROUP BY type"
            ).fetchall())
            edges_by_relation = dict(conn.execute(
                "SELECT relation, COUNT(*) FROM edges GROUP BY relation"
            ).fetchall())
            total_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_type": nodes_by_type,
            "edges_by_relation": edges_by_relation,
        }

    # ── Populate: Ergebnisse in den Graphen eintragen ─────────────

    def populate_from_result(
        self,
        analysis_id: str,
        claims_analysis: list[dict[str, Any]],
        original_text: str = "",
    ) -> None:
        """Trage die Ergebnisse einer Analyse in den Graphen ein.

        Args:
            analysis_id: ID der Analyse (z.B. archive_id)
            claims_analysis: Liste der Claim-Ergebnisse (aus transform_result)
            original_text: Der Originaltext für Akteur-Extraktion
        """
        claim_node_ids: list[str] = []

        for claim in claims_analysis:
            claim_id = f"{analysis_id}:{claim.get('id', 'C?')}"
            claim_text = claim.get("text", "")
            claim_rating = claim.get("rating", "")

            # Claim-Knoten
            self.add_node(GraphNode(
                id=claim_id, type="CLAIM", label=claim_text,
                properties={
                    "rating": claim_rating,
                    "claim_type": claim.get("type", ""),
                    "analysis_id": analysis_id,
                },
            ))
            claim_node_ids.append(claim_id)

            # Source-Knoten + Kanten
            sources = claim.get("sources", [])
            for url in sources:
                source_id = f"src:{url}"
                domain = _extract_domain(url)
                label = url.replace("https://", "").replace("http://", "").replace("www.", "")
                if len(label) > 60:
                    label = label[:57] + "..."

                self.add_node(GraphNode(
                    id=source_id, type="SOURCE", label=label,
                    properties={"url": url, "domain": domain},
                ))

                relation = "supported_by" if claim_rating in ("TRUE", "MOSTLY_TRUE") else "referenced_by"
                self.add_edge(GraphEdge(
                    source_id=claim_id, target_id=source_id, relation=relation,
                ))

            # Akteur-Extraktion (einfache Heuristik: Wörter mit Großbuchstaben)
            actors = _extract_actors(claim_text)
            for actor in actors:
                actor_id = f"actor:{actor.lower().replace(' ', '_')}"
                self.add_node(GraphNode(
                    id=actor_id, type="ACTOR", label=actor,
                ))
                self.add_edge(GraphEdge(
                    source_id=claim_id, target_id=actor_id, relation="mentions",
                ))

        # Beziehungen zwischen Claims der gleichen Analyse
        for i, cid_a in enumerate(claim_node_ids):
            for cid_b in claim_node_ids[i + 1:]:
                self.add_edge(GraphEdge(
                    source_id=cid_a, target_id=cid_b, relation="related_to",
                    properties={"reason": "same_analysis"},
                ))


def _extract_domain(url: str) -> str:
    """Extrahiere die Domain aus einer URL."""
    match = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else url


def _extract_actors(text: str) -> list[str]:
    """Extrahiere potenzielle Akteure aus einem Claim-Text.

    Einfache Heuristik: Sequenzen von Wörtern mit Großbuchstaben (> 2 Wörter)
    die keine typischen Satzanfänge sind. Kein ML nötig.
    """
    # Suche nach aufeinanderfolgenden großgeschriebenen Wörtern
    # z.B. "Bundesamt für Migration" oder "Angela Merkel"
    # Pattern 1: Eigennamen (zwei+ großgeschriebene Wörter direkt hintereinander)
    names = re.findall(
        r'\b([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)+)\b',
        text,
    )
    # Pattern 2: Organisationen mit Bindewörtern (Bundesamt für Migration und Flüchtlinge)
    orgs = re.findall(
        r'\b([A-ZÄÖÜ][a-zäöüß]+(?:\s+(?:für|und|der|des|von|zu)\s+'
        r'[A-ZÄÖÜ]?[a-zäöüß]+)+)\b',
        text,
    )
    # Pattern 3: Abkürzungen (BAMF, BKA, etc.)
    abbrevs = re.findall(r'\b([A-ZÄÖÜ]{2,6})\b', text)

    candidates = names + orgs + abbrevs

    # Filtere zu kurze oder generische Matches
    stopwords = {
        "Die Regierung", "Der Staat", "Das Land", "Die Menschen",
        "Die Studie", "Der Bericht", "Die Daten", "Das Ergebnis",
    }
    actors = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if len(candidate) < 4 or candidate in stopwords:
            continue
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            actors.append(candidate)

    return actors[:10]  # Max 10 Akteure pro Claim
