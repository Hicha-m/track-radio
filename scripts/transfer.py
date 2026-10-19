"""Export/import JSONL gzip, sans supprimer ni modifier la base source."""

import argparse
import gzip
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, func, insert, inspect, select

from app.database import engine
from app.models import Play, Station, TrackMetadata

TABLES = {
    "stations": Station.__table__,
    "plays": Play.__table__,
    "track_metadata": TrackMetadata.__table__,
}


def export_data(path):
    counts = {}
    with engine.connect() as connection, gzip.open(path, "xt", encoding="utf-8") as output:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
        elif engine.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        connection = connection.execution_options(yield_per=250)
        for name, table in TABLES.items():
            if not inspect(connection).has_table(name):
                if name == "track_metadata":
                    continue  # Backup an installation from before metadata migration.
                raise ValueError(f"Table manquante : {name}")
            counts[name] = 0
            for row in connection.execute(select(table)).mappings().yield_per(250):
                output.write(json.dumps({"table": name, "row": dict(row)}, default=str) + "\n")
                counts[name] += 1
        output.write(json.dumps({"counts": counts}) + "\n")
    print(json.dumps(counts))


def import_data(path):
    if engine.dialect.name != "sqlite":
        raise ValueError("La destination doit être SQLite.")
    counts = {name: 0 for name in TABLES}
    manifest = None
    with engine.begin() as connection:
        for table in TABLES.values():
            if connection.scalar(select(func.count()).select_from(table)):
                raise ValueError("Import refusé : la destination contient déjà des données.")
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line in source:
                record = json.loads(line)
                if "counts" in record:
                    manifest = record["counts"]
                    continue
                if manifest is not None:
                    raise ValueError("Export invalide : données après le manifeste.")
                table = TABLES[record["table"]]
                row = record["row"]
                for column in table.columns:
                    if isinstance(column.type, DateTime) and row.get(column.name):
                        row[column.name] = datetime.fromisoformat(row[column.name])
                connection.execute(insert(table), row)
                counts[table.name] += 1
        # Older archives contain only stations and plays. Missing metadata is allowed
        # only when there were no metadata rows in the archive.
        if manifest is not None and "track_metadata" not in manifest:
            manifest = {**manifest, "track_metadata": 0}
        if manifest != counts:
            raise ValueError("Export incomplet : les comptes ne correspondent pas.")
    print(json.dumps(counts))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "import"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    (export_data if args.action == "export" else import_data)(args.path)
