# Radio Tracker

Collecte les métadonnées des radios, conserve les passages et affiche des statistiques. Le navigateur écoute directement les flux officiels : le serveur ne stocke et ne redistribue aucun audio.

Le déploiement par défaut vise une petite machine Linux avec **500 Mo de RAM et 1 CPU**, pour un usage personnel et quelques visiteurs. SQLite remplace le service PostgreSQL. Les limites Docker sont de 160 Mio pour l’API et 128 Mio pour le collecteur ; ce sont des plafonds, pas des mesures ni une garantie pour un trafic illimité. Le système, Docker et le proxy HTTPS doivent aussi tenir dans la RAM disponible.

## Démarrage local

```bash
cp .env.example .env
# Générer une clé, puis la renseigner dans ADMIN_TOKEN dans .env :
python -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose up --build -d --wait
```

Ouvrir **http://127.0.0.1:8080**. Le port 8080 évite le conflit avec un autre serveur local présent sur 8000. Pour le changer, définir `PORT` dans `.env`. Par défaut le port est uniquement accessible depuis la machine locale.

L’API et le collecteur doivent tous deux être démarrés et sains :

```bash
docker compose ps
docker compose logs --tail 50 collector
docker stats --no-stream
```

Les données sont dans le volume `radio_data`. `docker compose down` les conserve. Ne pas utiliser `down --volumes` pour un simple redémarrage.

## Classements et recherche

L’accueil présente les 20 titres les plus diffusés sur toutes les radios ou sur une radio choisie, sur 24 heures, 7 jours (par défaut), 30 jours ou tout l’historique. Le classement utilise les passages réellement collectés et s’actualise toutes les 30 secondes lorsque la page est visible. Cliquer sur un titre ouvre ses passages dans la recherche avancée. Les pochettes sont des visuels provisoires générés en CSS, sans service externe d’images.

La recherche spécialisée est accessible sur **`/recherche`** depuis la navigation. Le lecteur radio est disponible sur les deux pages ; changer de page interrompt la lecture.

## Explorer la base en direct

La page affiche un explorateur sans bouton « Filtrer » : les champs de recherche se combinent et les résultats se mettent à jour après 350 ms de frappe. Les changements de listes et de dates s’appliquent immédiatement. L’actualisation automatique vérifie les nouveaux résultats toutes les 10 secondes lorsque la page est visible, avec un cache serveur de 3 secondes maximum. Les radios restent collectées à leur propre cadence.

- **Passages** : chaque diffusion, sa station, sa date, sa source, sa dernière observation et ses métadonnées brutes.
- **Musiques** : regroupement par artiste et titre, nombre de passages, nombre de stations et première/dernière diffusion.
- Filtres cumulables : station, recherche générale, artiste, titre, correspondance exacte, dates de diffusion, dates de dernière observation, source, artiste renseigné ou inconnu, suivi actif ou désactivé et texte brut. Le minimum de passages concerne la vue Musiques.
- Tri par date, artiste, titre, station ou fréquence selon la vue. Les recherches SQLite ignorent la casse et les accents et traitent les caractères `%` et `_` littéralement.
- Cliquer sur un artiste explore ses musiques ; cliquer sur une musique affiche ses passages ; « Voir les musiques » sous une station ouvre son catalogue musical.

Les résultats sont paginés (25, 50 ou 100 lignes), sans transférer la base complète ni recalculer un total global à chaque frappe. La navigation dans les pages conserve un instantané des identifiants pour éviter les doublons dus aux nouvelles insertions. L’actualisation automatique concerne la première page ; « Actualiser » revient à la première page avec les mêmes filtres. Les filtres sont conservés dans l’URL, donc partageables et rechargeables. Les dates sont saisies et affichées dans le fuseau du navigateur, puis transmises en UTC à l’API. La borne de fin est exclusive.

## Gérer les radios manuellement

Modifier **`config/stations.json`**, puis appliquer le fichier sans reconstruire l’image :

```bash
# Vérifier sans changer la base :
docker compose exec api python scripts/stations.py --check
# Appliquer les ajouts et modifications :
docker compose exec api python scripts/stations.py
```

Une radio ICY nécessite seulement ceci dans la liste JSON :

```json
{
  "slug": "ma-radio",
  "name": "Ma radio",
  "stream_url": "https://example.com/live.mp3",
  "enabled": true
}
```

Le **slug est l’identifiant stable** : le conserver pour renommer une radio ou changer son flux. `enabled: false` désactive sa collecte. Retirer une entrée du fichier désactive également sa collecte, tout en conservant la station et son historique en base. Le fichier est validé avant application et la mise à jour est transactionnelle. Les champs JSON/HTML sont illustrés par les radios existantes. Le collecteur prend en compte la modification au cycle suivant. Recharger la page après un ajout ou un changement de nom pour actualiser la liste des stations. La même synchronisation a lieu au démarrage.

Le dossier `config` est monté en lecture seule dans Docker et versionné avec le projet. Ne pas y placer de secrets. Les données collectées restent exclusivement dans le volume `radio_data`. En développement local, utiliser `python -m scripts.stations`.

### Radio sans ICY : source HTML

Une radio peut lire ses métadonnées depuis une autre URL que son flux audio. Ajouter par exemple cette entrée à `config/stations.json` (remplacer les URL et sélecteurs par ceux du site réel) :

```json
{
  "slug": "ma-radio",
  "name": "Ma radio",
  "stream_url": "https://radio.example/stream",
  "source_type": "html",
  "metadata_url": "https://radio.example/en-ce-moment",
  "html": {
    "adapter": "selectors",
    "title_selector": "#now-playing .title",
    "artist_selector": "#now-playing .artist"
  }
}
```

Le premier élément correspondant à chaque sélecteur CSS fournit son texte, espaces normalisés. `artist_selector` est facultatif ; s’il est renseigné mais absent de la page, aucun passage incomplet n’est enregistré. Choisir un sélecteur qui cible le morceau actuel et non une liste entière. Pour lire un attribut plutôt que le texte, ajouter `title_attribute` ou `artist_attribute`, par exemple `"title_selector": "meta[name='song']", "title_attribute": "content"`. Les sélecteurs sont validés avant toute modification de la base.

```bash
docker compose exec api python scripts/stations.py --check
docker compose exec api python scripts/stations.py --probe ma-radio
docker compose exec api python scripts/stations.py
```

`--probe` télécharge et affiche le morceau extrait **sans écrire dans la base**. Le JSON peut aussi utiliser une source `json` avec les chemins de champs des radios existantes. Si le titre n’apparaît qu’après exécution de JavaScript, chercher l’API JSON appelée par la page dans l’onglet Réseau du navigateur et configurer cette URL : le serveur n’embarque pas de navigateur.

En plus du plafond réseau de 512 Kio, une page analysée par sélecteurs est limitée à 20 000 éléments de structure/texte, contrôlés avant construction du DOM. Cela évite qu’une petite page contenant énormément de balises fasse dépasser la mémoire du collecteur.

Pour les formats nécessitant un traitement Python particulier, `app/services/html_sources.py` définit le contrat `HtmlAdapter` : `url(base_url, now)` construit l’URL et `parse(content, options)` renvoie `(artiste, titre)` ou `None`. Fun Radio implémente déjà ce contrat pour son URL datée et son attribut contenant du JSON. Pour ajouter un adaptateur, l’implémenter, l’enregistrer dans `HTML_ADAPTERS`, ajouter son nom à `HtmlConfig.adapter` et ses tests, puis reconstruire l’image. La configuration ne peut pas importer ni exécuter du code arbitraire. Chaque adaptateur utilise le téléchargement commun avec ses protections réseau, limites de taille et délai ; les analyses HTML sont sérialisées pour limiter les pics de mémoire.

## Protections

L’ancienne route d’administration `POST /api/stations` reste disponible pour compatibilité avec une clé `ADMIN_TOKEN`, envoyée dans `Authorization: Bearer …`, mais l’interface n’a plus de formulaire d’ajout. Le fichier de configuration est la référence au prochain démarrage ou à la prochaine synchronisation. Une clé vide désactive cette route. Garder `.env` privé ; il est exclu de Git et de l’image Docker.

Les flux doivent utiliser HTTP(S), sur le port 80 ou 443, sans identifiants dans l’URL. Le serveur refuse les IP locales, privées et réservées, y compris après résolution DNS ou redirection. Les connexions sont faites à l’IP publique vérifiée, avec le nom TLS et l’en-tête Host d’origine. Chaque origine a son propre pool de connexions.

Les réponses sont limitées à 512 Kio, les intervalles ICY à 64 Kio, les redirections à cinq et la durée totale à 12 secondes par collecte. La compression distante est refusée pour éviter les bombes de décompression. L’ajout HTTP est limité à 8 Kio et le nombre de radios à 50 par défaut. Une destination non conforme reste écoutable côté navigateur, mais sa collecte échoue et utilise un délai progressif avant nouvel essai.

## Collecte et consommation

Trois radios au maximum sont interrogées simultanément. Les sessions SQL sont fermées pendant les appels réseau. Le client HTTP est partagé et les écritures de présence sont espacées de 120 secondes ; `last_seen_at` peut donc avoir ce retard. Un titre différent crée toujours immédiatement un nouveau passage.

Les sources disponibles sont `icy`, `json` et `html`. Les radios livrées sont Skyrock, NRJ, Europe 2, Fun Radio et Mouv’. Leurs sources sont configurées dans `config/stations.json`. Les services tiers peuvent changer ou refuser leurs endpoints : les erreurs d’une station ne doivent pas arrêter les autres. Le seed synchronise la configuration manuelle au démarrage, sans supprimer les stations ou les passages existants.

Les classements disposent d’un cache de 30 secondes, limité à 64 entrées, et les passages d’un index global sur leur date. Les logs Docker sont limités à deux fichiers de 5 Mo par service. L’image de production ne contient ni outils de test, ni pilote PostgreSQL, ni les dépendances optionnelles d’Uvicorn.

Les passages dont la dernière observation date de plus de `RETENTION_DAYS=180` jours sont archivés dans `data/archives/*.jsonl.gz`, **avant** retrait de la base active. Un échec d’écriture conserve les lignes SQL. Les archives gardent toutes les colonnes des passages ; les stations restent dans la base. Les statistiques du site portent sur la base active. Définir `RETENTION_DAYS=0` désactive l’archivage automatique. Le travail quotidien est limité à 10 000 passages pour éviter un pic CPU sur une grande base. Les archives s’accumulent : prévoir sauvegarde et capacité disque.

## API

Documentation interactive : http://127.0.0.1:8080/docs

- `GET /api/explore?view=plays&artist=etoile&page_size=50` (mêmes filtres que l’interface ; `view=tracks` pour les musiques)
- `GET /api/stations`
- `POST /api/stations` avec authentification et `{"name":"Ma radio","stream_url":"https://…","enabled":true}`
- `GET /api/plays?station_id=1&since=2026-10-01T00:00:00Z&until=2026-10-02T00:00:00Z&limit=100`
- `GET /api/stats/top?station_id=1&limit=20`

Les dates de stockage sont en UTC ; l’explorateur les affiche dans le fuseau du navigateur. Les bornes temporelles de l’API acceptent des dates ISO 8601. Le lecteur peut fonctionner sans métadonnées.

## HTTPS sur un serveur

Avant d’envoyer la clé administrateur sur Internet, configurer un domaine pointant vers le serveur et `DOMAIN=radio.example.com` dans `.env`, puis :

```bash
docker compose -f compose.yaml -f compose.https.yaml up --build -d --wait
```

Caddy termine HTTPS et renouvelle le certificat. Les ports 80 et 443 doivent être disponibles et accessibles pour l’obtention du certificat. L’API reste liée à l’adresse locale. Le proxy dispose de 48 Mio ; le total des plafonds des conteneurs est alors 336 Mio et un CPU. Cette configuration est fournie pour le futur serveur ; aucun domaine ni déploiement distant n’est configuré automatiquement.

## Sauvegarde et passage depuis PostgreSQL

L’ancien volume `postgres_data` n’est pas utilisé par défaut et doit être conservé jusqu’à validation de la migration. Ne jamais effacer un ancien volume pour faire fonctionner SQLite.

`scripts/transfer.py` exporte stations et passages dans un fichier JSONL gzip avec comptes de contrôle. L’import conserve les identifiants et dates, refuse une base non vide et annule la transaction si l’export est incomplet.

Pour sauvegarder la base active :

```bash
docker compose exec api python scripts/transfer.py export /app/data/backup.jsonl.gz
docker compose cp api:/app/data/backup.jsonl.gz ./backup.jsonl.gz
```

Choisir un nom de fichier différent à chaque sauvegarde. Sauvegarder également le répertoire d’archives et `.env` dans un lieu privé. Pour restaurer dans un **nouveau volume vide**, exécuter les migrations, importer le fichier, puis lancer le seed et les services. Ne pas copier uniquement un fichier SQLite actif en oubliant son journal WAL.

Pour exporter une ancienne installation PostgreSQL, utiliser `scripts/transfer.py export …` dans l’ancien conteneur (qui possède son pilote), puis copier le fichier hors du conteneur. Dans le nouveau conteneur, avant tout seed :

```bash
alembic upgrade head
python scripts/transfer.py import /chemin/backup.jsonl.gz
python scripts/seed.py
```

## Développement et CI

```bash
python -m venv .venv
# Activer le venv adapté à votre shell, puis :
pip install --require-hashes -r requirements-dev.lock
pip install --no-deps -e .
mkdir -p data
alembic upgrade head
python scripts/seed.py
uvicorn app.main:app --reload --port 8080
# Dans un autre terminal :
python -m app.worker
```

Tests avec Python 3.12 dans Docker :

```bash
docker build --target test -t track-radio:test .
docker run --rm track-radio:test
```

La CI vérifie Ruff, le formatage, les tests, les dépendances vulnérables et les migrations SQLite. La construction et la publication GHCR dépendent du succès de ces contrôles, avec un démarrage HTTP réel de l’image.

## Déploiement automatique sur le VPS

Après un push sur la branche par défaut (actuellement `master`), le job `deploy`
attend la publication et installe l’image identifiée par son digest sur
`ubuntu@129.151.250.229`. Aucun clonage ni build sur le VPS. Le site est servi
sur le port 80, sans modifier le service existant sur 8080. L’administration HTTP
est désactivée tant que ce déploiement utilise HTTP sans domaine.

Prérequis : Docker avec Compose v2 (`--wait`), `curl`, `flock`, port 80 libre et
accessible dans les pare-feu du VPS et de l’hébergeur. L’utilisateur `ubuntu`
doit pouvoir exécuter `docker info` sans sudo. Vérifier aussi l’architecture :
l’image de CI cible actuellement Linux amd64, pas ARM64.

Secrets GitHub Actions requis : `VPS_SSH_KEY` (clé privée dédiée autorisée sur
le VPS) et `VPS_KNOWN_HOSTS` (entrée SSH du serveur vérifiée). Depuis PowerShell,
si la connexion SSH habituelle a déjà enregistré et vérifié cette identité :

```powershell
ssh-keygen -F 129.151.250.229 -f "$HOME\.ssh\known_hosts" |
  gh secret set VPS_KNOWN_HOSTS --repo Hicha-m/track-radio
```

Ne pas utiliser une collecte `ssh-keyscan` non vérifiée pour établir la confiance.
La CD utilise le `GITHUB_TOKEN` temporaire pour télécharger l’image GHCR, puis
déconnecte le registre. Aucun token personnel GHCR nécessaire.

Le premier démarrage crée une base vide. Les fichiers runtime et le `.env`
privé sont installés dans `~/track-radio`, les données dans le volume Docker
`track-radio_radio_data`. Les configurations de radios suivent le dépôt.
Ne jamais supprimer ce volume pour une mise à jour.

Avant les déploiements suivants, un export cohérent de la base active et la
configuration précédente sont conservés dans `~/track-radio/backups/`. Les
archives historiques restent dans le volume et ne font pas partie de cet export.
Prévoir leur sauvegarde externe ainsi qu’une gestion de l’espace disque : les
sauvegardes et images ne sont pas automatiquement purgées.

Le collecteur est arrêté avant les migrations, puis Compose attend que les deux
services soient sains. Une courte interruption est possible. En cas d’échec,
la CI affiche les logs et conserve les sauvegardes ; elle ne restaure pas
automatiquement une ancienne image avec une base potentiellement migrée.
Une restauration demande un volume vide et la procédure d’import décrite plus
haut. Après succès, les redémarrages manuels utilisent :

```bash
cd ~/track-radio
docker compose up -d --wait
docker compose ps
```

Les fichiers `requirements.lock` et `requirements-dev.lock` verrouillent les dépendances transitives avec leurs empreintes. Pour les actualiser après modification de `pyproject.toml`, utiliser `uv pip compile pyproject.toml --universal --generate-hashes --python-version 3.12 -o requirements.lock` et la même commande avec `--extra dev -o requirements-dev.lock`. Relancer tests et audit avant publication.

## Santé des conteneurs

`healthy` correspond à un contrôle périodique indépendant de l’affichage du site. Pendant un démarrage lent, ce contrôle peut échouer alors que le serveur devient ensuite disponible. L’API dispose d’un délai initial de 120 secondes et le contrôle d’un plafond de 15 secondes ; il utilise directement `127.0.0.1`, sans proxy ni résolution de nom. Le contrôle du collecteur vérifie l’âge de son fichier de progression avec la bibliothèque standard, sans charger l’application et ses dépendances. Pour diagnostiquer une erreur persistante : `docker inspect --format '{{json .State.Health}}' track-radio-api-1` et `docker compose logs --tail 50 api collector`.
