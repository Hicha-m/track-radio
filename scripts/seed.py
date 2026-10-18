"""Applique la configuration des radios au démarrage, sans effacer leur historique."""

from app.services.station_config import sync_stations

if __name__ == "__main__":
    sync_stations()
