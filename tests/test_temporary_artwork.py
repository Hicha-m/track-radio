"""Artwork may decorate a song, never select or resurrect another recording."""

from datetime import UTC, datetime, timedelta

from app.models import GlobalExclusion, Play, Station
from app.services.catalog import ensure_entry, public_metadata
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.metadata import TrackMetadata
from app.services.station_now_playing import now_playing
from app.services.temporary_artwork import artwork_snapshot, radio_key

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
COVER = "https://media.skyrock.fm/covers/fixture.jpg"


def snapshot(start=-10, end=100):
    begin = (NOW + timedelta(seconds=start)).replace(microsecond=0)
    finish = (NOW + timedelta(seconds=end)).replace(microsecond=0)
    return {
        "ts": int(NOW.timestamp()),
        "radios": {
            "francais": {
                "on_air_program": {"cover_uri": "https://example.test/program.jpg"},
                "schedule": [
                    {
                        "type": "record",
                        "artists": [{"name": "Artist"}],
                        "likes": [{"user_id": "PRIVATE"}],
                        "info": {
                            "uid": 5,
                            "title": "Song",
                            "cover_big_uri": COVER,
                            "start_ts": str(int(begin.timestamp())),
                            "start": begin.isoformat(),
                            "end_ts": str(int(finish.timestamp())),
                            "end": finish.isoformat(),
                        },
                    }
                ],
            }
        },
    }


def test_snapshot_only_current_track_artwork():
    result = artwork_snapshot(snapshot(), NOW)
    assert result["francais"]["url"] == COVER
    assert "PRIVATE" not in str(result) and "program" not in str(result)
    assert artwork_snapshot(snapshot(-200, -100), NOW) == {}
    assert artwork_snapshot(snapshot(10, 100), NOW) == {}
    payload = snapshot()
    payload["ts"] -= 120
    assert artwork_snapshot(payload, NOW) == {}
    payload = snapshot()
    payload["radios"]["francais"]["schedule"][0]["info"].pop("cover_big_uri")
    assert artwork_snapshot(payload, NOW) == {}


def test_radio_key_requires_expected_host_and_mount():
    assert radio_key("https://icecast.skyrock.net/s/francais_aac_128k") == "francais"
    assert radio_key("https://icecast.skyrock.net/s/natio_mp3_128k") == "natio"
    assert radio_key("https://icecast.skyrock.net/s/hit_us_aac_128k") == "hit-us"
    assert radio_key("https://other.test/s/natio_mp3_128k") is None
    assert radio_key("https://icecast.skyrock.net/other/natio_mp3_128k") is None


def test_official_cover_replaces_fallback_without_recording_change(db):
    entry = ensure_entry(db, "Artist", "Song")
    entry.temporary_cover_url = COVER
    assert public_metadata(entry)["cover_url"] == COVER
    assert public_metadata(entry)["recording_id"] is None
    entry.selected = {
        "provider": "itunes",
        "provider_id": "12",
        "cover_url": "https://example.test/official.jpg",
    }
    info = public_metadata(entry)
    assert info["cover_url"] == "https://example.test/official.jpg"
    assert info["recording_id"] == "itunes:12"
    entry.selected = {**entry.selected, "cover_url": "javascript:invalid"}
    assert public_metadata(entry)["cover_url"] == COVER


def test_fallback_is_filtered_in_station_results(db):
    station = Station(name="Skyrock test", slug="skyrock-test", stream_url="https://example.test")
    db.add(station)
    db.flush()
    entry = ensure_entry(db, "Artist", "Song")
    entry.temporary_cover_url = COVER
    db.add(
        Play(
            station_id=station.id,
            artist="Artist",
            title="Song",
            source="icy",
            raw_metadata="fixture",
            detected_at=NOW,
            last_seen_at=NOW,
        )
    )
    db.commit()
    assert now_playing(db, now=NOW)["items"][0]["track"]["metadata"]["cover_url"] == COVER
    exclusions = '[{"artist":"Artist","title":"Song"}]'
    assert now_playing(db, exclusions, NOW)["items"][0]["track"] is None
    artist, title = normalized_pair("Artist", "Song")
    db.add(
        GlobalExclusion(
            id=exclusion_id("Artist", "Song"),
            artist="Artist",
            title="Song",
            artist_normalized=artist,
            title_normalized=title,
        )
    )
    db.commit()
    assert now_playing(db, now=NOW)["items"][0]["track"] is None


def test_catalogue_contains_annexes_only_on_directory(db, client):
    db.add_all(
        [
            Station(name="Base", slug="base", stream_url="https://example.test/base"),
            Station(
                name="Skyrock Français",
                slug="skyrock-francais",
                stream_url="https://icecast.skyrock.net/s/francais_aac_128k",
            ),
        ]
    )
    db.commit()
    home = client.get("/").text
    directory = client.get("/radios").text
    from bs4 import BeautifulSoup

    assert len(BeautifulSoup(home, "html.parser").select(".station-card")) == 1
    assert len(BeautifulSoup(directory, "html.parser").select(".station-card")) == 2


def test_optional_cover_matches_song_and_survives_unavailable_service(monkeypatch):
    import asyncio
    import json

    import httpx

    from app.services import temporary_artwork as artwork

    async def run():
        stamp = datetime.now(UTC)
        delta = (stamp - NOW).total_seconds()
        payload = snapshot(delta - 10, delta + 100)
        payload["ts"] = int(stamp.timestamp())
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps(payload).encode()))

        monkeypatch.setattr(artwork, "_expires", 0)
        monkeypatch.setattr(artwork, "_cache", {})
        station = Station(stream_url="https://icecast.skyrock.net/s/francais_aac_128k")
        song = TrackMetadata("Artist", "Song", "fixture", "icy")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await artwork.cover_for(station, song, client) == COVER
            wrong = TrackMetadata("Artist", "Different Song", "fixture", "icy")
            assert await artwork.cover_for(station, wrong, client) is None
            assert len(requests) == 1
        monkeypatch.setattr(artwork, "_expires", 0)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503))
        ) as client:
            assert await artwork.cover_for(station, song, client) is None
        assert artwork._cache == {}

    asyncio.run(run())
