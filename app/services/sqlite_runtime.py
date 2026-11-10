"""Keep SQLite spill files on the writable data volume when configured."""

import os
from pathlib import Path


def prepare_temp_directory():
    directory = os.environ.get("SQLITE_TMPDIR")
    if directory:
        Path(directory).mkdir(mode=0o700, parents=True, exist_ok=True)
