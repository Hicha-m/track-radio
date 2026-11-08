from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class GlobalExclusion(Base):
    __tablename__ = "global_exclusions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artist: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    artist_normalized: Mapped[str] = mapped_column(Text)
    title_normalized: Mapped[str] = mapped_column(Text)
    __table_args__ = (Index("ix_global_exclusions_match", "artist_normalized", "title_normalized"),)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    key_signature: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TrackMetadata(Base):
    __tablename__ = "track_metadata"
    __table_args__ = (
        Index("ix_track_metadata_genre_cover", "id", "primary_genre").ddl_if(dialect="sqlite"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artist: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider: Mapped[str] = mapped_column(String(20), default="deezer")
    temporary_cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    lyrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lyrics_candidates: Mapped[list] = mapped_column(JSON, default=list)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lyrics_status: Mapped[str] = mapped_column(String(20), default="pending")
    automatic_done: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    primary_genre: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Station(Base):
    __tablename__ = "stations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    stream_url: Mapped[str] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), default="icy")
    metadata_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    json_artist_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    json_title_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    json_started_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    html: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    collection_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    config_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    configured: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    plays: Mapped[list["Play"]] = relationship(back_populates="station")


class Play(Base):
    __tablename__ = "plays"
    __table_args__ = (
        Index("ix_plays_station_detected", "station_id", "detected_at"),
        Index("ix_plays_detected", "detected_at"),
        # Keep analytics off the much wider source-payload rows on SQLite.
        Index(
            "ix_plays_chart_cover", "artist", "title", "detected_at", "station_id", "track_id"
        ).ddl_if(dialect="sqlite"),
        Index(
            "ix_plays_period_cover", "detected_at", "artist", "title", "station_id", "track_id"
        ).ddl_if(dialect="sqlite"),
        Index("ix_plays_track_station_detected", "track_id", "station_id", "detected_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"))
    artist: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    raw_metadata: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(20))
    station: Mapped[Station] = relationship(back_populates="plays")


@event.listens_for(Play, "before_insert")
def assign_track_identity(mapper, connection, target):
    from app.services.track_identity import identity

    target.track_id = identity(target.artist, target.title)


class YouTubeMatch(Base):
    __tablename__ = "youtube_matches"
    track_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artist: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    selected: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceQuota(Base):
    __tablename__ = "service_quotas"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    day: Mapped[str] = mapped_column(String(10))
    used: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkerState(Base):
    __tablename__ = "worker_states"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    state: Mapped[str] = mapped_column(String(20))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    activity: Mapped[str] = mapped_column(String(100), default="")


class WorkerEvent(Base):
    __tablename__ = "worker_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    worker: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    code: Mapped[str] = mapped_column(String(40))


class CollectionStatus(Base):
    """Technical counters only: never store track identities or upstream payloads."""

    __tablename__ = "collection_status"
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_observation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probe_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(32), default="unknown")
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    runtime: Mapped[dict] = mapped_column(JSON, default=dict)


class CollectionEvent(Base):
    __tablename__ = "collection_events"
    __table_args__ = (
        Index("ix_collection_station_id", "station_id", "id"),
        Index("ix_collection_started", "started_at"),
        Index("ix_collection_pending", "kind", "outcome", "id"),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(32), unique=True)
    round_id: Mapped[str | None] = mapped_column(String(32))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16), default="collection")
    source: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
