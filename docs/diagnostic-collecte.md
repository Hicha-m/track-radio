# Diagnostiquer un retard de collecte

Cette procédure décrit les moyens existants, leurs limites et les éléments à
recueillir avant de changer une source. Les observations d'incident, journaux et
réponses de production restent dans le dossier local ignoré `.plan/`.

## Vérifier la version et le périmètre

Comparer `/api/version` au SHA dont la CI et la CD ont réussi. `/health` vérifie
l'accès de l'API à la base ; il ne vérifie ni le collecteur ni une radio.
`/api/stations` expose notamment la source, le flux et l'activation réellement
enregistrés. Il ne donne pas tous les paramètres d'extraction ou les variables
effectives du conteneur.

Depuis une connexion d'exploitation autorisée, relever en lecture seule :

- l'heure UTC du serveur et la synchronisation de son horloge ;
- la révision et l'état des conteneurs, leurs redémarrages et contrôles de santé ;
- les paramètres effectifs de collecte, limités à l'intervalle, au timeout, à la
  concurrence, au nombre maximal de stations et à l'intervalle d'écriture des
  observations ; ne jamais afficher l'environnement complet ;
- la configuration de la station et les événements du collecteur sur la fenêtre
  concernée, sans exporter de métadonnées masquées ou de données d'utilisateurs.

## Interpréter les horodatages

| Horodatage | Signification et limite |
| --- | --- |
| Début/fin fournisseur | Horaire annoncé du passage, avec fuseau explicite ; pas nécessairement l'heure de mise à disposition de la réponse. |
| `ts`, `Date`, `Last-Modified`, `Age` | Indices de génération, d'horloge et de cache ; aucun ne garantit à lui seul que le morceau est courant. |
| Réception de la sonde | Heure du client de diagnostic ; ce n'est celle du worker que si la mesure vient de son environnement. |
| `detected_at` | Création du passage en base, pas début réel d'antenne. |
| `last_seen_at` | Dernière observation **persistée** ; les répétitions sont écrites au plus une fois par `LAST_SEEN_WRITE_SECONDS` (120 s par défaut). |
| `checked_at` | Heure de calcul de l'état exposé par `/api/stations/now-playing`. |
| Affichage | Dépend du parcours et de la visibilité de l'onglet ; les cartes radio interrogent l'API toutes les 30 s. |

Convertir les instants en UTC avant de soustraire des dates. Mesurer le décalage
des horloges et conserver la durée de chaque requête. Une comparaison entre
début fournisseur et détection donne un écart apparent, sans décomposer le délai
de publication du fournisseur, celui du flux ICY et celui de la collecte.

L'intervalle de collecte est désormais une cible entre les débuts de tentatives
par radio. Une tentative longue, une file occupée ou une sonde peuvent repousser
ce départ ; aucune échéance temps réel n'est garantie. Après un échec, le
collecteur temporise jusqu'à 300 s selon les échecs consécutifs. Un titre inchangé
ou exclu peut constituer une collecte réussie sans nouvelle diffusion.

## Supervision disponible

`/admin/workers` et `/api/admin/workers` nécessitent l'administration. Ils
présentent les états persistants du collecteur, de l'enrichissement et de
YouTube, avec heartbeat, début du tour, activité et compteurs. Les événements
`started`, `completed` et `error` sont conservés dans un journal global plafonné
à 200 entrées ; l'API en restitue les 100 dernières. Une fenêtre ancienne peut
donc déjà avoir disparu.

La même page présente désormais un résumé par radio et un journal de tentatives,
via `GET /api/admin/collection`. Chaque collecte conserve son identifiant de
tentative et de tour, source, début/fin UTC, durée totale (réseau et traitement),
statut HTTP final lorsqu'il existe et résultat. Les logs structurés
`collection_diagnostic` utilisent les mêmes identifiants. Une tentative est
persistée avant l'accès réseau : un arrêt du processus peut donc laisser une
tentative en cours, reconnue comme interrompue au prochain essai de cette radio.
Les échecs de stockage du diagnostic sont signalés séparément sans empêcher une
collecte valide ni remplacer son erreur initiale.

Le journal distingue refus/erreur HTTP (dont 403, 429 et 5xx), timeout, erreur
réseau, format inattendu, extraction/réponse vide, événement ancien ignoré,
contenu inchangé, nouveau passage et exclusion. Il conserve les dates HTTP,
`Last-Modified`, `Age` et directives de cache reconnues lorsqu'ils sont présents.
Aucun message d'exception, URL, en-tête arbitraire, titre, artiste, identifiant
musical ou payload fournisseur n'est stocké dans ce diagnostic.

Le résumé indique dernier essai, succès, observation utile, changement détecté,
compteurs cumulés depuis le début de la supervision, échecs consécutifs, durée,
HTTP et prochaine tentative **au plus tôt**. Cette date est une estimation :
la fin du tour et l'attente des autres radios peuvent la repousser. Les valeurs
de pause, timeout, concurrence, nombre maximal de radios et intervalle d'écriture
affichées sont relevées par le worker lui-même à chaque tentative.

Une observation utile vient d'une extraction traitée, même si elle est inchangée
ou exclue, contrairement à `Play.last_seen_at`, dont les écritures sont espacées.
Un changement détecté peut être un habillage ou une émission : il n'atteste pas
à lui seul d'un changement musical. Un événement ancien ignoré confirme un accès
réussi mais ne renouvelle pas l'observation utile. Les sondes n'actualisent aucun
compteur ni aucune date de santé de la collecte normale.

L'état `completed` du worker décrit la fin d'un tour, pas le succès de toutes les radios.
Le heartbeat du fichier de santé est mis à jour après un tour ; il n'est pas une
preuve de fraîcheur de Skyrock. Le seuil d'ancienneté affiché par l'administration
diffère du délai du contrôle de santé Docker.

### Seuils et coût de lecture

- Observation ancienne après `max(180, 3 × pause + timeout)` secondes, selon la
  configuration de l'API ; contenu inchangé signalé après 900 secondes. Ce sont
  des invitations à vérifier, pas des preuves de panne ou de morceau bloqué.
- Tentative anormalement longue après `timeout + 30` secondes ; cet indicateur
  ne remplace pas le heartbeat du worker. Un échec reste distinct d'une source
  observée mais inchangée ; un succès remet les échecs consécutifs à zéro.
- Journal limité à 500 tentatives par radio, 5 000 au total et 48 h au maximum.
  Nettoyage après chaque tour : si le worker s'arrête, le nettoyage reprend à
  son redémarrage. Les plafonds peuvent raccourcir fortement la fenêtre réelle.
  Seuls les diagnostics sont purgés, jamais les diffusions.
- Résumés limités à 100 radios ; filtrer par identifiant pour inspecter une
  radio hors de ce lot. Les événements se filtrent par radio, période de
  1/6/24/48 h, résultat et type (collecte/sonde), avec pagination par identifiant
  (50 entrées par défaut, 100 maximum). Deux lectures SQL, sans lecture musicale.
- Rafraîchissement toutes les dix secondes sur la page visible et authentifiée,
  sans chevauchement automatique de requêtes. Les lectures sont `no-store`.
  Les filtres musicaux personnels/globaux ne retirent pas les preuves techniques,
  qui ne contiennent aucune donnée musicale ; aucun résultat musical n'est exposé.

La migration `0014` crée `collection_status` et `collection_events`, sans
modifier les anciennes diffusions. Les sauvegardes incluent ces tables ; les
archives antérieures restent importables. Le retour à `0013` retire uniquement
les nouveaux diagnostics. Les anciennes pannes ne sont pas reconstruites.
Le contrôle de déploiement vérifie en lecture seule que le worker de la révision
attendue a persisté une tentative terminée et récente pour la première radio
active. Il valide la remontée du diagnostic, y compris en cas d'erreur amont,
sans publier de données de production ni imposer le succès de chaque fournisseur.

## Comparer les sources Skyrock

Observer le flux ICY configuré et les deux endpoints publics
`https://skyrock.fm/api/v3/player/onair/super` et
`https://skyrock.fm/api/v3/player/onair`, depuis l'environnement du collecteur.
Utiliser le transport et les bornes du collecteur, limiter la lecture ICY au
premier bloc de métadonnées et fermer le flux. Ne pas enregistrer l'audio.

Le bouton **Sonder ICY et JSON depuis le worker**, sur la carte Skyrock,
demande trois sondes via `POST /api/admin/collection/probes`, avec la session
admin et sa protection CSRF habituelles. Les destinations sont fixes, limitées
au flux national configuré et aux deux endpoints ci-dessus ; aucune URL ne
peut être fournie par le client. Un seul lot par station est accepté toutes les
cinq minutes, et aucun nouveau lot tant qu'une sonde précédente reste en attente
ou en cours. Le worker exécute une sonde à la fin de chaque tour, avec son propre
transport, son réseau et son timeout. Si le worker est arrêté, les sondes restent
en attente : leur demande ne déclenche aucun accès réseau dans l'API.

Une sonde ajoute au plus une requête bornée au tour, donc peut augmenter sa durée
jusqu'au timeout configuré. Elle ne modifie ni la source, ni l'historique, ni les
choix musicaux ; aucun rattrapage n'est effectué. Une sonde interrompue depuis
plus de deux minutes est reconnue au passage suivant du worker. Les heures,
statuts et identifiants de tour permettent la corrélation avec les collectes.
La comparaison JSON conserve seulement l'heure `ts`, le nombre d'entrées, le
nombre d'intervalles couvrant cette heure et la dernière fin déjà passée : elle
ne sélectionne ni ne recommande automatiquement un morceau ou une source.

Pour chaque sonde, conserver uniquement les heures, la durée, le statut HTTP,
les erreurs et les en-têtes de cache disponibles. Pour JSON, extraire seulement
les champs techniques nécessaires : `ts`, région, type, UID, début et fin.
Écarter les informations personnelles des réponses. Pour `/super`, examiner
`radios.natio`, sans confondre les autres radios avec la station suivie.

Un tableau `schedule` peut mêler historique et passage courant ; vérifier les
bornes temporelles de chaque événement. Le libellé d'une émission ne suffit pas
à classifier une entrée comme morceau. Une réponse récente peut contenir un
événement terminé. Un ETag différent ne prouve pas un changement musical.

Ne pas transformer ces observations en changement automatique de source, en
rattrapage ou en suppression d'historique. Un succès depuis le poste de
diagnostic ne démontre pas l'accès depuis le worker. Un refus HTTP isolé ne
prouve pas un bannissement d'IP ; conserver le contexte sans contourner le refus.

## Distinguer les causes

| Observation | Ce qu'elle permet de vérifier |
| --- | --- |
| Heartbeat ancien et aucun tour récent | Collecteur interrompu ou tour en retard ; vérifier le processus et les logs. |
| Worker actif, réponses ICY répétées, JSON évolutif | Possible stagnation ICY ; comparer depuis le même environnement avant de conclure. |
| Erreurs réseau/HTTP ou extraction vide | Échec d'une tentative ; relever statut, durée et réessai effectifs. |
| Métadonnées identiques et observations persistées renouvelées | Morceau inchangé ou source répétitive ; le worker continue à observer. |
| État `hidden` | Exclusion appliquée ; ce n'est pas une preuve de panne. |
| Base/API récente, affichage ancien | Examiner le polling du parcours, la visibilité de l'onglet, les erreurs navigateur et les filtres. |

Appliquer les exclusions personnelles et globales avant les résultats musicaux
et les comparaisons concernés, y compris en administration. En cas d'exclusion,
conserver uniquement le diagnostic technique sans révéler les libellés masqués.
La blacklist bloque les nouvelles insertions et conserve l'historique.

Un bilan doit séparer faits, hypothèses et inconnues. Si les logs historiques ou
l'accès au worker manquent, le dire explicitement : une reprise observée ne
permet pas d'attribuer la cause de l'incident passé.

## Identification Skyrock et choix de source

ICY reste la source nationale active. Les lectures comparatives depuis le poste
de diagnostic confirment l'accès aux deux JSON et un coût inférieur à ICY, mais
ne prouvent pas leur disponibilité depuis le worker ni une meilleure couverture
musicale : un JSON récent peut conserver le dernier morceau terminé. Aucun repli
automatique et aucun rattrapage ne sont activés. La cadence reste inchangée.

L'adaptateur JSON national est disponible pour une configuration explicitement
choisie : `source_type=json`, `metadata_url` égal à l'un des deux endpoints
Skyrock documentés, flux national exact et `json_title_path=radios.natio.schedule`
(champ exigé par la configuration, l'adaptateur réalise la sélection temporelle).
Pour `/super`, seule `radios.natio` est inspectée. Il exige une horloge fournisseur
âgée d'au plus 60 s, avec une avance tolérée de 5 s, des dates ISO zonées cohérentes
avec leurs epochs, une durée positive d'au plus deux heures, un UID valide et
un unique événement `record` couvrant à la fois l'heure fournisseur et l'heure
locale. L'ordre du tableau ne décide jamais du morceau. Un programme, un trou
de programme ou un événement terminé ne déclenchent aucune reconstruction.
Les artistes multiples sont conservés dans l'ordre fourni. Seuls début et UID
sont retenus dans les métadonnées brutes JSON, sans payload ni données personnelles.

Les UID nationaux ICY (`§…`) et JSON partagent une identité de passage. Un même
UID ne crée pas une seconde diffusion lors d'un réessai, changement de libellé
ou transition de source. Un nouvel UID permet une vraie rediffusion, même avec
un libellé identique. Les 32 dernières identités sont mémorisées dans l'état de
collecte, y compris lorsqu'elles sont exclues, et survivent au redémarrage.
Ce registre borné ne garantit pas la reconnaissance d'un très ancien UID ICY
rejoué au-delà de cette fenêtre ; JSON bénéficie aussi de la validation horaire.

Le couple exact `SKYROCK` / `Hit Skyrock`, identifié comme libellé d'émission dans
les éléments fournis, est observé comme contenu non musical : résultat technique
`non_music`, sans nouveau passage ni retour au précédent morceau sur les cartes.
Aucune règle générale ne classe tous les titres de Skyrock comme émissions.
L'historique existant est conservé, dont les anciens passages de cet habillage.
Les exclusions continuent à bloquer les nouvelles insertions ; aucun masquage
ne provoque une suppression ou une fusion automatique.

## Transport et cadence

L'observation réseau du site fournisseur a montré des requêtes périodiques
`/onair` et `/onair/super` espacées d'environ 15 secondes sur une fenêtre de
45 secondes, sans connexion SSE ou WebSocket observée. Cette fenêtre ne prouve
pas l'absence générale de notifications. Aucun abonnement serveur documenté et
exploitable n'a été établi ; les [CGU du fournisseur](https://skyrock.fm/cgu)
ne constituent pas un contrat d'API de notifications. Aucun transport poussé
n'est donc activé ou promis.

Le collecteur reste en polling avec ICY actif. La valeur configurée reste 20 s
par défaut (bornes de configuration 5–3600 s). Chaque radio possède une échéance
monotone calculée depuis le début de sa tentative, plutôt qu'une pause complète
ajoutée après le dernier résultat du tour. La concurrence et le timeout restent
bornés ; une radio en échec est retirée des tours jusqu'à son échéance. La boucle
attend au moins une seconde, et vérifie configuration/heartbeat au plus tard à
l'intervalle de base, même lorsqu'une radio est en longue temporisation.

Avec une tentative synthétique de 2 s et un intervalle de 20 s, deux départs
mesurés sont distants de 20 s contre 22 s pour l'ancien ordonnancement. Le délai
moyen théorique d'échantillonnage passe de 11 à 10 s dans ce cas régulier, avec
un budget passant d'environ 164 à 180 requêtes/h par radio. Ce résultat contrôlé
n'est pas une mesure de latence d'antenne ou de charge CPU en production. Une
source retardée peut continuer à transmettre un événement ancien malgré cette
réduction de l'attente du collecteur. La valeur de 10 s n'est pas imposée.

Après un échec, la temporisation exponentielle reste plafonnée à 300 s ; un
`Retry-After` valide sur HTTP 429/503 impose une attente au moins aussi longue.
Les secondes et dates HTTP zonées sont acceptées, les valeurs invalides ignorées
et les durées numériques représentables limitées à 2^31−1 s. Le prochain départ
persisté est restauré après redémarrage. Les sondes admin respectent aussi ces
temporisations ; une sonde refusée peut repousser la collecte normale sans
modifier ses compteurs de réussite. Cette précaution s'applique à toute la radio,
même lorsque les deux endpoints utilisent des hôtes différents.

Le flux ICY observé ne fournit ni ETag ni Last-Modified, avec `no-cache` : aucune
revalidation conditionnelle ni réutilisation d'anciennes métadonnées n'est
introduite. L'adaptateur JSON inactif ne reçoit pas de cache musical automatique.
Un 304 futur ne serait pas assimilé à une nouvelle observation de morceau sans
règles de fraîcheur supplémentaires.

Lors d'un déploiement, si l'échéance persistée dépasse la fenêtre du contrôle,
un heartbeat récent permet de signaler le contrôle comme différé plutôt que
de provoquer une requête anticipée. Ce résultat ne certifie pas une nouvelle
tentative de la révision déployée ; il indique que le délai fournisseur est
respecté. Sans heartbeat récent, le contrôle continue d'attendre puis échoue.

## Validation et observation après publication

La CD exécute maintenant `scripts/observe_collection.py --seconds 360` après les
contrôles de démarrage : six minutes d'observation, avec une lecture toutes les
30 secondes dans la base de l'application. Le script ne fait aucun appel au
fournisseur, ne force aucun réessai et ne demande aucune clé administrateur.
Il ne sélectionne ni titres, ni artistes, ni métadonnées brutes et ne modifie
aucune ligne. Les sessions de lecture sont fermées entre les relevés.

Seuls des indicateurs booléens sont publiés dans le journal de validation :
heartbeat récent, progression des tentatives, progression des autres stations,
plusieurs changements Skyrock détectés après le premier relevé, renouvellement
des observations, présence d'erreurs nouvelles et échéance future. Les dates,
compteurs, données musicales et réponses fournisseur ne sont pas exportés.
Ces indicateurs sont techniques ; ils ne constituent pas des statistiques
musicales et ne dévoilent pas les libellés masqués. Deux requêtes bornées sont
utilisées par relevé, sans lecture de la table des diffusions.

Un heartbeat récent ou une progression des autres radios ne valide pas la
fraîcheur de Skyrock. Les changements déjà présents au début de la fenêtre ne
comptent pas comme nouvelles transitions. Si deux changements Skyrock ne sont
pas effectivement observés, le résultat indique explicitement que les
transitions et la comparaison du délai d'antenne restent non confirmées.
Cela ne transforme pas un titre inchangé ou exclu en panne de déploiement.
Un heartbeat ancien à la fin de la fenêtre fait en revanche échouer ce contrôle.
Le délai maximal du job de déploiement inclut cette fenêtre d'observation.

Les tests de reprise couvrent également un redémarrage après expiration d'une
échéance : le nombre d'échecs consécutifs reste conservé pour calculer le
prochain délai progressif, tout en permettant l'essai devenu éligible.
