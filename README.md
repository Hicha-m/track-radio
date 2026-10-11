# Radio Tracker

Service léger qui détecte les changements de métadonnées des radios, conserve chaque passage et expose des statistiques filtrables. Aucun flux audio n'est enregistré.

## Démarrage

```bash
cp .env.example .env
docker compose up --build -d
```

Ouvrir <http://localhost:8000> (tableau de bord) ou <http://localhost:8000/docs> (API). Les tables sont migrées et les radios initiales ajoutées automatiquement. Le tableau de bord permet d'écouter un flux, de filtrer par radio et période, et d'ajouter une station sans redémarrer les conteneurs.

```bash
docker compose logs -f collector
docker compose down             # conserve PostgreSQL
docker compose down --volumes   # efface aussi les données
```

## Fonctionnement

Le collecteur demande `Icy-MetaData: 1`, lit seulement jusqu'au premier bloc de métadonnées, puis ferme la connexion. Si le titre n'a pas changé, il met à jour `last_seen_at`; sinon il crée un passage avec la radio, l'artiste, le titre, l'heure et la source.

Skyrock et plusieurs de ses webradios sont activées au départ. Lors du contrôle du 11 octobre 2026, les flux de NRJ, Europe 2, Fun Radio et Mouv' ne fournissaient pas de titre ICY. Ces radios restent préconfigurées mais désactivées pour ne pas lire de l'audio inutilement. Elles restent écoutables depuis le site. Radio France propose une API officielle avec inscription pour Mouv'. On peut passer une station en source `json` dès que son endpoint autorisé est renseigné. L'architecture prévoit deux sources :

- `icy` : métadonnées incluses dans le flux ;
- `json` : endpoint HTTP, avec `json_artist_path` et `json_title_path` en notation pointée.

Les URLs publiques changent parfois. Vérifier les logs après le premier lancement et ne laisser activées que les radios dont l'utilisation respecte les conditions de l'éditeur.

## API et filtres

- `GET /api/stations`
- `POST /api/stations` avec `{"name":"Ma radio","stream_url":"https://…","enabled":true}`
- `GET /api/plays?station_id=1&since=2026-10-01T00:00:00Z&until=2026-10-02T00:00:00Z`
- `GET /api/stats/top?station_id=1&since=2026-10-01T18:00:00Z&until=2026-10-02T00:00:00Z&limit=20`

Les dates sont stockées en UTC. Le client choisit son fuseau lors de l'affichage.

## Ajouter une radio

Cliquer sur **Ajouter une radio** dans le tableau de bord, saisir son nom et l'URL directe du flux. Le slug est généré automatiquement et le worker la prend en charge au cycle suivant. Si le flux ne contient pas de métadonnées ICY, décocher le suivi : la radio restera disponible dans le lecteur sans provoquer de sondages inutiles.

Les stations livrées avec le projet restent dans `scripts/seed.py`. Le seed est idempotent : il ajoute les stations manquantes et met à jour leurs URLs sans effacer l'historique.

## Développement local

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
alembic upgrade head
python scripts/seed.py
python scripts/check_streams.py
uvicorn app.main:app --reload
# autre terminal
python -m app.worker
```

Par défaut, hors Docker, l'application utilise SQLite (`radio.db`) pour rendre le développement immédiat. Docker utilise PostgreSQL 16.

## Suite prévue

1. Stabiliser les adaptateurs officiels/API JSON station par station.
2. Enrichir artiste/titre via MusicBrainz (cache local et limitation de débit).
3. Produire des playlists Spotify/Deezer avec OAuth explicite.
4. Ajouter en dernier recours une reconnaissance d'empreinte sur un court extrait. AudD/ACRCloud sont adaptés à un service ; ShazamIO est non officiel. Cette étape doit être optionnelle car elle augmente CPU, bande passante, coût et contraintes juridiques.

## CI/CD

GitHub Actions exécute Ruff, les tests avec couverture et construit l'image Docker à chaque push/PR. Hors pull request, l'image est publiée dans GitHub Container Registry (`ghcr.io/<proprietaire>/<depot>`). Le déploiement sur un VPS n'est volontairement pas activé tant que la cible et ses secrets ne sont pas choisis.

