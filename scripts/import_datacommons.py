#!/usr/bin/env python3
"""Import-Script für DataCommons Fact Check Dataset.

Lädt ClaimReview-Daten aus einer JSON-Datei in die lokale SQLite-Datenbank.

Datenquelle:
    https://datacommons.org/factcheck/download

Verwendung:
    python scripts/import_datacommons.py data/factchecks.json
    python scripts/import_datacommons.py data/factchecks.json --db data/factcheck_local.db

Lizenz: CC-BY 4.0 – Attribution bei Nutzung erforderlich.
"""

import argparse
import sys
from pathlib import Path

# Projekt-Root zum Pfad hinzufügen
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.factcheck_local import LocalFactCheckDatabase


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importiere DataCommons Fact Check Daten in die lokale SQLite-DB."
    )
    parser.add_argument(
        "json_file",
        help="Pfad zur DataCommons JSON-Datei mit ClaimReview-Daten.",
    )
    parser.add_argument(
        "--db",
        default="data/factcheck_local.db",
        help="Pfad zur SQLite-Datenbank (Standard: data/factcheck_local.db).",
    )
    args = parser.parse_args()

    db = LocalFactCheckDatabase(db_path=args.db)

    print(f"Importiere aus: {args.json_file}")
    print(f"Datenbank:      {args.db}")

    count = db.import_datacommons(args.json_file)

    print(f"Importiert:     {count} Einträge")
    print(f"Gesamt in DB:   {db.count()} Einträge")

    db.close()


if __name__ == "__main__":
    main()
