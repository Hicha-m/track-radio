"""Synchroniser les radios depuis config/stations.json."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Station
from app.services.html_sources import HtmlConfig
from app.services.network import validate_url


class StationConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    slug: str = Field(min_length=1, max_length=90, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=2, max_length=100)
    stream_url: str = Field(max_length=2048)
    website_url: str | None = Field(None, max_length=2048)
    enabled: bool = True
    source_type: Literal["icy", "json", "html"] = "icy"
    metadata_url: str | None = Field(None, max_length=2048)
    json_artist_path: str | None = Field(None, max_length=200)
    json_title_path: str | None = Field(None, max_length=200)
    html: HtmlConfig | None = None

    @field_validator("stream_url", "metadata_url", "website_url")
    @classmethod
    def public_url(cls, value):
        if value is not None:
            validate_url(value)
        return value

    @model_validator(mode="after")
    def complete_source(self):
        if self.source_type in {"json", "html"} and not self.metadata_url:
            raise ValueError("metadata_url est obligatoire pour une source JSON ou HTML.")
        if self.source_type == "json" and not self.json_title_path:
            raise ValueError("json_title_path est obligatoire pour une source JSON.")
        if self.source_type == "html" and self.html is None:
            raise ValueError("html est obligatoire : préciser l’adaptateur et ses sélecteurs.")
        if self.source_type != "html" and self.html is not None:
            raise ValueError("html ne s’utilise qu’avec source_type=html.")
        return self


def load_stations(path):
    values = TypeAdapter(list[StationConfig]).validate_python(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
    if len(values) > settings.max_stations:
        raise ValueError(f"Maximum {settings.max_stations} radios suivies.")
    for field in ("slug", "name"):
        identifiers = [getattr(value, field) for value in values]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError(f"Chaque {field} doit être unique dans la configuration.")
    return values


def apply_stations(db, values):
    existing = {station.slug: station for station in db.scalars(select(Station))}
    wanted = {value.slug for value in values}
    for value in values:
        station = existing.get(value.slug)
        if station is None:
            db.add(Station(**value.model_dump(), config_enabled=value.enabled, configured=True))
        else:
            station.config_enabled = value.enabled
            station.configured = True
            for field, content in value.model_dump(exclude={"enabled"}).items():
                setattr(station, field, content)
            station.enabled = (
                station.enabled_override if station.enabled_override is not None else value.enabled
            )
    for slug, station in existing.items():
        if slug not in wanted:
            station.configured = False
            station.enabled = False
    db.commit()


def sync_stations(path=None):
    values = load_stations(path or settings.stations_file)
    with SessionLocal() as db:
        apply_stations(db, values)
    print(f"Configuration appliquée : {len(values)} radios ; historique conservé.")
