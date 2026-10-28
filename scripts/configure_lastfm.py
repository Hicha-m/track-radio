"""Install the deployment secret without logging its value."""

import os
import re
import sys
from pathlib import Path


def configure(secret, target, name="LASTFM_API_KEY"):
    if name not in {"LASTFM_API_KEY", "YOUTUBE_API_KEY"}:
        raise ValueError("Unsupported configuration name")
    key = Path(secret).read_text().strip()
    pattern = r"[a-fA-F0-9]{32}" if name == "LASTFM_API_KEY" else r"[A-Za-z0-9_-]{20,128}"
    if not re.fullmatch(pattern, key):
        raise ValueError("Invalid provider key format")
    path = Path(target)
    lines = [
        line
        for line in path.read_text().splitlines()
        if not re.match(r"\s*(?:export\s+)?" + re.escape(name) + r"\s*=", line)
    ]
    temporary = path.with_name(path.name + ".lastfm.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("\n".join(lines) + "\n" + name + "=" + key + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


if __name__ == "__main__":
    configure(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "LASTFM_API_KEY")
