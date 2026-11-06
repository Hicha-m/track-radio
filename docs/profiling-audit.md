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
