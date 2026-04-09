#!/usr/bin/env python3
"""Migrate graph SOURCE nodes from domain-based to full-URL-based IDs.

Reads all archived analyses and re-populates the graph so that each
unique source URL gets its own node instead of sharing one per domain.

Run on the server where PostgreSQL is available (DB_BACKEND=postgres).
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AppConfig
from tools.db.factory import create_archive, create_graph


def main() -> None:
    config = AppConfig()
    archive = create_archive(config)
    graph = create_graph(config)

    # Step 1: Delete old domain-based SOURCE nodes + their edges
    print("Deleting old SOURCE nodes and their edges...")
    with graph._conn() as conn:
        conn.execute(
            "DELETE FROM graph_edges WHERE source_id IN "
            "(SELECT id FROM graph_nodes WHERE type = 'SOURCE') "
            "OR target_id IN "
            "(SELECT id FROM graph_nodes WHERE type = 'SOURCE')"
        )
        cur = conn.execute("DELETE FROM graph_nodes WHERE type = 'SOURCE'")
        deleted = cur.rowcount
        conn.commit()
    print(f"  Removed {deleted} old SOURCE nodes.")

    # Step 2: Re-populate from archive
    print("Re-populating graph from archive...")
    offset = 0
    total = 0
    while True:
        batch = archive.list(limit=100, offset=offset)
        items = batch.get("items", [])
        if not items:
            break
        for item in items:
            full = archive.get(item["id"])
            if not full:
                continue
            result = full.get("result") or json.loads(full.get("result_json", "{}"))
            claims = result.get("claims", [])
            if claims:
                graph.populate_from_result(
                    analysis_id=item["id"],
                    claims_analysis=claims,
                    original_text=full.get("input_text", ""),
                )
                total += 1
        offset += len(items)

    print(f"  Re-populated graph from {total} archived analyses.")

    stats = graph.stats()
    source_count = stats.get("nodes_by_type", {}).get("SOURCE", 0)
    print(f"  New SOURCE node count: {source_count}")
    print("Done.")


if __name__ == "__main__":
    main()
