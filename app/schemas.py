from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    enabled: bool
    source_type: str


class PlayOut(BaseModel):
    id: int
    station_id: int
    station_name: str
    artist: str | None
    title: str
    detected_at: datetime
    last_seen_at: datetime
    source: str


class StatsOut(BaseModel):
    artist: str | None
    title: str
    plays: int = Field(ge=1)
