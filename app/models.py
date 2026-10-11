from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Station(Base):
    __tablename__ = "stations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    stream_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20), default="icy")
    metadata_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    json_artist_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    json_title_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    plays: Mapped[list["Play"]] = relationship(back_populates="station")


class Play(Base):
    __tablename__ = "plays"
    __table_args__ = (Index("ix_plays_station_detected", "station_id", "detected_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"))
    artist: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    raw_metadata: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(20))
    station: Mapped[Station] = relationship(back_populates="plays")

