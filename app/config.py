from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./radio.db"
    poll_interval_seconds: int = Field(20, ge=5, le=3600)
    request_timeout_seconds: float = Field(12, ge=1, le=60)
    collector_concurrency: int = Field(3, ge=1, le=4)
    max_stations: int = Field(50, ge=1, le=100)
    max_response_bytes: int = Field(512 * 1024, ge=4096, le=2 * 1024 * 1024)
    max_icy_metaint: int = Field(65536, ge=1024, le=262144)
    last_seen_write_seconds: int = Field(120, ge=20, le=3600)
    admin_session_hours: int = Field(8, ge=1, le=24)
    admin_token: SecretStr = SecretStr("")
    lastfm_api_key: SecretStr = SecretStr("")
    youtube_api_key: SecretStr = SecretStr("")
    youtube_searches_per_day: int = Field(80, ge=1, le=10000)
    spotify_client_id: SecretStr = SecretStr("")
    spotify_client_secret: SecretStr = SecretStr("")
    stats_cache_seconds: int = Field(30, ge=1, le=300)
    retention_days: int = Field(180, ge=0)
    archive_directory: str = "data/archives"
    stations_file: str = "config/stations.json"
    log_level: str = "INFO"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
