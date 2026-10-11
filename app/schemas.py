from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    enabled: bool
    source_type: str
    stream_url: str


class StationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    stream_url: str = Field(min_length=8, max_length=2000)
    enabled: bool = True

    @field_validator("stream_url")
    @classmethod
    def valid_stream_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("L'URL doit commencer par http:// ou https://")
        return value


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
