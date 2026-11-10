"""SQLite counters cover ORM, imports, corrections, bulk SQL and archive deletions."""


def expressions(prefix):
    hour = f"strftime('%Y-%m-%d %H:00:00.000000', {prefix}.detected_at)"
    key = (
        f"json_array({hour}, {prefix}.station_id, {prefix}.artist, "
        f"{prefix}.title, {prefix}.track_id)"
    )
    return hour, key


def install(connection):
    if connection.dialect.name != "sqlite":
        return
    hour, key = expressions("NEW")
    _, previous = expressions("OLD")
    add = f"""
        INSERT INTO play_counts (id, hour, station_id, track_id, artist, title, plays)
        VALUES ({key}, {hour}, NEW.station_id, NEW.track_id, NEW.artist, NEW.title, 1)
        ON CONFLICT(id) DO UPDATE SET plays = plays + 1;
    """
    remove = f"""
        UPDATE play_counts SET plays = plays - 1 WHERE id = {previous};
        DELETE FROM play_counts WHERE id = {previous} AND plays <= 0;
    """
    for name, operation, body in (
        ("insert", "INSERT", add),
        ("delete", "DELETE", remove),
        ("update", "UPDATE OF station_id, artist, title, track_id, detected_at", remove + add),
    ):
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS plays_count_{name} AFTER {operation} ON plays "
            f"BEGIN {body} END"
        )


def backfill(connection):
    if connection.dialect.name != "sqlite":
        return
    hour, key = expressions("plays")
    connection.exec_driver_sql(
        "INSERT INTO play_counts (id, hour, station_id, track_id, artist, title, plays) "
        f"SELECT {key}, {hour}, station_id, track_id, artist, title, count(*) FROM plays "
        "GROUP BY 1, 2, 3, 4, 5, 6"
    )
