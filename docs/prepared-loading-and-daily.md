# Chargement, classements préparés et écoute quotidienne

Travail demandé le 10 novembre 2026 : réaliser le reste du TODO et les demandes
à intégrer, en laissant k6 en attente. Les modifications préexistantes des outils
k6 et du `.gitignore` restent hors de cette livraison.

## Premier chargement et mesures

Le profilage serveur précédent cette livraison est le workflow
[42834186530](https://github.com/Hicha-m/track-radio/actions/runs/42834186530), sur
`cc628b2`. Le classement à froid demandait encore environ 2 294 ms de préparation
API, dont 2 097 ms d’exécution SQL ; un appel identique chaud descendait à 6 ms.
Les titres courants répondaient en environ 38 puis 8 ms au loopback. Cela distingue
le coût d’agrégation du coût de démarrage dans le navigateur.

Les premiers parcours Chrome neufs ont également montré des appels API démarrant
plusieurs secondes après réception du document. La page utilisait 22 fichiers JavaScript distincts et attendait la session
administrateur avant de monter la vue publique. Les délais sont variables ;
leur ensemble n’est pas attribué au SQL ni à des téléchargements supposés séquentiels.
Le serveur lisait aussi la liste des genres du catalogue pour les pages qui ne
l’utilisaient pas.

La nouvelle page charge un seul script d’application, différé et versionné. Les
sources restent séparées et inspectables ; le bundle est assemblé une fois au
démarrage, vérifié syntaxiquement en CI et cacheable par son empreinte. Le montage
public et ses lectures réseau fraîches ne sont plus bloqués par la session
administrateur ou la vérification de révision. Le serveur vérifie les exclusions
sur chaque résultat ; les caches navigateur restent inutilisés tant que la
révision n’est pas connue. Les mutations invalident toujours les lectures en vol. La
liste des genres est lue uniquement pour la recherche avancée.

Ces changements ne constituent pas une mesure de capacité. La comparaison réelle
avant/après et le seuil de deux secondes doivent être vérifiés après déploiement,
avec le statut des requêtes et les conditions des échantillons, sans test k6.

## Comptes exacts et vues préparées

La migration `0017` ajoute trois tables :

| Table | Usage |
| --- | --- |
| `play_counts` | Comptes par heure, radio et identité brute, avec l’identité musicale lorsqu’elle est connue |
| `ranking_counts` | Comptes des périodes courantes par radio, plus une vue toutes radios ; générations publiées ou en préparation |
| `ranking_states` | Génération publiée, heure de calcul et révision des exclusions globales pour chaque période |

Sur SQLite, des triggers maintiennent les compteurs dans la transaction qui
insère, corrige, importe ou supprime une diffusion. Une mise à jour du seul
`last_seen_at` ne touche pas ces compteurs. Le rattrapage initial compte l’historique
existant sans le modifier. Le masquage ne supprime aucune diffusion. Les vraies
rediffusions restent des lignes distinctes et contribuent chacune au compte.

Les fenêtres glissantes 24 h, 7 j, 30 j et tout l’historique sont préparées dans
le worker, avec une cadence visée de 30 secondes. Les heures entièrement incluses
viennent des compteurs ; les heures partielles aux frontières sont lues dans les
diffusions pour conserver exactement les bornes inclusives/exclusives. Sur un
moteur sans ces triggers, le préparateur utilise les diffusions ; aucune migration
de production vers PostgreSQL n’est introduite ici.

Un instantané de lecture cohérent alimente des lots de 500 lignes. Le préparateur
possède ses connexions, distinctes de celles du collecteur. Une brève transaction
publie les quatre générations ensemble ; un échec conserve les anciennes vues.
Les anciennes générations sont ensuite nettoyées par lots de 1 000 lignes. En production, les fichiers temporaires SQLite peuvent déborder sur le volume
de données configuré, plutôt que sur le tmpfs de 16 Mio. Le
Compose actuel utilise un seul préparateur ; cette coordination ne définit pas
un déploiement de plusieurs préparateurs concurrents.

Les exclusions globales sont appliquées pendant la préparation. À la lecture,
exclusions personnelles et globales sont cumulées **avant** regroupement, tri et
limite, y compris pour une session administrateur. Les variantes brutes sont
conservées jusqu’à cette étape ; les identités anciennes inconnues ne sont pas
fusionnées arbitrairement. Une révision globale incompatible empêche de servir
l’instantané et demande une nouvelle préparation.

`/api/stats/prepared` fournit les résultats, leur date de calcul et les bornes
exactes. La fiche, l’historique et l’export utilisent ces mêmes bornes. Les périodes
particulières restent disponibles via les routes existantes de calcul à la demande.
Aucune visite ne lance la reconstruction des vues courantes. Si aucune vue
compatible n’est prête, seul le classement attend et réessaie ; les titres et les
commandes restent indépendants. Un classement ancien conserve sa date, avec un
avertissement au-delà d’une minute ; une erreur d’actualisation ne lui invente pas
un nouvel horodatage.

## Daily et morceaux entiers

« Daily du jour » compose une sélection datée à Paris à partir des titres visibles
des sept derniers jours et des radios choisies. L’ordre est déterminé par la date
et les identités ; la source peut évoluer avec les nouvelles observations. La
sélection ne prétend pas représenter les goûts d’un compte externe.

La daily réutilise les correspondances YouTube déjà enregistrées. Elle ne lance
aucune recherche fournisseur à l’ouverture. Chaque vidéo est de nouveau validée
contre les exclusions et les choix courants avant lecture ; les absences et
correspondances ambiguës restent explicites. L’utilisateur peut rechercher les
manquantes, choisir une proposition, réordonner et exporter sa liste comme dans
le composeur existant. La lecture enchaîne les vidéos vérifiées, saute une vidéo
refusée et s’arrête à la fin sans boucler.

Le bouton « Écouter en entier · YouTube » distingue ce parcours des préécoutes.
Le lecteur YouTube ne reçoit pas de durée de coupure d’extrait : il lit la vidéo
choisie. Son accessibilité, les publicités et l’autoplay restent dépendants du
fournisseur et du navigateur.

Le plafond quotidien des **recherches** YouTube est distinct du temps d’écoute.
Une recherche externe reste accessible en cas d’erreur ou de quota. Dans l’éditeur
administrateur, on peut aussi fournir un lien YouTube : le serveur accepte des
URL HTTPS de domaines précis, interroge seulement l’endpoint oEmbed officiel et
vérifie artiste, titre et version avant association manuelle. Cette vérification
ne consomme pas de recherche YouTube Data API. Le choix utilise le contrôle de
révision et vérifie les exclusions avant/après l’appel distant. Une erreur ou une
mauvaise correspondance ne remplace pas le choix courant.

La recherche Google ciblée reste une piste manuelle : l’API Custom Search est
fermée aux nouveaux clients. La page de recherche Top-Charts proposée n’a pas pu
être vérifiée comme source d’un flux audio complet intégrable. Aucun accès direct
universel à tous les morceaux n’est annoncé. Spotify n’est pas ajouté, conformément
au choix utilisateur.

## Réglages de lecture

- L’enchaînement automatique est activable et mémorisé ; le désactiver termine le
  morceau courant puis arrête la suite, pour les extraits et la sélection YouTube.
- Le fondu enchaîné des extraits est optionnel, de 0,5 à 10 secondes. Deux éléments
  audio partagent le volume principal ; pause, recherche dans le morceau,
  changement de source ou d’exclusions annulent la transition. Une source
  défectueuse n’est pas réessayée en boucle.
- Les vidéos YouTube s’enchaînent sans mélange sonore. « Afficher la vidéo YouTube »
  masque ou réaffiche le panneau par CSS, conserve le même iframe et mémorise le
  choix. Le réglage est aussi accessible dans la sélection pendant la lecture.
  Les erreurs et choix sans vidéo disponible restent affichables.

La possibilité CSS est technique. Les [règles du lecteur YouTube](https://developers.google.com/youtube/terms/developer-policies)
prévoient un lecteur visible et interdisent les lecteurs en arrière-plan. Le réglage
ne change pas ces conditions ni les restrictions du fournisseur ; il ne sépare
pas l’audio et ne crée pas de nouvelle source musicale.

## Vérifications et exploitation

Les tests comparent les comptes préparés aux requêtes brutes avec les mêmes
bornes, variantes, radios et exclusions. Ils couvrent corrections SQL, annulations,
suppressions, migration d’un historique existant, restauration, résultats vides
et périmés. Les parcours Chrome couvrent également la daily, le masquage conservant
l’iframe, l’arrêt de l’autoplay, le fondu, les quotas, les erreurs, l’export et les
formats mobiles.

Le script `scripts/profile_prepared_rankings.py` effectue une comparaison bornée
en lecture seule à l’heure de calcul publiée. Il ne publie ni libellés musicaux,
ni exclusions privées, ni SQL paramétré. La CI et la CD restent les étapes finales
avant de déclarer cette livraison déployée. Les résultats réels sont ajoutés après
vérification. Une vérification oEmbed réelle d’un lien public connu a réussi
localement, sans clé Data API ; cela ne garantit pas la disponibilité de chaque vidéo.


Validation locale avant publication : **444 tests Python et 17 tests JavaScript**,
Ruff et syntaxe du bundle réussis. Les parcours couvrent ordinateur/mobile,
classements/détails/exports, média persistant, fondus et annulations, daily,
visibilité et autoplay. Les gardes session/révision peuvent rester bloquées dans
un parcours contrôlé sans empêcher les titres frais et l’écoute.

Sur un jeu synthétique de 200 000 diffusions, les comptes et les résultats sont
identiques : environ 5 211 ms pour préparer les quatre périodes, 251 ms pour le
classement brut et 24 ms pour la lecture préparée. Ce relevé local, avec activité
de validation, ne décrit pas les performances ou la capacité du serveur Oracle.
Les trois références Chrome avant livraison ont reçu des réponses API HTTP 200,
mais leur démarrage variait de 10,0 à 14,9 s après navigation. Un autre relevé a
trouvé les gardes rapides : le délai est variable et n’est pas attribué à une
cause exclusive sans preuve.


Le premier relevé après livraison a révélé une autre attente : la route asynchrone
des logos faisait sa lecture SQLite directement dans la boucle des requêtes.
L’acquisition d’une connexion occupée pouvait donc retarder les réponses publiques.
La lecture passe désormais par une dépendance synchrone exécutée dans le pool de
threads de FastAPI et libère sa transaction avant toute redirection ou découverte
d’image. Un test bloque volontairement cette lecture et vérifie qu’une seconde
requête répond avant sa libération, y compris pour un logo fourni directement.

Le diagnostic serveur [42844917582](https://github.com/Hicha-m/track-radio/actions/runs/42844917582)
a confirmé l’égalité exacte entre classements bruts et préparés dans les trois
cas contrôlés, avec les mêmes bornes et 100 résultats : toutes radios, exclusion
personnelle et radio seule. Les lectures brutes/préparées ont pris respectivement
environ 795/214 ms, 984/189 ms et 1 048/10 ms dans ces échantillons. Une ancienne
route brute a aussi attendu 16,9 s au loopback alors que son traitement instrumenté
prenait 505 ms : ce temps d’attente ne doit pas être attribué uniquement au SQL.

La première observation CD a été interrompue par une coupure SSH ; la révision
publique et la santé répondaient toujours. Des messages SSH de maintien de
connexion sont ajoutés pendant l’observation, sans supprimer ses contrôles ni
considérer leur interruption comme un succès.
