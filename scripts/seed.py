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
        "enabled": False,
    },
    {
        "name": "Europe 2",
        "slug": "europe2",
        "stream_url": "http://europe2.lmn.fm/europe2.mp3",
        "enabled": False,
    },
    {
        "name": "Fun Radio",
        "slug": "fun-radio",
        "stream_url": "http://icecast.funradio.fr/fun-1-44-128?listen=radio-tracker",
        "enabled": False,
    },
    {
        "name": "Mouv'",
        "slug": "mouv",
        "stream_url": "https://icecast.radiofrance.fr/mouv-midfi.mp3",
        "enabled": False,
    },
]

with SessionLocal() as db:
    for values in STATIONS:
        station = db.scalar(select(Station).where(Station.slug == values["slug"]))
        if not station:
            db.add(Station(source_type="icy", **values))
        else:
            station.stream_url = values["stream_url"]
            station.enabled = values.get("enabled", True)
    db.commit()
