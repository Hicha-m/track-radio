from collections import OrderedDict
from threading import Lock
from time import monotonic

from sqlalchemy import func, select

from app.config import settings
from app.models import Play
from app.services.exclusions import exclusion_conditions, global_revision

_cache = OrderedDict()
_lock = Lock()


def cached_top(db, station_id, since, until, limit, exclusions=""):
    key = (db.get_bind(), global_revision(db), station_id, since, until, limit, exclusions)
    # Bound cache size even when every caller supplies different timestamps.
    with _lock:
        now = monotonic()
        if key in _cache and _cache[key][0] > now:
            _cache.move_to_end(key)
            return _cache[key][1]
        query = select(Play.artist, Play.title, func.count(Play.id)).where(
            *exclusion_conditions(exclusions, db.get_bind().dialect.name == "sqlite")
        )
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
