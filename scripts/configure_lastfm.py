"""Install the deployment secret without logging its value."""

import os
import re
import sys
from pathlib import Path


def configure(secret, target):
    key = Path(secret).read_text().strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", key):
        raise ValueError("Invalid Last.fm key format")
    path = Path(target)
    lines = [
        line
        for line in path.read_text().splitlines()
        if not re.match(r"\s*(?:export\s+)?LASTFM_API_KEY\s*=", line)
    ]
    temporary = path.with_name(path.name + ".lastfm.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("\n".join(lines) + "\nLASTFM_API_KEY=" + key + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


if __name__ == "__main__":
    configure(sys.argv[1], sys.argv[2])
