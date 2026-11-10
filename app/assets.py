"""One ordered application download, while retaining individually inspectable sources."""

from pathlib import Path

SCRIPTS = (
    "i18n",
    "theme",
    "cache",
    "dialogs",
    "filters",
    "export",
    "metrics",
    "admin-session",
    "youtube-admin",
    "collection-monitor",
    "admin",
    "platforms",
    "youtube",
    "catalog",
    "search",
    "player",
    "stations",
    "playlists",
    "charts",
    "dashboard",
    "mobile-navigation",
    "navigation",
)


def application_bundle():
    return b"\n;\n".join((Path("app/static") / f"{name}.js").read_bytes() for name in SCRIPTS)
