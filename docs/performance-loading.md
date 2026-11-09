# Chargement initial — première mesure du 9 novembre 2026

Diagnostic du point 2, en cours. Aucun changement de stockage ou de stratégie de
cache n’est approuvé par ce rapport. Référence applicative examinée : `d20214b`.
[Profilage serveur](https://github.com/Hicha-m/track-radio/actions/runs/42652257552).

## Protocole et limites

Le script `scripts/profile_loading.py` est exécuté par le workflow manuel
`loading-diagnostics.yml`, dans le conteneur API. Une connexion SQLite est mise
en lecture seule et les calculs sont bornés par un progress handler. Aucun cache
du processus API n’est vidé, aucun titre, résultat, paramètre SQL ou secret n’est
imprimé. Les services appliquent la blacklist et une liste personnelle vide :
ce relevé ne représente pas les préférences d’un navigateur particulier.

Le premier calcul du classement a un cache applicatif vide dans un processus
isolé. Le cache disque du système et celui du service existant ne sont pas
contrôlés. « À froid » ne signifie donc pas machine redémarrée. Les appels
HTTP locaux utilisent ensuite le vrai processus API ; leur premier état de cache
est inconnu. L’API, le worker et les quotas CPU continuent à fonctionner pendant
la mesure. Deux échantillons ne permettent pas de calculer un p95 représentatif.

`cursor_execute_ms` mesure les appels au pilote SQL ; la récupération des lignes,
la préparation des objets et la contention CPU peuvent aussi contribuer au temps
total. La différence ne doit pas être baptisée automatiquement « temps Python ».
SQLite utilise ici `EXPLAIN QUERY PLAN`, pas le `EXPLAIN ANALYZE` de PostgreSQL.

## Résultats

| Calcul direct dans le processus isolé | Total | Temps cumulé `cursor.execute` |
| --- | ---: | ---: |
| Classement, période de 7 jours, premier calcul | 701,76 ms | 558,69 ms |
| Même classement, mêmes paramètres | 6,42 ms | 0,12 ms |
| Classement, début de période décalé de 30 s | 381,72 ms | 373,87 ms |
| Titres courants, premier appel | 204,22 ms | 99,22 ms |
| Titres courants, répétition | 91,61 ms | 0,33 ms |
| Tableau admin, premier appel | 13 667,47 ms | 13 658,86 ms |
| Tableau admin, répétition | 16 431,67 ms | 16 013,92 ms |

| HTTP via loopback du serveur | Premier échantillon | Deuxième échantillon |
| --- | ---: | ---: |
| Classement | 2 884,07 ms | 16,49 ms |
| Titres courants | 3 879,63 ms | 143,01 ms |
| Image station 2, redirection suivie | 224,57 ms | 32,43 ms |
| Tableau admin authentifié | 11 902,76 ms | 13 023,36 ms |

Contrôle depuis le poste de travail, avec période du signalement : classement
HTTP 200 à 559,72 ms, titres courants HTTP 200 à 1 762,91 ms, redirection d’image
HTTP 307 à 115,18 ms sans téléchargement final. Ces relevés ont un autre réseau
et un autre contexte ; ils ne s’additionnent pas aux durées du tableau.

## Lecture des plans

- `SEARCH plays USING COVERING INDEX ix_plays_period_cover (detected_at>?)` :
  le classement utilise un index couvrant pour restreindre la période. Cela évite
  de lire les larges payloads des lignes complètes, mais pas de parcourir tous les
  passages de la période.
- `USE TEMP B-TREE FOR GROUP BY` et `ORDER BY` : SQLite construit des structures
  temporaires pour regrouper et classer. `LIMIT 20` borne les résultats renvoyés,
  pas nécessairement le travail d’agrégation préalable.
- `CORRELATED SCALAR SUBQUERY` puis recherche dans
  `ix_global_exclusions_match` : la blacklist est appliquée dans le calcul.
  La retirer pour gagner du temps serait un changement incorrect de résultat.
- Les titres courants utilisent l’index station/date pour retrouver les derniers
  passages, puis leurs clés primaires et l’index de catalogue. Ce relevé ne prouve
  pas un balayage complet de l’historique pour le cas observé.
- Le tableau admin fait quatre SELECT, dont deux `SCAN track_metadata`, avec
  vérification des exclusions. Le code compte séparément les fiches visibles et
  les fiches à revoir. La somme des temps SQL est très élevée, mais le relevé
  cumulé ne dit pas encore lequel des quatre SELECT en porte quelle part.

## Conclusion provisoire et options

Le signalement de lenteur répétée du tableau admin est reproduit. Le cache du
classement aide fortement sur une clé identique ; des timestamps différents
changent cette clé. Les 15 s initialement signalées pour le classement et l’image
ne sont pas reproduites à l’identique, et aucune cause unique ne doit leur être
attribuée sans nouvelle mesure.

Première proposition : détailler les temps de chaque SELECT et comparer une
agrégation commune des deux compteurs du catalogue aux deux calculs existants.
Elle peut éviter un parcours et une répétition du filtre, mais son gain reste à
mesurer. Un index couvrant adapté ou des champs normalisés persistés sont d’autres
options à évaluer si ce calcul reste trop coûteux ; ils impliquent stockage,
migration et maintenance. Un cache admin réduit les recalculs répétés, mais ne
corrige pas le coût à froid et introduit un délai d’affichage. PostgreSQL n’est
pas établi comme nécessaire par ces mesures.

Les objectifs proposés au product owner sont inférieurs à 2 s pour le tableau
admin et 1 s pour classement/titres côté serveur, sans nouveau cache par défaut.
Ce sont des propositions, pas des objectifs approuvés ni des gains annoncés.
Le choix, les mesures avant/après, les essais avec exclusions personnelles et
l’analyse du parcours navigateur restent à compléter avant de cocher le point 2.
