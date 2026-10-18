"""Synchroniser les radios depuis config/stations.json."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from app.config import settings
from app.models import Station
from app.services.metadata import fetch_metadata
from app.services.station_config import load_stations, sync_stations


async def probe(value):
    metadata = await fetch_metadata(Station(**value.model_dump()), settings.request_timeout_seconds)
    if metadata is None:
        raise SystemExit("Aucun titre trouvé : vérifier la page, les sélecteurs et la source.")
    print(json.dumps(asdict(metadata), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=Path(settings.stations_file))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Valider sans modifier la base")
    mode.add_argument("--probe", metavar="SLUG", help="Tester une source sans écrire dans la base")
    args = parser.parse_args()
    if args.probe:
        value = next((v for v in load_stations(args.file) if v.slug == args.probe), None)
        if value is None:
            parser.error("Slug absent du fichier de configuration.")
        asyncio.run(probe(value))
    elif args.check:
        values = load_stations(args.file)
        print(f"Configuration valide : {len(values)} radios.")
    else:
        sync_stations(args.file)
