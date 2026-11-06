# Audit de profiling du serveur — 6 novembre 2026

Cette branche `profiling` est réservée aux mesures et aux expériences. Elle ne doit pas être fusionnée dans `master`. Les corrections éventuelles sont proposées dans une branche et une pull request distinctes.

## Mesures sur le serveur

Source : [workflow Profiling audit 42267652685](https://github.com/Hicha-m/track-radio/actions/runs/42267652685), application publiée `ee06406`, 18:06–18:07 UTC. Requêtes GET séquentielles depuis le serveur, deux échantillons par cas ; aucune charge massive. Les mentions premier/deuxième appel ne garantissent pas un cache système froid. Ces observations ne sont ni des percentiles utilisateurs ni un test de capacité.

| Requête interne | Premier appel | Deuxième appel |
| --- | ---: | ---: |
| Santé | 27 ms | 2 ms |
| Accueil HTML | 147 ms | 7 ms |
| Passages récents | 243 ms | 23 ms |
| Classement sur sept jours | 505 ms | 7 ms |
| Classement complet | 5 456 ms | 77 ms |
| Recherche des musiques | 3 055 ms | 13 ms |
| Synthèse Stats sur 24 h | 4 446 ms | 4 ms |

Une requête de santé lancée pendant un classement complet répond en 4 ms ; pas de blocage global démontré par cet essai à deux requêtes simultanées.

Observations de ressources : 951 Mio de RAM hôte, 441 Mio disponibles ; swap occupé mais aucun échange actif dans les deux intervalles `vmstat` observés. Disque utilisé à 21 %. Conteneurs API et collecteur sains, sans OOM ni redémarrage, API à 40 puis 58 Mio sur une limite de 160 Mio ; collecteur à 76 puis 43 Mio sur 128 Mio. La charge système avant les sondes est de 0,31. Ces instantanés ne constituent pas une surveillance de longue durée et ne justifient pas une augmentation automatique des ressources.

## Profil Python/SQL

Profil dans un conteneur distinct : 160 Mio, 0,25 CPU, réseau désactivé, volume de production monté en lecture seule, délai maximal par cas de vingt secondes. Ces durées instrumentées ne sont pas les latences HTTP de l’API. Aucune ligne de catalogue, paramètre SQL, donnée source ou variable secrète n’est affiché ni exporté.

Les quatre cas aboutissent. Les plans montrent :

- classement complet : lecture de `plays`, sous-requête d’exclusion utilisant son index existant, regroupement et tri temporaires ;
- période : parcours de l’index de date, suivi d’accès aux lignes de `plays` ;
- recherche des musiques : parcours par identifiant, jointure aux stations, regroupement, `COUNT(DISTINCT)` et tri temporaires ;
- synthèse : plusieurs parcours par date ; la requête des genres représente environ 2,01 s instrumentées et utilise l’index de clé des métadonnées avec accès aux lignes.

La normalisation Unicode contribue au coût, mais la majorité du temps profilé reste dans l’exécution SQLite. Les lignes contiennent aussi des métadonnées source et des fiches JSON/paroles inutiles à ces agrégations : l’évitement de ces accès est l’hypothèse d’optimisation principale. Un [index couvrant](https://www.sqlite.org/eqp.html) peut fournir les colonnes de la requête sans retourner à la table. Aucun contournement des exclusions ni regroupement supplémentaire des diffusions n’est proposé.

## Expérience synthétique

`scripts/profiling/synthetic.py` crée une base temporaire indépendante : 48 000 passages, 4 000 morceaux, neuf stations, trente exclusions globales, lignes source de huit kilo-octets et paroles synthétiques. Aucun échantillon de production n’est utilisé. Le cache SQL applicatif est vidé entre les mesures ; même machine, même base, trois répétitions par cas. Les résultats complets avant/après sont comparés à l’identique.

Première expérience : médianes de 253 à 92 ms pour le classement complet, 601 à 154 ms pour la recherche des musiques, 102 à 44 ms pour la synthèse. Une seconde expérience ajoute d’abord `ANALYZE` seul puis les index un par un : le classement complet et la recherche restent lents après `ANALYZE` seul et sont accélérés lors de l’ajout de l’index par artiste/titre. Les chiffres synthétiques ne sont pas présentés comme des gains vérifiés en production.

Proposition à valider dans une PR distincte : index couvrants sur les passages (artiste/titre et période) et sur le couple identifiant/genre des métadonnées, ciblés sur SQLite. Migration réversible, données et filtres conservés. Comparer à nouveau les plans et latences après déploiement ; ne pas augmenter CPU/RAM ni changer les limites de cache sans nouvelles preuves.


## Correction fusionnée et mesures après déploiement

La [PR #1](https://github.com/Hicha-m/track-radio/pull/1), issue de `perf/sqlite-covering-indexes`, a été fusionnée dans le commit [8de8c3a](https://github.com/Hicha-m/track-radio/commit/8de8c3a55dab1302ad20eed600b7c139387cd0ea). Elle contient seulement quatre fichiers : modèles/index, migration 0013, tests et documentation. Aucun fichier de cette branche d’audit n’a été fusionné. Les index sont conditionnés à SQLite.

Validation : 298 tests Python et 16 tests Node, Ruff/formatage et syntaxe JavaScript/déploiement réussis. Le test de migration compare avant/après puis après retour à 0012 et réapplication : historique brut conservé, exclusions personnelles/globales appliquées, quatre vraies diffusions toujours comptées comme quatre, agrégations/pagination/sélections inchangées. Le schéma PostgreSQL reste sans ces index spécifiques. Régression navigateur des classements/sélections/exports réussie en ordinateur/mobile sur fixtures synthétiques.

[CI du commit fusionné](https://github.com/Hicha-m/track-radio/actions/runs/42269391813) et [déploiement](https://github.com/Hicha-m/track-radio/actions/runs/42269595583) réussis. La révision réellement servie est vérifiée avant la seconde série de mesures. [Audit après déploiement 42270144724](https://github.com/Hicha-m/track-radio/actions/runs/42270144724), 18:22–18:23 UTC : mêmes endpoints, même serveur, mêmes limites de conteneurs, deux appels par cas, sans concurrence sauf la sonde explicitement indiquée.

| Requête | Avant, appels 1 / 2 | Après, appels 1 / 2 |
| --- | ---: | ---: |
| Santé | 27 / 2 ms | 2 / 3 ms |
| Accueil HTML | 147 / 7 ms | 269 / 7 ms |
| Passages récents | 243 / 23 ms | 47 / 10 ms |
| Classement sept jours | 505 / 7 ms | 194 / 7 ms |
| Classement complet | 5 456 / 77 ms | 1 775 / 11 ms |
| Recherche des passages | 212 / 48 ms | 32 / 10 ms |
| Recherche des musiques | 3 055 / 13 ms | 370 / 10 ms |
| Synthèse Stats | 4 446 / 4 ms | 173 / 3 ms |

La sonde simultanée trouve 243 ms pour le classement et 5 ms pour la santé, contre 3 185 ms et 4 ms avant. Tous les appels mesurés renvoient HTTP 200. Le premier rendu HTML est plus lent dans cette série ; son deuxième appel est stable et l’échantillon ne permet pas d’attribuer une régression au rendu.

Les plans SQL réels confirment `ix_plays_period_cover` pour les périodes et la synthèse, `ix_plays_chart_cover` pour le classement complet et la recherche, et `ix_track_metadata_genre_cover` pour la jointure des genres. Le regroupement temporaire du classement complet et de la recherche disparaît ; leurs tris restent nécessaires. Le SQL des genres passe d’environ 2,01 à 0,13 s dans le profil instrumenté séparé. Les quatre profils aboutissent sans interruption ni erreur.

Les deux conteneurs sont sains, sans OOM ni redémarrage dans les instantanés finaux. API : 53 puis 81 Mio / 160 Mio ; collecteur : 71 puis 68 Mio / 128 Mio. Il ne s’agit donc pas d’une promesse de baisse de RAM. Les limites CPU/RAM, le traitement des exclusions et la collecte restent inchangés.

### Limites et suites

Ces chiffres sont des observations ponctuelles internes, pas des p95 ni des mesures d’expérience utilisateur. Le cache applicatif et le cache système influencent les temps, et la collecte continue entre les deux séries. L’identité des résultats est vérifiée sur données synthétiques fixes, pas par export d’un catalogue réel. La vérification des plans réels renforce l’attribution de la baisse des lectures SQLite aux index, sans transformer deux échantillons en garantie permanente.

Aucune augmentation de CPU/RAM, changement de quota, refonte de cache ou de normalisation n’est justifié par ces seules mesures. Le coût résiduel du classement complet et les tris/distincts restent à surveiller si le volume augmente. Les profils SQL et les essais synthétiques sont reproductibles avec les scripts de cette branche. Pour lancer un nouvel audit serveur, modifier explicitement `scripts/profiling/request.json` avec le SHA déployé attendu : une mise à jour de ce rapport ne relance pas de charge serveur et ne déploie rien.

## Nouvelle vérification indépendante sur production réelle

À la demande de l’utilisateur, les mêmes tests serveur ont été relancés sans simulation sur la révision déployée `8de8c3a55dab1302ad20eed600b7c139387cd0ea`. [Deuxième tentative du workflow serveur](https://github.com/Hicha-m/track-radio/actions/runs/42270144724/attempts/2), mesures 18:40–18:41 UTC : succès. Santé et révision contrôlées ; aucune purge, aucun redémarrage et aucune modification applicative ou de configuration.

| Requête interne | Avant correction, appel 1 | Nouveau contrôle, appels 1 / 2 |
| --- | ---: | ---: |
| Classement sept jours | 505 ms | 153 / 7 ms |
| Classement complet | 5 456 ms | 301 / 10 ms |
| Recherche des musiques | 3 055 ms | 339 / 9 ms |
| Synthèse Stats | 4 446 ms | 92 / 3 ms |

Le profil Python/SQL est réexécuté dans un nouveau conteneur avec le volume réel en lecture seule et la même limite de 0,25 CPU : les calculs SQL sont effectivement relancés, sans réutilisation des résultats en cache du processus HTTP. Classement complet instrumenté : 4,31 s avant correction, 0,90 s à ce nouveau contrôle ; recherche : 4,79 → 1,06 s ; synthèse : 3,14 → 1,34 s. Ces durées instrumentées restent distinctes des latences HTTP. Les trois index couvrants sont toujours présents dans les plans ; quatre profils terminés sans erreur.

En complément, trois tours via `https://radio.hicham.dpdns.org` avec le client `httpx` standard, sans changement de User-Agent, de 18:42 à 18:43 UTC : deux appels par endpoint (santé, classement complet, recherche des musiques, synthèse), pause de 35 secondes entre les tours, contrôle de la révision à chaque tour. Les 24 mesures sont HTTP 200. Premiers appels observés : classement 679 / 303 / 279 ms ; recherche 359 / 339 / 341 ms ; synthèse 113 / 140 / 151 ms. Seconds appels : classement 21–46 ms ; recherche 30–113 ms ; synthèse 16–41 ms. Ces temps publics incluent le réseau et Cloudflare. L’espacement dépasse le TTL nominal de trente secondes, mais ne garantit pas un cache froid si d’autres visiteurs l’ont alimenté.

Le test public initial avec `urllib` a reçu HTTP 403, `server: cloudflare`, corps `error code: 1010`, alors que le client `httpx` normal reçoit HTTP 200. Les 24 refus `urllib` ne sont pas inclus dans les mesures de performance applicative. Aucun réglage Cloudflare ni contrôle d’accès n’a été modifié.

Les conteneurs restent sains, sans OOM ni redémarrage observé. API : 94 puis 109 Mio / 160 Mio ; collecteur : 79 puis 62 Mio / 128 Mio. Ces instantanés ne prouvent pas l’absence de pic ou de fuite sur une longue période. Les observations confirment l’efficacité sur la vraie production, avec les limites d’échantillonnage déjà indiquées. Aucun nouveau correctif ni déploiement n’a été nécessaire pour cette vérification.
