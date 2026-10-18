from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.network import validate_url


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    stream_url: str
    enabled: bool
    source_type: str


class StationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=2, max_length=100)
    stream_url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True

    @field_validator("stream_url")
    @classmethod
    def public_stream_url(cls, value: str) -> str:
        validate_url(value)
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
