from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    stream_url: str
    enabled: bool
    source_type: str
    website_url: str | None = None


class PlayOut(BaseModel):
    id: int
    station_id: int
    station_name: str
    artist: str | None
    title: str
    detected_at: datetime
    last_seen_at: datetime
    source: str
    metadata: dict | None = None


class StatsOut(BaseModel):
    artist: str | None
    title: str
    plays: int = Field(ge=1)
    metadata: dict | None = None
