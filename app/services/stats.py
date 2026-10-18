from collections import OrderedDict
from threading import Lock
from time import monotonic

from sqlalchemy import func, select

from app.config import settings
from app.models import Play

_cache = OrderedDict()
_lock = Lock()


def cached_top(db, station_id, since, until, limit):
    key = (db.get_bind(), station_id, since, until, limit)
    # Bound cache size even when every caller supplies different timestamps.
    with _lock:
        now = monotonic()
        if key in _cache and _cache[key][0] > now:
            _cache.move_to_end(key)
            return _cache[key][1]
        query = select(Play.artist, Play.title, func.count(Play.id))
        if station_id:
            query = query.where(Play.station_id == station_id)
        if since:
            query = query.where(Play.detected_at >= since)
        if until:
            query = query.where(Play.detected_at < until)
        result = tuple(
            db.execute(
                query.group_by(Play.artist, Play.title)
                .order_by(func.count(Play.id).desc())
                .limit(limit)
            )
        )
        _cache[key] = (now + settings.stats_cache_seconds, result)
        _cache.move_to_end(key)
        while len(_cache) > 64:
            _cache.popitem(last=False)
        return result
