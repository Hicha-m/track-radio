"""Create and verify a consistent SQLite snapshot while the database is live."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def backup(source_path: Path, target_path: Path) -> int:
    source_path = source_path.resolve(strict=True)
    target_path = target_path.resolve()
    if source_path == target_path:
        raise ValueError("La source et la destination doivent être différentes.")
    if target_path.exists():
        raise FileExistsError(target_path)
    if not target_path.parent.is_dir():
        raise FileNotFoundError(target_path.parent)

    source_uri = f"{source_path.as_uri()}?mode=ro"
    last_percent = -10

    def report(_status: int, remaining: int, total: int) -> None:
        nonlocal last_percent
        percent = 100 if total == 0 else (total - remaining) * 100 // total
        if percent >= last_percent + 10 or percent == 100:
            print(f"Sauvegarde SQLite : {percent}%", flush=True)
            last_percent = percent

    created = False
    try:
        descriptor = os.open(target_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
        created = True
        with closing(sqlite3.connect(source_uri, uri=True)) as source:
            with closing(sqlite3.connect(target_path)) as target:
                source.backup(target, pages=512, progress=report, sleep=0.01)
                result = target.execute("PRAGMA quick_check").fetchone()
                if result != ("ok",):
                    raise sqlite3.DatabaseError(f"Contrôle SQLite échoué : {result!r}")
                table_count = target.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
                ).fetchone()[0]
        print(f"Instantané SQLite vérifié ({table_count} tables).", flush=True)
        return table_count
    except BaseException:
        if created:
            target_path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(args.source, args.destination)
