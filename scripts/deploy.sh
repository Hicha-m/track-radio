#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Executed on the VPS, from a unique upload directory.
image=${1:?Image digest required}
release=$PWD
mkdir -p "$HOME/track-radio/backups"
cd "$HOME/track-radio"
exec 9>.deploy.lock
flock -n 9 || { echo 'Another deployment is running'; exit 1; }
export RADIO_IMAGE="$image"
compose() { docker compose -f compose.yaml "$@"; }

docker pull "$image"
# Preserve the previous configuration and export using the running image,
# before new migrations or station synchronization can change the database.
if [[ -f compose.yaml ]]; then
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  archive="backups/$stamp"
  mkdir "$archive"
  cp compose.yaml .env image.env "$archive/"
  cp -r config "$archive/"
  if [[ -n $(compose ps --status running -q api) ]]; then
    compose exec -T api python scripts/transfer.py export "/app/data/deploy-$stamp.jsonl.gz"
    compose cp "api:/app/data/deploy-$stamp.jsonl.gz" "$archive/database.jsonl.gz"
  else
    echo 'Existing API is stopped: refusing to deploy without a backup.' >&2
    exit 1
  fi
  compose stop collector
fi

cp "$release/compose.production.yaml" compose.yaml
mkdir -p config
cp "$release/config/stations.json" config/stations.json
# The container runs as uid 10001; only public station configuration is readable.
chmod 755 config
chmod 644 config/stations.json
if [[ ! -f .env ]]; then
  cp "$release/.env.example" .env
fi
# Optional credentials arrive from GitHub Secrets, never from the source tree.
if [[ -f "$release/spotify.env" ]]; then
  if ! [[ $(wc -l < "$release/spotify.env") -eq 2 ]] ||
      ! grep -Eq '^SPOTIFY_CLIENT_ID=[a-zA-Z0-9]+$' "$release/spotify.env" ||
      ! grep -Eq '^SPOTIFY_CLIENT_SECRET=[a-zA-Z0-9]+$' "$release/spotify.env"; then
    echo 'Invalid Spotify credential file' >&2
    exit 1
  fi
  sed -i '/^SPOTIFY_CLIENT_ID=/d; /^SPOTIFY_CLIENT_SECRET=/d' .env
  printf '\n' >> .env
  cat "$release/spotify.env" >> .env
  rm -f "$release/spotify.env"
fi
printf 'RADIO_IMAGE=%s\n' "$image" > image.env
# Compose reads the pinned image on future manual restarts, too.
sed -i '/^RADIO_IMAGE=/d' .env
printf '\nRADIO_IMAGE=%s\n' "$image" >> .env
chmod 600 .env image.env
if ! compose up -d --wait --wait-timeout 420; then
  compose logs --tail 80 >&2
  echo 'Deployment failed. Backups retained; no automatic database downgrade.' >&2
  exit 1
fi
curl --fail --silent --show-error --retry 5 --retry-delay 3 http://127.0.0.1/health
echo 'Deployment healthy'
