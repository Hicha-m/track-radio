# Revue BMad — fidélité à l’existant

Date : 9 novembre 2026. Périmètre : architecture versionnée, lecture du code et des configurations ; aucune nouvelle mesure de production, aucune migration ou modification applicative. Le document source n’est pas modifié.

## Verdict

Le document représente correctement les composants et les mécanismes examinés. Une incohérence de statut et deux imprécisions de documentation restent à corriger. Aucun défaut critique démontré par cette revue.

## R1 — Moyen — D02 reste présenté comme entièrement à valider

**Preuves :** `ARCHITECTURE.md:285` indique « objectifs à valider au point 2 », tandis que `ARCHITECTURE.md:300` annonce l’objectif admin confirmé et atteint sur deux échantillons. `docs/performance-loading.md:96` consigne l’arbitrage confirmé et `:120–125` le relevé après index. `app/admin_api.py:135–140` confirme l’agrégation unique et `migrations/versions/0016_catalog_review_cover.py:11–18` l’index réellement implémenté.

**Impact :** un prochain développeur pourrait traiter l’objectif admin et la correction retenue comme des décisions encore ouvertes.

**Action :** actualiser D02 en distinguant l’objectif admin déjà arbitré du diagnostic global toujours ouvert. Correction documentaire, sans nouvelle décision technique.

## R2 — Faible — La table des workflows ne couvre pas le profilage de chargement

**Preuves :** `ARCHITECTURE.md:64` décrit CI, déploiement et diagnostic manuel Skyrock ; le dépôt contient aussi `.github/workflows/loading-diagnostics.yml` et `scripts/profile_loading.py`, précisément utilisés par le rapport cité au §9.

**Impact :** le chemin permettant de reproduire le diagnostic du chargement est moins visible que celui de Skyrock.

**Action :** nommer les diagnostics de collecte et de chargement dans la table et l’exploitation. Le texte du §9 décrit correctement le middleware et ses limites.

## R3 — Faible — L’absence de version de stack est acceptable, mais son autorité gagnerait à être explicite

**Preuves :** `pyproject.toml:9–22` fixe Python >=3.12 et les dépendances principales ; `requirements.lock` et `requirements-dev.lock` portent les environnements verrouillés. `ARCHITECTURE.md` nomme les technologies sans prétendre en recommander une version récente.

**Constat :** aucune version technologique inventée ni recommandation de migration n’est identifiée. La vérification brownfield porte sur la réalité du dépôt, pas sur une prétendue actualité du marché.

**Action :** ajouter un renvoi vers `pyproject.toml`, les locks et `Dockerfile` comme autorité de versions. Éviter de recopier une matrice qui divergerait du code.

## Affirmations examinées et confirmées

- API et boucles de collecte/enrichissement/YouTube : `app/worker.py:288`, Compose de production et Dockerfile ; pas de broker externe dans la configuration.
- SQLite partagé, WAL, pool 2 sans débordement et timeout : `app/database.py:9–31`, `compose.production.yaml`.
- Métadonnées récupérées sans connexion SQL de station conservée pendant le réseau : `app/worker.py:101–122`.
- Cache classement sous verrou pendant le calcul : `app/services/stats.py:29–57`. Caches statistiques/exploration bornés et calcul hors verrou : `app/metrics_api.py:341–352`, `app/services/explorer.py:148–159`.
- Archive rendue durable avant suppression SQL : `app/services/maintenance.py:39–61` ; indépendante des exclusions.
- Pause radio par sourdine, distincte des extraits : `app/static/player.js:36`, `:425–431`.
- Pages administratives protégées par session et redirection : `app/main.py:209–216`. API protégées par les dépendances d’administration.
- Sauvegarde préalable et observation 360 secondes : `scripts/deploy.sh:8–28`, `:105` ; santé et qualité fournisseur demeurent distinctes.
- Profilage authentifié, trois chemins, SQL non publié : `app/loading_profile.py`.

## Limites

Cette revue vérifie les mécanismes nommés par échantillonnage ciblé, sans prouver l’absence de bugs dans toutes les routes. Les durées de production sont attribuées au rapport existant ; elles n’ont pas été remesurées. L’exhaustivité des exclusions de chaque parcours et la restauration réelle nécessitent leurs validations propres. Rien ne permet d’affirmer qu’une migration PostgreSQL ou un découpage en services est nécessaire.
