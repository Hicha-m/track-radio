from sqlalchemy import select

from app.database import SessionLocal
from app.models import Station

STATIONS = [
    {
        "name": "Skyrock",
        "slug": "skyrock",
        "stream_url": "https://icecast.skyrock.net/s/natio_mp3_128k",
    },
    {
        "name": "NRJ",
        "slug": "nrj",
        "stream_url": "https://scdn.nrjaudio.fm/adwz2/fr/30001/mp3_128.mp3?origine=radio-tracker",
        "source_type": "json",
        "metadata_url": "https://players.nrjaudio.fm/wr_api/live/fr?act=get_plist&id_wr=158&fmt=json&sg_memory=",
        "json_artist_path": "itms.0.art",
        "json_title_path": "itms.0.tit",
    },
    {
        "name": "Europe 2",
        "slug": "europe2",
        "stream_url": "http://europe2.lmn.fm/europe2.mp3",
        "source_type": "json",
        "metadata_url": "https://www.europe2.fr/cetait-quoi-ce-titre/history?idplayer=1&player_id=1&limit=8&ts=&date_diffusion=",
        "json_artist_path": "nowplaying.0.artist",
        "json_title_path": "nowplaying.0.title",
    },
    {
        "name": "Fun Radio",
        "slug": "fun-radio",
        "stream_url": "http://icecast.funradio.fr/fun-1-44-128?listen=radio-tracker",
        "source_type": "html",
        "metadata_url": "https://www.funradio.fr/quel-est-ce-titre",
    },
    {
        "name": "Mouv'",
        "slug": "mouv",
        "stream_url": "https://icecast.radiofrance.fr/mouv-midfi.mp3",
        "source_type": "json",
        "metadata_url": "https://api.radiofrance.fr/livemeta/live/6/transistor_mouv_player",
        "json_title_path": "now.secondLine",
    },
]

with SessionLocal() as db:
    for values in STATIONS:
        station = db.scalar(select(Station).where(Station.slug == values["slug"]))
        if not station:
            db.add(Station(**values))
        else:
            station.stream_url = values["stream_url"]
            station.enabled = values.get("enabled", True)
            station.source_type = values.get("source_type", "icy")
            station.metadata_url = values.get("metadata_url")
            station.json_artist_path = values.get("json_artist_path")
            station.json_title_path = values.get("json_title_path")
    db.commit()
