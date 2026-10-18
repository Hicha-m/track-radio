import json

import pytest

from app.models import Station
from app.services.station_config import StationConfig, apply_stations, load_stations


def test_manual_config_updates_preserve_station_ids_and_disable_missing(db):
    db.add_all(
        [
            Station(name="Old", slug="same", stream_url="https://example.com/old"),
            Station(name="Removed", slug="removed", stream_url="https://example.com"),
        ]
    )
    db.commit()
    station = db.query(Station).filter_by(slug="same").one()
    original_id = station.id
    values = [
        StationConfig(slug="same", name="New name", stream_url="https://example.com/new"),
        StationConfig(slug="new", name="Added", stream_url="https://example.com"),
    ]
    apply_stations(db, values)
    assert db.query(Station).count() == 3
    assert station.id == original_id
    assert station.name == "New name"
    assert db.query(Station).filter_by(slug="removed").one().enabled is False
    apply_stations(db, values)
    assert db.query(Station).count() == 3


def test_config_validation_has_no_database_side_effects(tmp_path):
    path = tmp_path / "stations.json"
    value = {"slug": "test", "name": "Test", "stream_url": "https://example.com"}
    path.write_text(json.dumps([value, value]))
    with pytest.raises(ValueError, match="unique"):
        load_stations(path)
    path.write_text(json.dumps([{**value, "stream_url": "http://127.0.0.1"}]))
    with pytest.raises(ValueError):
        load_stations(path)


def test_json_source_requires_paths():
    with pytest.raises(ValueError):
        StationConfig(
            slug="test",
            name="Test",
            stream_url="https://example.com",
            source_type="json",
            metadata_url="https://example.com/api",
        )
