import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import GlobalExclusion, MediaDiscovery, ServiceQuota, YouTubeMatch
from app.services import media_discovery as jobs
from app.services import musicbrainz_media as media
from app.services.catalog import ensure_entry
from app.services.exclusions import exclusion_id

RECORDING = "11111111-1111-4111-8111-111111111111"
RELEASE = "22222222-2222-4222-8222-222222222222"
VIDEO = {
    "id": "aaaaaaaaaaa",
    "title": "Artist Song (Official Audio)",
    "channel": "Artist",
    "source": "musicbrainz_oembed",
}
COVER = {
    "url": "https://coverartarchive.org/image.jpg",
    "source": "coverartarchive",
    "release_id": RELEASE,
}
RESULT = {"status": "matched", "recordings": [RECORDING], "video": VIDEO, "cover": COVER}


def test_lookup_validates_recording_isrc_album_and_oembed(monkeypatch):
    calls = []
    recording = {
        "id": RECORDING,
        "title": "Song",
        "artist-credit": [{"name": "Artist"}],
        "length": 180000,
    }

    async def fetch(client, url, params=None, **kwargs):
        calls.append((url, params))
        if url.endswith("recording/"):
            return {"count": 1, "recordings": [recording]}
        if "recording/" in url:
            return {
                **recording,
                "isrcs": ["FRTEST1234567"],
                "relations": [
                    {"type": "free streaming", "url": {"resource": "https://youtu.be/aaaaaaaaaaa"}},
                    {"type": "free streaming", "url": {"resource": "https://127.0.0.1/private"}},
                ],
                "releases": [
                    {"id": RELEASE, "title": "Album", "status": "Official"},
                ],
            }
        return {"images": [{"front": True, "approved": True, "thumbnails": {"500": COVER["url"]}}]}

    async def verify(artist, title, url):
        assert (artist, title, url) == ("Artist", "Song", "https://youtu.be/aaaaaaaaaaa")
        return {**VIDEO, "score": 7}

    monkeypatch.setattr(media, "fetch_json", fetch)
    monkeypatch.setattr(media, "verify_link", verify)

    # The real limiter is exercised separately; this test avoids a wall-clock sleep.
    async def sleep(delay):
        assert delay <= 1.1

    monkeypatch.setattr(media.asyncio, "sleep", sleep)
    result = asyncio.run(
        media.Lookup(None).find(
            "Artist",
            "Song",
            {"album": "Album", "isrc": "FRTEST1234567", "duration": 180},
            want_cover=True,
            want_video=True,
        )
    )
    assert result == RESULT
    assert 'isrc:"FRTEST1234567"' in calls[0][1]["query"]
    assert calls[-1][0] == "https://coverartarchive.org/release/" + RELEASE


def test_musicbrainz_rate_limit_is_respected(monkeypatch):
    times, delays = iter([10, 10, 10.2, 10.2]), []
    monkeypatch.setattr(media, "monotonic", lambda: next(times))

    async def sleep(delay):
        delays.append(delay)

    async def fetch(*args, **kwargs):
        return {}

    monkeypatch.setattr(media.asyncio, "sleep", sleep)
    monkeypatch.setattr(media, "fetch_json", fetch)

    async def run():
        lookup = media.Lookup(None)
        await lookup.musicbrainz("recording/", {})
        await lookup.musicbrainz("recording/", {})

    asyncio.run(run())
    assert delays[0] == 0
    assert 0.89 < delays[1] < 0.91


def test_lookup_rejects_wrong_versions_and_ambiguous_results(monkeypatch):
    async def fetch(*args, **kwargs):
        return {
            "count": 2,
            "recordings": [
                {"id": RECORDING, "title": "Song (Live)", "artist-credit": [{"name": "Artist"}]},
                {
                    "id": RELEASE,
                    "title": "Song",
                    "disambiguation": "DJ mix",
                    "artist-credit": [{"name": "Artist"}],
                },
            ],
        }

    monkeypatch.setattr(media, "fetch_json", fetch)
    result = asyncio.run(
        media.Lookup(None).find("Artist", "Song", {}, want_cover=True, want_video=True)
    )
    assert not result["video"] and not result["cover"]
    assert result["status"] == "not_found"


def factory(db):
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)


def test_durable_queue_deduplicates_claims_and_reuses_validated_links(db):
    entry = ensure_entry(db, "Artist", "Song")
    jobs.queue(db, entry.id)
    db.commit()
    assert len(list(db.scalars(select(MediaDiscovery)))) == 1
    snapshot = jobs.claim(factory(db))
    assert snapshot and jobs.claim(factory(db)) is None
    jobs.finish(factory(db), snapshot, RESULT)
    db.expire_all()
    assert db.get(YouTubeMatch, entry.id).selected == VIDEO
    assert db.get(YouTubeMatch, entry.id).manual is False
    assert db.get(ServiceQuota, "youtube") is None
    assert db.get(type(entry), entry.id).temporary_cover_url == COVER["url"]
    assert jobs.claim(factory(db)) is None


def test_manual_choices_and_provider_cover_arriving_during_io_win(db):
    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    snapshot = jobs.claim(factory(db))
    entry.temporary_cover_url = "https://radio.example/cover.jpg"
    entry.revision += 1
    db.add(
        YouTubeMatch(
            track_id=entry.id,
            artist=entry.artist,
            title=entry.title,
            status="rejected",
            manual=True,
            revision=1,
            requested_at=datetime.now(UTC),
        )
    )
    db.commit()
    jobs.finish(factory(db), snapshot, RESULT)
    db.expire_all()
    assert entry.temporary_cover_url == "https://radio.example/cover.jpg"
    assert db.get(YouTubeMatch, entry.id).selected is None
    assert db.get(YouTubeMatch, entry.id).manual


def test_exclusions_are_rechecked_before_publication(db):
    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    snapshot = jobs.claim(factory(db))
    db.add(
        GlobalExclusion(
            id=exclusion_id("Artist", "Song"),
            artist="Artist",
            title="Song",
            artist_normalized="artist",
            title_normalized="song",
        )
    )
    db.commit()
    jobs.finish(factory(db), snapshot, RESULT)
    db.expire_all()
    assert entry.temporary_cover_url is None
    assert db.get(YouTubeMatch, entry.id) is None
    assert db.get(MediaDiscovery, entry.id).status == "hidden"
    assert jobs.claim(factory(db)) is None


def test_errors_have_durable_backoff_and_catalogue_backfill(db):
    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    db.query(MediaDiscovery).delete()
    db.commit()
    cursor = jobs.backfill(factory(db), "")
    assert cursor == entry.id
    assert jobs.backfill(factory(db), cursor) is None

    class Broken:
        async def find(self, *args, **kwargs):
            raise HTTPException(503)

    assert asyncio.run(jobs.worker_round(factory(db), Broken()))
    db.expire_all()
    task = db.get(MediaDiscovery, entry.id)
    assert task.status == "error" and task.lease_until is None
    assert task.retry_at > datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=14)
    assert jobs.claim(factory(db)) is None


def test_completed_assets_do_not_trigger_external_requests(db):
    entry = ensure_entry(db, "Artist", "Song")
    entry.temporary_cover_url = COVER["url"]
    db.add(
        YouTubeMatch(
            track_id=entry.id,
            artist=entry.artist,
            title=entry.title,
            status="matched",
            selected=VIDEO,
            requested_at=datetime.now(UTC),
        )
    )
    db.commit()
    assert asyncio.run(jobs.worker_round(factory(db), SimpleNamespace()))
    db.expire_all()
    assert db.get(MediaDiscovery, entry.id).status == "ready"


def test_music_video_relation_and_streaming_uuid_are_followed(monkeypatch):
    requests = []
    recording = {"id": RECORDING, "title": "Song", "artist-credit": [{"name": "Artist"}]}

    class Linked(media.Lookup):
        async def musicbrainz(self, path, params):
            requests.append(path)
            if path == "recording/":
                return {"count": 1, "recordings": [recording]}
            if path.endswith(RECORDING):
                return {
                    **recording,
                    "relations": [
                        {
                            "type-id": "ce3de655-7451-44d1-9224-87eb948c205d",
                            "recording": {"id": RELEASE},
                        }
                    ],
                }
            return {
                **recording,
                "relations": [
                    {
                        "type-id": "7e41ef12-a124-4324-afdb-fdbae687a89c",
                        "type": "streaming music",
                        "url": {"resource": "https://youtu.be/aaaaaaaaaaa"},
                    }
                ],
            }

    async def verify(*args):
        return VIDEO

    monkeypatch.setattr(media, "verify_link", verify)
    result = asyncio.run(Linked(None).find("Artist", "Song", {}, want_video=True, want_cover=False))
    assert requests == ["recording/", "recording/" + RECORDING, "recording/" + RELEASE]
    assert result["video"] == VIDEO


def test_expired_lease_is_reclaimed_and_old_result_is_discarded(db):
    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    old = jobs.claim(factory(db))
    task = db.get(MediaDiscovery, entry.id)
    task.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    fresh = jobs.claim(factory(db))
    assert fresh
    jobs.finish(factory(db), old, RESULT)
    db.expire_all()
    assert db.get(YouTubeMatch, entry.id) is None
    jobs.finish(factory(db), fresh, RESULT)
    db.expire_all()
    assert db.get(YouTubeMatch, entry.id).selected == VIDEO


def test_active_youtube_api_request_is_not_replaced(db):
    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    snapshot = jobs.claim(factory(db))
    db.add(
        YouTubeMatch(
            track_id=entry.id,
            artist=entry.artist,
            title=entry.title,
            status="pending",
            requested_at=datetime.now(UTC),
            lease_until=datetime.now(UTC) + timedelta(minutes=2),
        )
    )
    db.commit()
    jobs.finish(factory(db), snapshot, RESULT)
    db.expire_all()
    assert db.get(YouTubeMatch, entry.id).selected is None


def test_different_version_or_isrc_at_lookup_is_not_used(monkeypatch):
    recording = {"id": RECORDING, "title": "Song", "artist-credit": [{"name": "Artist"}]}

    class WrongIsrc(media.Lookup):
        async def musicbrainz(self, path, params):
            return (
                {"count": 1, "recordings": [recording]}
                if path == "recording/"
                else {
                    **recording,
                    "isrcs": ["WRONG"],
                    "relations": [
                        {
                            "type": "free streaming",
                            "url": {"resource": "https://youtu.be/aaaaaaaaaaa"},
                        }
                    ],
                }
            )

    result = asyncio.run(
        WrongIsrc(None).find("Artist", "Song", {"isrc": "FRTEST"}, want_video=True, want_cover=True)
    )
    assert not result["recordings"] and result["video"] is None and result["cover"] is None


def test_two_equally_plausible_videos_are_not_selected(monkeypatch):
    recording = {"id": RECORDING, "title": "Song", "artist-credit": [{"name": "Artist"}]}

    class Ambiguous(media.Lookup):
        async def musicbrainz(self, path, params):
            if path == "recording/":
                return {"count": 1, "recordings": [recording]}
            return {
                **recording,
                "relations": [
                    {"type": "free streaming", "url": {"resource": "https://youtu.be/" + key}}
                    for key in ["aaaaaaaaaaa", "bbbbbbbbbbb"]
                ],
            }

    async def verify(artist, title, url):
        return {**VIDEO, "id": url.rsplit("/", 1)[1]}

    monkeypatch.setattr(media, "verify_link", verify)
    result = asyncio.run(
        Ambiguous(None).find("Artist", "Song", {}, want_video=True, want_cover=False)
    )
    assert result["status"] == "ambiguous"
    assert result["video"] is None


def test_postdeploy_flags_require_worker_and_a_completed_attempt(db):
    from app.services.supervision import report
    from scripts.check_media_discovery import check

    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    assert check(factory(db))["worker_recent"] is False
    report(factory(db), "discovery", "started", total=1)
    flags = check(factory(db))
    assert flags["worker_recent"] and flags["queue_has_visible_tasks"]
    assert not flags["completed_attempt_seen"]
    snapshot = jobs.claim(factory(db))
    jobs.finish(factory(db), snapshot, {"status": "not_found"})
    report(factory(db), "discovery", "completed", completed=1)
    assert check(factory(db))["completed_attempt_seen"]
    assert db.get(MediaDiscovery, entry.id) is not None


def test_new_discovery_strategy_retries_old_results_once_without_waiting_a_day(db):
    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    old = db.get(MediaDiscovery, entry.id)
    old.status = "not_found"
    old.checked_at = datetime.now(UTC)
    old.retry_at = datetime.now(UTC) + timedelta(days=1)
    db.commit()
    assert jobs.claim(factory(db)) is None
    snapshot = jobs.claim(factory(db), "free-media-v1")
    assert snapshot is not None
    jobs.finish(factory(db), snapshot, {"status": "not_found", "strategy": "free-media-v1"})
    assert jobs.claim(factory(db), "free-media-v1") is None
    db.expire_all()
    assert db.get(MediaDiscovery, entry.id).evidence["strategy"] == "free-media-v1"


def test_deployment_observes_the_new_strategy_in_the_durable_queue(db):
    from app.services.supervision import report
    from scripts.check_media_discovery import check

    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    snapshot = jobs.claim(factory(db), "free-media-v1")
    jobs.finish(
        factory(db),
        snapshot,
        {
            "status": "matched",
            "strategy": "free-media-v1",
            "video": VIDEO,
            "cover": COVER,
            "sources": {"youtube": "matched", "cover": "matched"},
        },
    )
    report(factory(db), "discovery", "completed", completed=1)
    assert check(factory(db))["free_strategy_attempt_seen"]
    db.expire_all()
    assert db.get(MediaDiscovery, entry.id).evidence["sources"] == {
        "youtube": "matched",
        "cover": "matched",
    }


def test_deployment_flags_identify_actual_free_results_and_respect_exclusions(db):
    from scripts.check_media_discovery import check

    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    snapshot = jobs.claim(factory(db), "free-media-v1")
    result = {
        **RESULT,
        "strategy": "free-media-v1",
        "video": {**VIDEO, "source": "yt_dlp_oembed"},
        "cover": {**COVER, "source": "ddgs_spotify"},
    }
    jobs.finish(factory(db), snapshot, result)
    flags = check(factory(db))
    assert flags["yt_dlp_link_seen"] and flags["ddgs_cover_seen"]
    db.add(
        GlobalExclusion(
            id=exclusion_id("Artist", "Song"),
            artist="Artist",
            title="Song",
            artist_normalized="artist",
            title_normalized="song",
        )
    )
    db.commit()
    flags = check(factory(db))
    assert not flags["yt_dlp_link_seen"] and not flags["ddgs_cover_seen"]
    assert db.get(YouTubeMatch, entry.id) is not None
