"""Cross-Reference Graph endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .dependencies import get_graph

router = APIRouter()


@router.get("/api/graph/stats")
async def graph_stats() -> dict:
    """Statistiken des Cross-Reference Graphen."""
    graph = get_graph()
    return graph.stats()


@router.get("/api/graph/actor/{actor_name}")
async def graph_actor(actor_name: str) -> dict:
    """Alle Claims, in denen ein Akteur erwaehnt wird."""
    graph = get_graph()
    claims = graph.get_actor_claims(actor_name)
    return {
        "actor": actor_name,
        "claims": [
            {"id": c.id, "text": c.label, "rating": c.properties.get("rating", "")}
            for c in claims
        ],
    }


@router.get("/api/graph/source/{domain}")
async def graph_source(domain: str) -> dict:
    """Wie oft und in welchem Kontext wurde eine Quelle verwendet?"""
    graph = get_graph()
    return graph.get_source_history(domain)


@router.get("/api/graph/search")
async def graph_search(
    type: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> dict:
    """Suche im Graphen nach Knoten."""
    graph = get_graph()
    nodes = graph.find_nodes(node_type=type, label_search=q, limit=limit)
    return {
        "nodes": [
            {"id": n.id, "type": n.type, "label": n.label, "properties": n.properties}
            for n in nodes
        ],
    }


@router.get("/api/graph/node/{node_id:path}")
async def graph_node(node_id: str) -> dict:
    """Knoten mit allen Kanten."""
    graph = get_graph()
    node = graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")
    edges = graph.get_edges(node_id)
    neighbors = graph.get_neighbors(node_id)
    return {
        "node": {"id": node.id, "type": node.type, "label": node.label, "properties": node.properties},
        "edges": [
            {"source": e.source_id, "target": e.target_id, "relation": e.relation}
            for e in edges
        ],
        "neighbors": [
            {"id": n.id, "type": n.type, "label": n.label}
            for n in neighbors
        ],
    }
