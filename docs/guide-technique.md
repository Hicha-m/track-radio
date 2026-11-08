# Guide technique

[← Retour au README](../README.md)

Configuration, fonctionnement du catalogue, collecte, API, sauvegardes et exploitation. Les commandes ci-dessous utilisent des valeurs d’exemple ; les identifiants restent dans l’environnement privé.

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

L’accueil présente les 20 titres les plus diffusés sur toutes les radios ou sur une radio choisie, sur 24 heures, 7 jours (par défaut), 30 jours ou tout l’historique. Le classement utilise les passages réellement collectés et s’actualise toutes les 30 secondes lorsque la page est visible. Les pochettes proviennent du catalogue musical quand une correspondance est validée ; un visuel CSS prend le relais sinon. Les images restent dans un cadre carré et les numéros du podium restent superposés, y compris si une image échoue à charger. Cliquer sur un titre du classement ouvre sa fiche avec durée, fournisseur, extrait et paroles disponibles. Le bouton ▶ adjacent lance seulement l’extrait ; il est désactivé si le fournisseur n’en propose pas. Le classement et les exports de musiques regroupent les variantes de casse selon l’identité musicale persistante (`track_id`), sans fusionner des versions distinctes ni modifier les détections. La fiche ouverte depuis le classement reprend sa période et sa sélection de radios ; son lien vers les passages conserve ce périmètre et utilise cette même identité. Une fiche ouverte sans période indique explicitement « Tout l’historique ».

La recherche spécialisée est accessible sur **`/recherche`** depuis la navigation. Le lecteur radio reste monté pendant la navigation interne : passer entre classements, recherche, statistiques et administration, ouvrir les passages d’un titre ou utiliser précédent/suivant conserve le même élément audio et le même flux. Une recharge complète de l’onglet ou un lien ouvert dans un autre onglet crée un nouveau lecteur.

## Cache du navigateur et catalogue musical

Chaque onglet mémorise jusqu’à 40 combinaisons de filtres dans `sessionStorage` (ou en mémoire si le stockage est refusé). Revenir à un filtre affiche immédiatement son résultat connu. Pendant 60 secondes, ce retour évite une requête ; les actualisations automatiques continuent à interroger uniquement notre API, toutes les 30 secondes pour le classement et toutes les 10 secondes pour la recherche lorsque le direct est activé. Un résultat mémorisé plus ancien peut s’afficher pendant sa mise à jour ou une panne, jusqu’à 5 minutes. Les réponses identiques ne reconstruisent pas les lignes. Les fichiers CSS/JS portent une empreinte de contenu : une nouvelle version invalide également ce cache.

Le collecteur enrichit les musiques **en arrière-plan**, indépendamment des filtres et de la collecte radio. Il traite au plus **2 morceaux**, puis attend **20 secondes**. Chaque morceau essaie Deezer, puis iTunes si aucune correspondance unique sûre n’est trouvée. Une correspondance Deezer peut demander sa fiche détaillée et les genres de son album ; les albums sont mémorisés pendant 24 heures, avec au plus 128 entrées. LRCLIB est ensuite interrogé pour les paroles. Les 100 titres du classement global des 7 derniers jours passent en priorité ; cette priorité est recalculée chaque minute. Le reste de l’historique est inscrit dans la file par lots de 200 passages, sans charger la base entière. Une erreur d’enrichissement n’arrête pas la collecte des radios.

Les résultats et tentatives par fournisseur sont conservés dans SQLite. Une absence ou ambiguïté définitive chez Deezer et iTunes arrête les recherches automatiques de la fiche. Les paroles ont leur propre état : un résultat absent ou ambigu attend une validation manuelle. Les erreurs temporaires sont réessayées après 15 minutes, uniquement pour les étapes non résolues ; elles ne retirent pas un enregistrement déjà validé. Une fiche complètement traitée n’est plus recherchée périodiquement. L’administrateur peut relancer explicitement l’automatisme. Une réponse HTTP 429 ou 503 suspend les requêtes vers ce fournisseur selon `Retry-After` (15 minutes par défaut, entre 1 minute et 24 heures). L’extrait validé est résolu au clic, avec un cache de 60 secondes, pour renouveler les URL Deezer temporaires. L’audio est lu depuis le fournisseur, sans être stocké par le serveur.

Un identifiant interne stable est calculé à partir de l’artiste et du titre, avec normalisation de la casse, des accents et des espaces. Un même titre sur plusieurs radios partage sa fiche ; chaque passage reste conservé. L’identifiant de l’enregistrement est son ISRC lorsque disponible, sinon `deezer:<id>` ou `itunes:<id>`. Il permet de repérer les libellés qui renvoient au même enregistrement. **Les classements continuent à regrouper par artiste/titre collectés** : aucune fusion approximative ou suppression des passages n’est effectuée.

La sélection automatique exige un seul candidat correspondant à l’artiste et au titre. Les mentions remix/live/radio edit ne sont pas retirées pour forcer un rapprochement. Plusieurs candidats, un invité différent, un artiste inconnu ou un résultat douteux restent à vérifier. Les paroles ne sont automatiquement liées qu’en cas de correspondance unique avec une durée à deux secondes près.

L’administration s’ouvre sur **`/admin`**. La clé `ADMIN_TOKEN` sert uniquement à se connecter : elle n’est conservée ni dans `localStorage`, ni dans `sessionStorage`, ni dans une variable globale. Le serveur crée une session révocable dans SQLite et envoie un identifiant opaque dans un cookie `__Host-radio_admin`, `HttpOnly`, `Secure`, `SameSite=Strict`, limité au site et au chemin `/`. Le cookie dure la session du navigateur ; le serveur impose en plus une expiration absolue de **8 heures** par défaut (`ADMIN_SESSION_HOURS`, de 1 à 24). La table contient uniquement le hash de l’identifiant, un jeton CSRF et une signature liée à la clé serveur. Changer `ADMIN_TOKEN` invalide les sessions existantes. Au plus 20 sessions sont conservées ; elles sont exclues des exports de sauvegarde.

La page **`/admin`** est réservée à la connexion. Une session ouverte mène à **`/admin/dashboard`**, qui présente la collecte active, les détections sur 24 heures, les titres visibles et les fiches à vérifier. Les indicateurs musicaux excluent les titres masqués personnellement et globalement avant les calculs. Les outils disposent de leurs propres pages : **`/admin/radios`**, **`/admin/diffusions`**, **`/admin/catalogue`** et **`/admin/exclusions`**. Une sous-page protégée renvoie à la connexion puis retrouve la destination demandée ; la navigation conserve le lecteur radio et la session.

Les radios et les exclusions se recherchent par texte et sont triées par nom/titre. Les fiches musicales disposent d’un tri par titre, artiste, détections récentes ou priorité de vérification. Le catalogue complet est affiché par défaut ; activez « Fiches à vérifier uniquement » pour limiter la liste aux correspondances ou paroles à examiner. La carte correspondante du tableau de bord ouvre directement ce filtre.

Dans **Diffusions**, recherchez un titre ou un artiste, choisissez une radio, une période et un tri. Les dates sont saisies et affichées dans le fuseau de l’appareil, puis échangées en UTC. Le filtre « Passages rapprochés uniquement » signale un même titre sur une même radio dans les trois minutes suivant son précédent passage, même si un autre titre s’intercale. Il s’agit d’un signal de contrôle, sans suppression automatique. Décocher ce filtre et rechercher le titre permet de sélectionner également son premier passage. Les données brutes et la dernière observation sont consultables dans chaque ligne.

Le bloc **Contrôler les doublons** analyse tout l’historique en base de toutes les radios, y compris celles dont la collecte est arrêtée. Il peut être limité à une radio, une période ou un motif. Les archives externes ne sont pas relues. Les exclusions personnelles et globales sont appliquées avant les résultats et les bilans par radio ; le contexte des passages intercalaires reste pris en compte pour ne pas transformer un titre masqué en fausse succession.

L’audit distingue quatre motifs : même horaire d’événement source sur la même radio et le même titre ; retour avant la durée de la version sélectionnée moins une marge de **15 secondes** ; même titre consécutif ; passages rapprochés sans durée connue, avec un seuil de secours de **180 secondes**. La marge est réglable de 0 à 120 secondes, le seuil de secours de 10 à 900 secondes. Les motifs peuvent se cumuler. Les durées absentes ou invalides restent inconnues. Des horaires source distincts sont indiqués : la durée et la succession sont des indices, jamais une décision de suppression, notamment pour les versions radio et les fondus.

Chaque signalement présente les deux diffusions, leur écart, la durée, le seuil et les données source. **Préparer le regroupement de cette paire** sélectionne les passages dans le formulaire existant avec le plus ancien de la paire à conserver par défaut. L’aperçu signé et la confirmation restent obligatoires. Si le regroupement concerne le passage courant du collecteur, sa référence est rattachée au passage conservé pour éviter une réinsertion au cycle suivant. L’analyse elle-même n’écrit aucune donnée et ne change pas la collecte. La pagination conserve un identifiant de fin d’historique pour éviter que les nouvelles collectes ne déplacent les pages ; une nouvelle analyse ou une correction actualise cet instantané.

Après sélection sur la page courante, choisissez une action et cliquez sur **Prévisualiser les modifications** :

- **Corriger une diffusion** : artiste, titre et, facultativement, date de détection. L’identité musicale et la fiche correspondante sont actualisées ; la source brute reste intacte.
- **Regrouper les doublons sélectionnés** : même identité musicale et même radio obligatoires. Choisissez la diffusion à conserver ; sa date et sa source sont conservées, sa dernière observation reprend la plus récente du groupe. Les autres diffusions sélectionnées sont supprimées uniquement après confirmation.
- **Supprimer la sélection** : suppression définitive des seules diffusions affichées dans l’aperçu, sans effacer les fiches musicales, les radios ou les exclusions.

La confirmation exige un aperçu signé, valable dix minutes, dont les données correspondent encore à la base. Une nouvelle observation par le collecteur ou une autre correction peut imposer de refaire l’aperçu. Les protections de session et CSRF s’appliquent aux deux étapes. Les caches de statistiques et de recherche du processus sont invalidés après modification.

Pour **Mouv’**, le collecteur conserve désormais la réponse Radio France et normalise `now.startTime` lorsqu’il est disponible (epoch en secondes/millisecondes ou ISO avec fuseau). Comme pour Fun Radio, un événement source ancien ou déjà enregistré ne crée pas de nouveau passage ; un horaire source ultérieur permet une vraie rediffusion, même consécutive. La décision s’appuie sur la base et subsiste après redémarrage. Une disparition temporaire de l’horaire sur un libellé inchangé ne crée pas de doublon. Les écritures de collecte sont sérialisées par radio sur PostgreSQL ; SQLite reste prévu pour un seul processus collecteur.

L’état de collecte conserve aussi le dernier horaire source connu indépendamment du libellé courant. Une transition vers une annonce sans horaire, blacklistée ou non, ne remet donc pas ce repère à zéro : le retour d’un événement déjà observé ou plus ancien est ignoré. Ce repère persiste après redémarrage ; un événement daté plus récent et les transitions des sources sans horaire restent enregistrés normalement. Lors de la reprise d’un ancien état blacklisté sans horaire, le dernier passage enregistré fournit le repère si sa réponse brute le contient. Cette protection ne supprime ni ne regroupe les données historiques.

Le diagnostic local ne prouve pas l’origine des cas « Ossau-Iraty » et « Ghetto Love Story » du 20/10/2026 : leurs anciennes métadonnées ne conservaient pas l’horaire source. Le collecteur lisait déjà la dernière diffusion au démarrage ; une réinsertion systématique après redéploiement n’est donc pas établie. Des réponses source anciennes alternant avec les courantes constituent un mécanisme reproductible, désormais filtré si l’horaire est fourni. La réponse publique examinée pendant cette intervention indiquait `startTime: null` pour l’habillage antenne. Sans horaire source, aucun seuil arbitraire ne peut distinguer avec certitude un doublon d’une rediffusion légitime : les anomalies historiques restent à examiner et corriger explicitement dans le panneau.


La connexion demande une origine du même site et un en-tête dédié. Les modifications et la déconnexion exigent la session, un jeton CSRF synchronisé et une origine concordante ; les réponses administratives sont `no-store`. Cette protection suit le [modèle de jeton synchronisé OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#synchronizer-token-pattern). Les tentatives de connexion sont limitées à 5 par adresse réseau observée sur 15 minutes, avec un plafond global de 100 et un stockage mémoire borné (un seul worker API). Les tokens `Authorization: Bearer …` invalides consomment le même quota, partagé entre les routes administratives et le formulaire de connexion. Une fois le quota épuisé, ces deux modes renvoient `429` avec `Retry-After` jusqu’à expiration de la fenêtre. Les appels Bearer valides ne consomment pas le quota. Les compteurs sont réinitialisés au redémarrage. Derrière un proxy non déclaré de confiance, cette adresse peut être celle du proxy. HTTPS reste nécessaire en public ; localhost sert au développement. Le déploiement HTTP public conserve l’administration désactivée.

La session fonctionne entre classement, recherche, Stats et `/admin/catalogue`, ainsi qu’après rechargement. Les fiches affichent **Modifier** uniquement après vérification de la session. Ce bouton ouvre le même composant de correction que la file administrative, sans formulaire de clé dans la fiche. La barre supérieure indique le mode admin et permet une déconnexion qui révoque la session côté serveur. Les clients serveur utilisant explicitement `Authorization: Bearer …` restent compatibles ; l’interface navigateur n’utilise plus ce mode.

La file de vérification de `/admin/catalogue` permet de rechercher les fiches par artiste/titre, de filtrer celles à vérifier et de parcourir les résultats par pages de 25. Les correspondances douteuses passent d’abord, puis les fiches sont classées par nombre de détections sur les sept derniers jours. Ouvrir une fiche permet de modifier la recherche, choisir Deezer, Apple Music via iTunes ou Spotify, choisir les paroles LRCLIB, retirer une mauvaise correspondance ou relancer l’automatisme. Les propositions de morceaux et de paroles sont séparées et ne sont plus exposées par la fiche publique. Une recherche manuelle suspend l’automatisme ; un choix manuel et les métadonnées brutes des radios sont conservés. Changer d’enregistrement retire les anciennes paroles. Une révision vérifiée atomiquement empêche un résultat automatique tardif d’écraser une intervention administrateur.

L’administration nécessite un `ADMIN_TOKEN` non vide et HTTPS sur un site public. `compose.production.yaml` reprend cette variable depuis l’environnement privé du serveur ; une valeur vide désactive la connexion. Aucune clé n’est ajoutée automatiquement au serveur. Deezer et l’API de recherche iTunes ne nécessitent pas de secret dans cette intégration. Spotify est intégré pour la recherche manuelle et la résolution du premier résultat depuis une fiche : configurer `SPOTIFY_CLIENT_ID` et `SPOTIFY_CLIENT_SECRET` dans le `.env` privé ou dans l’environnement serveur. Le jeton OAuth est obtenu côté serveur, conservé uniquement en mémoire jusqu’à son expiration avec une marge de 60 secondes, puis renouvelé. Une réponse 401 provoque un seul renouvellement et nouvel essai ; un refus 403 n’est pas réessayé et ne devient pas un faux résultat « morceau absent ». La recherche utilise au maximum 10 résultats et ne fabrique pas d’extrait si Spotify n’en fournit pas. Les identifiants et jetons ne sont jamais renvoyés au navigateur. La collecte automatique utilise Deezer puis iTunes ; Spotify reste réservé aux recherches manuelles. Une authentification Spotify réussie suivie d’un refus 403 indique que l’application ne dispose pas de l’accès au catalogue demandé. Voir la [documentation OAuth Spotify](https://developer.spotify.com/documentation/web-api/tutorials/client-credentials-flow). Aucun identifiant réel n’est versionné ; seules les variables vides figurent dans `.env.example`.

Les cartes des radios sont centrées sur la pochette du morceau observé, avec une note musicale si elle est absente ou en erreur. Le logo officiel reste un repère de station. La commande lecture/pause est distincte de la pochette : ouvrir les détails du morceau ne lance pas le flux radio. Le lecteur affiche la pochette du morceau validé dans le catalogue, puis l’image officielle de la radio si la pochette est absente ou ne charge pas. Les extraits utilisent également leur pochette. Pour une nouvelle radio, renseigner `website_url` dans `config/stations.json` : le serveur détecte automatiquement `og:image`, `twitter:image`, puis une icône du site, sans maintenir une liste de logos. Sans `website_url`, il essaie `metadata_url` si cette page contient du HTML. La découverte limite la réponse à 256 Kio, vérifie les destinations publiques et conserve le résultat en mémoire 24 heures (15 minutes en cas d’échec). Une image indisponible laisse la note musicale. La migration `0005` ajoute le site officiel ; appliquer `alembic upgrade head`, puis `python scripts/seed.py` pour synchroniser les sites configurés.

Dans la fiche, « Masquer ce titre » se trouve dans le pied de fenêtre à droite. Le lien « Rechercher sur Spotify » ouvre directement la recherche publique Spotify, sans déclencher de résolution API à l’ouverture. La route `/api/catalog/spotify-match` est désormais réservée à l’administration ; le fournisseur reste accessible dans son sélecteur de recherche. Les identifiants Spotify restent côté serveur.

Les sauvegardes `scripts/transfer.py` comprennent le catalogue, les propositions, les paroles et les choix manuels. Les anciennes sauvegardes sans catalogue restent importables. La migration `0004` crée uniquement une table de métadonnées et conserve les passages existants.

## Compléter les informations avec un second catalogue

Un enregistrement Deezer reste l’enregistrement principal même si iTunes fournit ses genres manquants, et réciproquement. Lors de l’identification initiale, le collecteur essaie le second catalogue lorsque les genres sont définitivement absents du premier, dans les mêmes petits lots qu’avant. Les absences et ambiguïtés sont mémorisées ; les erreurs temporaires restent réessayables. Aucun appel supplémentaire n’est déclenché à l’ouverture d’une fiche.

Après sélection d’une version, **Relancer la recherche automatique** met en file une recherche de compléments auprès de Deezer, iTunes et MusicBrainz, en excluant le fournisseur principal. Spotify et Last.fm y participent seulement si leurs identifiants serveur sont configurés. Cette relance fonctionne même si le genre est déjà renseigné. Elle utilise le titre et l’artiste de la version choisie et conserve son identité, ses champs remplis et ses paroles. Sans version choisie, la relance conserve le parcours d’identification initial.

La recherche peut compléter album, durée, pochette, extrait, genres, date de sortie, invités et ISRC effectivement fournis. Les liens des fournisseurs compatibles sont ajoutés aux sources complémentaires. Une pochette ou une date d’une autre édition ne complète pas l’album choisi. Pour un résultat MusicBrainz compatible, Cover Art Archive peut fournir la pochette de l’édition correspondante. Les absences, ambiguïtés et fournisseurs non configurés ne sont pas interrogés en boucle ; seules les étapes en erreur temporaire sont réessayées après 15 minutes. Une nouvelle relance explicite remet les tentatives à zéro. Le traitement garde les lots de deux fiches et le contrôle de révision qui protège une correction concurrente. Les titres blacklistés ne sont pas enrichis et leur historique reste conservé.

Le rapprochement exige une correspondance unique artiste/titre/version/invités et une durée à deux secondes près ; deux ISRC fournis doivent être identiques. Sans durée, un ISRC commun est nécessaire. Les champs déjà présents et le choix d’enregistrement ne sont pas remplacés. Une date de sortie issue d’un autre album ou d’une autre édition n’est pas attribuée à l’album principal. Le complément initial limité aux genres peut aussi fournir album, date de sortie et invités ; la relance explicite utilise les champs élargis décrits ci-dessus. `field_sources` conserve la source de chaque champ et `additional_sources` les liens des enregistrements secondaires validés. La fiche présente cette provenance dans ses métadonnées.

Pour le complément uniquement, un crédit avec collaborateurs explicites séparés par ` & ` est aussi accepté lorsque l’artiste principal est identique et placé en premier, avec titre complet, album et durée concordants. Par exemple, **Tayc — MAMAN PRIE** sur Deezer peut recevoir le genre d’**iTunes : Tayc & RnBoi**, sans remplacer son identité Deezer. Cette exception exige un album et une durée renseignés des deux côtés et une correspondance unique ; elle ne modifie pas la sélection automatique de l’enregistrement principal.

Dans **Modifier**, chaque proposition utile porte désormais l’action **Compléter les informations manquantes**. Elle utilise exclusivement les données de ce résultat enregistré, sans recherche supplémentaire. Les champs absents peuvent recevoir album, durée, pochette, extrait, genres, date de sortie, invités et ISRC si la proposition les fournit. Les valeurs déjà renseignées, l’identité et le fournisseur de la version choisie, ainsi que les paroles sont conservés. Aucun ISRC absent n’est inventé. Un lien d’un autre fournisseur est ajouté aux sources complémentaires au lieu de remplacer le lien principal. L’action disparaît lorsqu’une proposition n’apporte plus rien ; elle exige qu’une version soit déjà choisie.

`POST /api/catalog/{track_id}/complete-candidate` reçoit seulement l’identifiant de la proposition et la révision de la fiche. Le serveur vérifie la session, le jeton CSRF, la présence de la proposition enregistrée et la révision avant d’appliquer les ajouts. Une fiche modifiée entre-temps provoque un refus 409. Les genres et les caches concernés sont actualisés. L’ancienne route `/complete` reste disponible pour les intégrations existantes ; son bouton générique est retiré de l’interface. Les actions **Voir les propositions de paroles enregistrées**, **Retirer la correspondance** et **Relancer la recherche automatique** sont regroupées.

Les genres apparaissent sous forme de liste dans l’éditeur, les propositions et la fiche musicale. Une absence produit une seule indication, « Aucun genre renseigné », sans blocs séparés pour genre principal, genres secondaires et source des genres.

Les boutons d’extrait des propositions utilisent `GET /api/catalog/{track_id}/candidates/{candidate_id}/preview`, réservé aux admins et limité aux résultats enregistrés. Comme la lecture de la version choisie, cette route renouvelle le lien Deezer avant la redirection ; écouter n’applique aucune sélection. Lorsqu’un extrait manquant provient d’une proposition complémentaire, la fiche conserve son fournisseur et son identifiant exacts pour lire cet extrait, y compris entre deux versions d’un même fournisseur. La migration **0008** remet uniquement les anciennes correspondances automatiques sans genre dans cette file ; les fiches manuelles et les morceaux absents restent inchangés.

## Fiche musicale

La fiche présente d’abord le titre officiel et l’artiste, puis la pochette carrée et un résumé (album, durée, genre, source, statut, extrait). L’extrait réutilise le lecteur global et n’est résolu qu’au clic. L’ouverture de la fiche lit uniquement l’API locale.

Les cartes de l’accueil et de la recherche consultent `GET /api/stations/now-playing` en un seul lot toutes les 30 secondes, uniquement sur une page visible contenant des cartes. Elles ne lancent aucun flux audio. Le service utilise les observations déjà conservées par le collecteur ICY/JSON/HTML et les pochettes du catalogue ; ce GET ne contacte ni les radios ni les fournisseurs musicaux. Les images sont chargées à la demande. Le bandeau de la radio sélectionnée utilise le même endpoint avec `station_id`, en conservant son rythme de vérification de 20 secondes et la connexion audio existante.

Les états exposés sont `current`, `last_known`, `hidden` et `unavailable`. « En cours » signifie une observation récente : la tolérance est le maximum entre 180 secondes et `last_seen_write_seconds + 2 × poll_interval_seconds + request_timeout_seconds` (arrondi supérieur pour le timeout), pour tenir compte des écritures espacées. L’âge de l’événement source est aussi borné : durée musicale connue plus cette tolérance, entre 5 minutes et 2 heures ; à défaut, 15 minutes depuis le début source ou la première détection. Une source ancienne n’est donc pas présentée indéfiniment comme actuelle même si elle répond encore. Une collecte désactivée reste en dernier titre connu. Les dates futures sont traitées avec prudence. Le statut correspond aux informations du collecteur, sans promettre une synchronisation exacte avec la latence du flux audio.

« Dernier titre connu » porte une date d’observation, tandis que l’absence de donnée affiche « Information indisponible ». Le client fait vieillir les informations chaque seconde, sans requête supplémentaire ; une panne réseau les rabat vers le dernier titre connu. Au retour d’un onglet masqué, les dates sont réévaluées avant l’actualisation. L’API répond `Cache-Control: no-store` et lit au plus quatre requêtes SQL, quel que soit le nombre de radios, en évitant les payloads bruts et les paroles.

La dernière observation est choisie **avant** l’application des exclusions personnelles et globales, sans exception administrateur. Si elle est masquée, aucun titre, artiste, identifiant de musique ou lien de pochette n’est renvoyé pour cette carte : « Morceau masqué » et le visuel neutre prennent le relais. Le service respecte aussi les transitions blacklistées sans diffusion enregistrée (`collection_state.play_id = null`) : il ne ressort pas la musique précédente. Les exclusions ne suppriment aucun historique. Un changement de filtre efface immédiatement les informations des cartes et du bandeau, et une réponse plus ancienne ne peut pas les restaurer. Le bouton d’écoute de la station reste disponible.

Validation reproductible : `tests/test_station_now_playing.py` couvre fraîcheur, source ancienne, dates futures, absence de données, collecte arrêtée, masquages combinés, administration, références invalides, historique conservé et nombre de requêtes. `PYTHONPATH=. python tests/browser_station_cards.py` vérifie 1280/414/320 pixels, clair/sombre, changement de morceau par polling, pochettes absentes/en erreur, masquage personnel/global, réponse tardive, expiration locale et navigation. Avant une action explicite d’écoute, aucun flux audio n’est ouvert ; après cette action, les mises à jour des cartes conservent la même source audio. Les régressions de sélection des radios, de persistance et d’enchaînement des préécoutes sont couvertes par leurs scénarios existants. Données et médias synthétiques uniquement ; captures locales `/tmp/radio-station-cards/`.

La section radio compte les **détections conservées**, les radios différentes, les première et dernière dates et les trois dernières détections. Deux observations proches restent distinctes ; aucun chiffre de « diffusions estimées » n’est fabriqué. `/api/catalog/{track_id}/radio` fournit ces indicateurs, avec `station_id` facultatif pour le contexte radio. Le lien d’historique garde les filtres exacts du morceau.

Les métadonnées techniques, genres secondaires, provenance et paroles sont repliables. Les invités et la date de sortie n’apparaissent que s’ils ont été fournis par le catalogue. La section **Écouter sur une plateforme** regroupe des liens de présentation identique, avec **un seul bouton Spotify**. Au clic, JavaScript tente le schéma `spotify:` puis ouvre le lien web après **1 000 ms** si la page reste active. Le minuteur est annulé si la page devient cachée, perd le focus, est quittée ou change via la navigation interne, ainsi qu’en cas de nouveau clic Spotify. Revenir de l’application ne déclenche donc pas ensuite un départ vers le web. L’ouverture de l’application est déduite de ces événements : un site ne dispose pas d’une détection certaine de l’installation. Certains navigateurs affichent une confirmation de protocole. Un clic modifié ou l’ouverture dans un nouvel onglet utilise directement le lien web.

Avec un identifiant Spotify validé, le bouton cible le morceau exact (`spotify:track:…`, puis `https://open.spotify.com/track/…`). Sinon, il lance une recherche (`spotify:search:…`, puis `https://open.spotify.com/search/…`). Voir les [liens d’application Spotify](https://developer.spotify.com/documentation/ios/tutorials/content-linking).

Dans l’éditeur administrateur, chaque proposition présente la pochette et les informations à gauche, et **la réponse JSON reçue par l’éditeur** à droite. Sur mobile, la réponse passe sous la proposition. Cette réponse contient les données normalisées de notre API, sans jeton ni en-tête d’authentification ; elle est remplacée par la réponse d’enregistrement après « Choisir cette version ».

## Statistiques

**`/stats`**, accessible dans la sidebar, propose des tableaux et barres natives sans bibliothèque graphique :

- Répétitions sur 6, 12 (défaut) ou 24 heures : couples titre/radio ayant au moins 3 passages, avec intervalle moyen entre les détections successives de la fenêtre.
- Diversité par radio sur 24 heures : titres uniques et total des passages, y compris les radios sans données.
- Recoupement des radios configurées sur 7 jours : `titres communs / union des titres`, avec effectifs. Une radio sans données rend la comparaison non calculable.
- Décalages entre deux radios sur 24 heures : même identité musicale et détections séparées de 5 minutes au maximum. Les paires les plus proches sont retenues en premier, sans réutiliser un passage.
- Genres sur 24 heures : nombre de passages par genre principal, catégorie Inconnu et taux de couverture. iTunes fournit `primaryGenreName` ; Deezer fournit les genres de l’album lorsqu’ils sont disponibles. Aucun pays d’origine n’est déduit.

Uniquement lorsqu’elle est ouverte depuis **Stats**, la fiche d’un morceau affiche 24 barres horaires compactes sur les 7 derniers jours, dans le fuseau du navigateur, pour la radio du contexte ou toutes les radios. Les passages des deux heures répétées lors du passage à l’heure d’hiver sont additionnés dans la même heure locale. Les états des paroles distinguent attente, absence, vérification nécessaire et panne temporaire. Une mention d’invité omise par LRCLIB peut être rapprochée de l’enregistrement validé, mais un invité contradictoire, une autre version ou une durée à plus de deux secondes ne sont pas validés automatiquement.

Les chiffres reposent sur les **détections collectées**, pas sur les horaires exacts d’antenne. Les fenêtres sont glissantes, début inclus et fin exclusive. Les titres masqués sont exclus avant calcul. L’identité normalisée artiste/titre conserve les mentions de versions ; aucune fusion approximative n’est effectuée. Les classements historiques conservent leur regroupement d’origine.

Les routes publiques `/api/stats/repetitions`, `/diversity`, `/overlap`, `/offsets`, `/genres` et `/hourly` sous le même préfixe lisent exclusivement la base. Paramètres communs : `station_id`, `exclusions`, `page`, `page_size` (25 par défaut, 100 maximum). `hours=6|12|24` concerne les répétitions ; les décalages demandent `station_a` et `station_b` distincts ; l’activité horaire demande `track_id` et accepte `timezone`. La matrice et les distributions restent complètes ; les listes sont paginées. Le cache serveur conserve au plus 64 résultats pendant 30 secondes. Le bouton Actualiser recharge les indicateurs.

Le filtre **Genre** de `/recherche` se conserve dans l’URL et s’applique avant regroupement, pagination et export. `genre=__unknown__` inclut les fiches sans genre et les morceaux sans métadonnées. CSV et JSON comprennent le genre principal et sa source.

La migration **0006** ajoute les préférences radio, les états d’enrichissement et un identifiant de catalogue indexé aux passages. Elle renseigne l’historique par lots de 250 sans modifier les libellés. Exécuter `alembic upgrade head` avant de démarrer cette version. Les anciennes sauvegardes restent importables ; les nouvelles conservent préférences, états et tentatives. La migration **0007** crée la table des sessions administrateur. `GET /api/admin/session` vérifie la session ; `POST /api/admin/login` ouvre la session et `POST /api/admin/logout` la révoque avec protection CSRF.

## Explorer la base en direct

La page affiche un explorateur sans bouton « Filtrer » : les champs de recherche se combinent et les résultats se mettent à jour après 350 ms de frappe. Les changements de listes et de dates s’appliquent immédiatement. L’actualisation automatique vérifie les nouveaux résultats toutes les 10 secondes lorsque la page est visible, avec un cache serveur de 3 secondes maximum pour les résultats de recherche. Les radios restent collectées à leur propre cadence.

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

Le **slug est l’identifiant stable** : le conserver pour renommer une radio ou changer son flux. `enabled: false` désactive sa collecte. Retirer une entrée du fichier désactive également sa collecte, tout en conservant la station et son historique en base. Le fichier est validé avant application et la mise à jour est transactionnelle. Les champs JSON/HTML sont illustrés par les radios existantes. Le collecteur prend en compte la modification au cycle suivant. Recharger la page après un ajout ou un changement de nom pour actualiser la liste des stations. La même synchronisation a lieu au démarrage. Depuis `/admin`, la section Radios permet aussi de désactiver/réactiver uniquement la collecte. La préférence est conservée en base et prioritaire sur `enabled` du fichier pour une radio encore configurée. « Revenir à la configuration » supprime cette préférence et applique la valeur du fichier. Retirer une radio du fichier désactive toujours sa collecte. L’écoute et l’historique restent accessibles. Les routes `/api/admin/access`, `/api/admin/stations`, `/api/admin/catalog` et `/api/admin/catalog/{track_id}` exigent une session valide (ou un token explicite pour les clients serveur) ; `PATCH /api/admin/stations/{id}` accepte `{"enabled": false}`, `true`, ou `null` pour revenir au fichier.

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

Pour Fun Radio, les attributs HTML sont lus avec les deux styles de guillemets. La collecte choisit la carte ayant le `start` le plus récent sans dépasser l’heure de la requête ; une carte sans horodatage n’est pas enregistrée. Le JSON brut conserve `singer`, `title` et `start` (millisecondes Unix), ce qui permet de rejeter une réponse antérieure au dernier événement enregistré, même après redémarrage. Une nouvelle diffusion du même titre avec un nouvel horodatage reste distincte. Les dates affichées restent celles de détection locale. Ce correctif ne supprime ni ne réécrit les anciennes détections erronées ; les anciennes lignes sans horodatage source ne permettent pas une déduplication certaine.

## Protections

Les radios sont gérées dans `config/stations.json`, synchronisé au démarrage et par le collecteur. Garder `.env` privé ; il est exclu de Git et de l’image Docker.

Les flux doivent utiliser HTTP(S), sur le port 80 ou 443, sans identifiants dans l’URL. Le serveur refuse les IP locales, privées et réservées, y compris après résolution DNS ou redirection. Les connexions sont faites à l’IP publique vérifiée, avec le nom TLS et l’en-tête Host d’origine. Chaque origine a son propre pool de connexions.

Les réponses sont limitées à 512 Kio, les intervalles ICY à 64 Kio, les redirections à cinq et la durée totale à 12 secondes par collecte. La compression distante est refusée pour éviter les bombes de décompression. Les requêtes d’écriture sont limitées à 8 Kio et le nombre de radios à 50 par défaut. Une destination non conforme reste écoutable côté navigateur, mais sa collecte échoue et utilise un délai progressif avant nouvel essai.

## Collecte et consommation

Trois radios au maximum sont interrogées simultanément. Les sessions SQL sont fermées pendant les appels réseau. Le client HTTP est partagé et les écritures de présence sont espacées de 120 secondes ; `last_seen_at` peut donc avoir ce retard. Un titre différent crée toujours immédiatement un nouveau passage.

Les sources disponibles sont `icy`, `json` et `html`. Les radios livrées sont Skyrock, NRJ, Europe 2, Fun Radio et Mouv’. Leurs sources sont configurées dans `config/stations.json`. Les services tiers peuvent changer ou refuser leurs endpoints : les erreurs d’une station ne doivent pas arrêter les autres. Le seed synchronise la configuration manuelle au démarrage, sans supprimer les stations ou les passages existants.

Les classements disposent d’un cache de 30 secondes, limité à 64 entrées, et les passages d’un index global sur leur date. Les logs Docker sont limités à deux fichiers de 5 Mo par service. L’image de production ne contient ni outils de test, ni pilote PostgreSQL, ni les dépendances optionnelles d’Uvicorn.

Les passages dont la dernière observation date de plus de `RETENTION_DAYS=180` jours sont archivés dans `data/archives/*.jsonl.gz`, **avant** retrait de la base active. Un échec d’écriture conserve les lignes SQL. Les archives gardent toutes les colonnes des passages ; les stations restent dans la base. Les statistiques du site portent sur la base active. Définir `RETENTION_DAYS=0` désactive l’archivage automatique. Le travail quotidien est limité à 10 000 passages pour éviter un pic CPU sur une grande base. Les archives s’accumulent : prévoir sauvegarde et capacité disque.

## API

Documentation interactive : http://127.0.0.1:8080/docs

- `GET /api/explore?view=plays&artist=etoile&page_size=50` (mêmes filtres que l’interface ; `view=tracks` pour les musiques)
- `GET /api/version` : révision de l’image et empreinte des fichiers statiques
- `GET /api/catalog?artist=Mosimann&title=Soon` : fiche et propositions stockées, sans recherche externe
- `GET /api/catalog/{track_id}/preview` : redirection vers l’extrait de la version validée
- `POST /api/catalog/{track_id}/search` : recherche authentifiée, corps `{ "provider": "deezer", "artist": "Mosimann", "title": "Soon" }` (`itunes` et `lrclib` disponibles)
- `POST /api/catalog/{track_id}/correction` : choix authentifié via `candidate_id`, retrait via `null`, ou relance via `{ "automatic": true }`
- `GET /api/stations`
- `GET /api/stations/now-playing` : observations des cartes, `station_id` et `exclusions` facultatifs ; aucun appel aux fournisseurs
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

## Titres masqués et exports filtrés

Sans connexion admin, dans une fiche musicale, « Masquer pour moi » ajoute le couple artiste/titre à la liste de cet appareil. « Titres masqués » permet de réafficher un titre ou de saisir un titre et son artiste exacts. Aucun raccourci spécifique à une radio ou à une annonce n’est codé en dur. Un artiste vide cible les passages sans artiste. La correspondance ignore la casse et les accents.

La liste est conservée dans `localStorage`, jusqu’à 50 titres ; si le stockage est indisponible, elle reste en mémoire pour la page courante. Elle ne supprime pas les passages collectés. Elle est transmise à notre API via le paramètre `exclusions`, puis appliquée en SQL **avant** les regroupements, le classement et la pagination. Elle s’applique au classement, à la recherche rapide, à la recherche avancée, aux statistiques (y compris diversité) et aux exports. Le masquage reconnaît les apostrophes droites et typographiques, notamment `Mouv'` et `Mouv’`, sans fusionner les identités musicales ni modifier l’historique brut. Les caches distinguent chaque liste pour éviter de réafficher des titres masqués. Il s’agit d’un filtre de présentation personnel, pas d’un contrôle d’accès aux données.

Les liens **CSV** et **JSON** exportent tous les résultats des filtres actifs, dans l’ordre choisi, et non uniquement la page visible. Sur le classement, ils exportent les musiques correspondant à la radio et à la période choisies, avec un tri par fréquence. La limite est de **10 000 lignes** ; si elle est dépassée, l’API demande de préciser les filtres. L’export fixe un instantané des identifiants et lit les lignes par lots de 50. Le CSV utilise UTF-8 avec BOM et `;` pour les tableurs français ; les textes pouvant être interprétés comme des formules sont neutralisés. Le JSON conserve le texte original. Les fichiers comprennent les métadonnées validées disponibles et un lien Spotify par titre.

Exemples : `GET /api/export?view=tracks&artist=Mosimann&format=csv` ou `GET /api/export?view=plays&station_id=1&since=2026-10-01T00:00:00Z&format=json`. Les dates, le tri, les filtres de source et d’artiste, ainsi que `exclusions`, suivent les mêmes règles que `/api/explore`. Aucun appel fournisseur n’est effectué pendant l’export.

## Spotify sans accès Web API

Chaque fiche propose « Rechercher sur Spotify », un lien ouvrant la recherche normale de Spotify à partir de l’artiste et du titre. Les exports contiennent également ce lien, ou le lien du morceau lorsqu’une correspondance Spotify est déjà validée. Cette ouverture ne nécessite ni jeton de notre application ni accès développeur Premium, et laisse l’utilisateur choisir la bonne version et l’ajouter à sa playlist dans Spotify.

La création automatique de playlists reste tributaire de la Web API et d’une autorisation OAuth utilisateur avec les droits `playlist-modify-private` ou `playlist-modify-public`. La [documentation des quotas](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) impose un compte Premium au propriétaire d’une application en mode développement : connecter un autre utilisateur ne lève pas cette condition. Le [Web Playback SDK](https://developer.spotify.com/documentation/web-playback-sdk/tutorials/getting-started) demande également Premium pour la lecture. Les SDK Android/iOS pilotent l’application Spotify et l’Ads API sert à la publicité ; ils ne remplacent pas l’accès Web API pour rechercher et créer des playlists. Aucun écran de connexion ou bouton de création automatique de playlist n’est ajouté tant que cet accès est bloqué. Deezer, iTunes et LRCLIB restent les fournisseurs utilisables pour les métadonnées et aperçus.

## Recherche rapide

Le raccourci de la barre latérale et `Ctrl+K` / `⌘K` ouvrent une fenêtre centrée. Elle recherche les morceaux collectés, par titre ou artiste, après 250 ms de pause et à partir de deux caractères. Les mots peuvent se répartir entre artiste et titre : « Mosimann Soon » retrouve le morceau, quel que soit leur ordre. Elle affiche jusqu’à 12 résultats, avec pochette, fiche au clic et aperçu disponible via ▶. « Voir tous les résultats » ouvre la recherche avancée avec la requête conservée. La saisie interroge notre API et son cache navigateur, sans appeler Spotify, Deezer ou iTunes. Fermer la fenêtre et ouvrir une fiche conservent la lecture. Démarrer explicitement un extrait remplace la radio dans le lecteur commun ; son volume reste inchangé. La recherche conserve son texte et ses résultats à la fermeture et propose « Retour à la recherche » depuis une fiche ouverte dans ses résultats. Les fiches et fenêtres se ferment aussi par Échap ou un clic à l’extérieur.

## Vérification de l’interface et du lecteur

Le même lecteur audio sert au direct radio et aux extraits, avec les mêmes réglages de volume et de sourdine. Un extrait remplace le direct dans le lecteur ; la barre affiche sa progression réelle, le temps écoulé et sa durée, avec déplacement dans l’extrait. « Reprendre la radio » relance le dernier flux sélectionné. Le passage entre pages, la fermeture d’une fiche ou de la recherche n’interrompt pas l’extrait. Le lecteur dispose de commandes lecture/pause, sourdine et volume sur ordinateur. Sur mobile, il privilégie les informations du morceau, lecture/pause et sourdine ; le volume se règle avec les boutons de l’appareil. La barre réserve l’espace nécessaire et la zone de sécurité en bas d’écran. Le thème clair/sombre continue de fonctionner pendant la navigation.

Un contrôle navigateur reproductible utilise une base temporaire, un serveur sur un port libre, une source WAV simulée et des images simulées. Il ne démarre pas le collecteur et ne modifie ni la base locale ni le VPS. Il vérifie les formats **320×740**, **402×874**, **412×915** et **1280×900**, les cadres carrés et rangs du podium, l’absence de débordement horizontal, la lecture réelle pendant les liens internes et l’historique du navigateur, les commandes et les erreurs de navigation. Il vérifie aussi Stats et Administration, la connexion, la session persistante pendant la navigation, la déconnexion et la non-persistance de la clé, les préférences radio, les 24 barres horaires et le lecteur commun aux extraits, le déplacement dans une piste, le volume conservé, le retour à la recherche, la fermeture par clic extérieur, le masquage/réaffichage et les téléchargements filtrés.

```bash
# Dans l’environnement de développement, pour ce contrôle facultatif :
pip install playwright==1.64.0
python -m playwright install chromium
PYTHONPATH=. python tests/browser_check.py
# Pour étendre aux moteurs installés avec leurs bibliothèques système :
# RADIO_BROWSER_ENGINES=chromium,webkit PYTHONPATH=. python tests/browser_check.py
```

Le test Chromium aux dimensions d’un téléphone ne remplace pas un essai sur un appareil physique. Les captures sont écrites dans `/tmp/radio-chromium-<largeur>.png`. Une erreur de chargement d’une page conserve le lecteur et affiche un message ; un changement de version du site invite à recharger après l’écoute pour éviter de mélanger des scripts de versions différentes.

## Déploiement automatique sur le VPS

Les responsabilités sont séparées :

- `.github/workflows/ci.yml` exécute les contrôles Python/JS, l’audit, les migrations et le démarrage de l’image Docker ; il ne publie aucune image et ne possède aucun code SSH.
- `.github/workflows/cd.yml` est un workflow réutilisable appelé uniquement après succès de la CI sur un push de la branche par défaut. Il publie puis déploie exactement le commit validé, épinglé par digest. Une pull request ou un échec des tests ne lance pas le CD.

Après un push sur la branche par défaut (actuellement `master`), la CI appelle donc le CD. Son job `deploy`
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

### Configurer les fournisseurs optionnels

Définir `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` et/ou `LASTFM_API_KEY` dans le `.env` privé du serveur ou dans l’environnement des services qui les utilisent. `track.search` de Last.fm ne demande pas de secret partagé. Deezer, iTunes et MusicBrainz restent utilisables sans ces identifiants.

Les workflows actuels publient l’image et déploient la configuration runtime ; ils ne transmettent pas automatiquement les secrets Spotify ou Last.fm des paramètres GitHub au serveur. Créer un secret GitHub seul ne suffit donc pas à configurer ces fournisseurs. Après une modification du `.env` serveur, recréer les services concernés pour charger les nouvelles valeurs. Une configuration existante est préservée lors d’un déploiement.

Les valeurs ne doivent figurer ni dans le dépôt, ni dans les commandes partagées, ni dans les journaux. Une réponse Spotify 403 indique un refus d’accès au catalogue ; renouveler un secret ne garantit pas de lever ce refus. Les liens de recherche Spotify de l’interface restent utilisables sans accès développeur.

### Installation et mises à jour du serveur

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

## Vérification du déploiement

La route `/api/version` expose la révision de l’image et l’empreinte des fichiers statiques. Comparer cette révision au commit validé par la CI et au workflow de déploiement. Les URL des assets portent aussi une empreinte de contenu pour éviter de mélanger les versions. Une différence visuelle peut provenir du thème ou de la largeur d’écran ; le statut du workflow et les contrôles de santé restent les preuves du déploiement.

## Masquage global et personnel

Une fois connecté en admin, les boutons **Masquer pour tous** et la saisie manuelle modifient une blacklist globale persistante. Elle s’applique à tous les visiteurs et aux admins avant les classements, statistiques, recherches, résumés de diffusions et exports. Les accès directs aux fiches masquées sont réservés aux admins ; les statistiques et résumés publics restent filtrés même avec une session admin. Les données historiques sont conservées et restent consultables dans les sections administratives. Les collecteurs n’enregistrent plus de nouveaux passages blacklistés et ne lancent pas leur enrichissement.

Dans **Mes exclusions personnelles sur cet appareil**, le bouton **Appliquer mes N exclusions personnelles à tous** ajoute toute la liste personnelle à la blacklist globale en une opération. Les doublons sont ignorés et les préférences de l’appareil restent conservées. Cette action est réservée aux administrateurs ; aucune conversion n’a lieu automatiquement à la connexion.

Sans connexion admin, le masquage reste personnel dans `localStorage` (50 titres maximum). Ces préférences restent actives avant, pendant et après une session admin et s’ajoutent à la liste globale : réafficher globalement un titre ne retire pas les exclusions personnelles. Les anciennes préférences ne sont jamais transférées automatiquement. L’admin peut les retirer dans une section distincte de **Titres masqués pour tous**, accessible aussi depuis l’administration.

Les titres masqués personnellement ou globalement sont retirés **avant** les calculs de répétitions, intervalles moyens, diversité, recoupement, décalages, genres et activité horaire. Ils ne contribuent ni aux effectifs, ni aux pourcentages, ni aux graphiques. Les deux côtés d’une comparaison entre radios sont filtrés. Les changements de session réactualisent les résultats et les réponses en cours devenues obsolètes sont ignorées.

La migration `0009` crée une liste globale vide. La migration `0010` ajoute `stations.collection_state`, un état courant sans historique des événements blacklistés. Il permet de conserver les transitions morceau → annonce exclue → même morceau, ainsi que le contrôle des horaires source, après redémarrage. Sans cet état, ignorer l’annonce pourrait faire perdre une vraie rediffusion du même morceau. Le retour d’un titre retiré de la blacklist est enregistré au prochain passage observé ; les diffusions manquées ne sont pas reconstituées. Exécuter `alembic upgrade head` avant de démarrer la nouvelle version (le déploiement existant réalise déjà cette étape). Les sauvegardes JSONL incluent cette liste et les anciennes sauvegardes restent importables. Les écritures passent par `POST /api/admin/exclusions` et `DELETE /api/admin/exclusions/{id}` et `POST /api/admin/exclusions/bulk` (1 à 50 titres), protégés par la session et le jeton CSRF existants ; la lecture admin utilise `GET /api/admin/exclusions`. `GET /api/exclusions/revision` expose une empreinte publique pour les caches. Les pages vérifient cette empreinte au chargement, à la navigation, au retour sur l’onglet et toutes les 30 secondes lorsque la page est visible.

Vérification dédiée : `PYTHONPATH=. python tests/browser_global_exclusions.py` (Playwright Chromium), plus `python -m pytest tests/test_global_exclusions.py tests/test_blacklist.py` et `node --test tests/*.test.cjs`.


Contrôle navigateur ciblé de la gestion des diffusions (base temporaire, aucun accès au VPS) :

```bash
PYTHONPATH=. .venv/bin/python tests/browser_data_admin.py
```

Il vérifie sur ordinateur et mobile la connexion, la recherche des passages rapprochés, l’aperçu, l’annulation, l’application d’une suppression et son résultat en base. Chromium doit être installé pour Playwright.


Vérification ciblée du choix des versions et de la complétion : `python -m pytest tests/test_candidate_actions.py` et `PYTHONPATH=. .venv/bin/python tests/browser_candidate_actions.py`. Le scénario navigateur utilise une base temporaire, des fournisseurs simulés et un média WAV local après contrôle des redirections réelles de l’API ; il vérifie la lecture, la pause, la complétion, la conservation des données et la lecture après sélection en 1280 et 402 pixels.

## Nouveaux fournisseurs : MusicBrainz, Cover Art Archive, Last.fm et lyrics.ovh

Dans **Modifier → Fournisseur**, MusicBrainz et Last.fm s’ajoutent aux recherches musicales, et **Paroles (lyrics.ovh)** s’ajoute à LRCLIB. Ces sources sont manuelles ; la collecte et les priorités d’enrichissement automatique existantes restent inchangées. Les propositions obtenues peuvent être choisies ou servir à compléter les champs absents via les actions de phase 3. Une panne conserve la fiche et les propositions précédentes et n’empêche pas de rechercher chez un autre fournisseur.

**MusicBrainz** recherche les enregistrements avec le titre et, s’il est renseigné, l’artiste. Les termes sont échappés pour Lucene puis encodés comme paramètres HTTP. Les crédits d’artistes, durée, ISRC, album et date ne sont repris que s’ils sont fournis. Les tags libres ne sont pas inventés comme genres. Une liste incomplète reste exploitable. Les requêtes MusicBrainz et les demandes de pochettes sont espacées d’au moins 1,05 seconde par le processus API, y compris entre utilisateurs concurrents ; le déploiement utilise un seul worker API. Le `User-Agent` identifie Radio Tracker et son dépôt public. [API de recherche MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API/Search), [limites et identification](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting).

Le bouton **Charger la pochette** consulte **Cover Art Archive** pour les éditions référencées par MusicBrainz, au maximum trois. Si une édition n’a pas de pochette, la suivante est essayée ; une erreur temporaire permet de réessayer. La face avant et sa miniature de 500 pixels sont privilégiées. Les redirections JSON du service sont suivies et les anciens liens HTTP de Cover Art Archive/Internet Archive sont normalisés en HTTPS. Lorsqu’une autre édition fournit la pochette, son album, sa date et son identifiant sont conservés ensemble dans la proposition. Aucune image n’est inventée et le chargement ne change pas la version validée. [API Cover Art Archive](https://musicbrainz.org/doc/Cover_Art_Archive/API).

**Last.fm** utilise `track.search` avec `track`, `artist` si disponible, `limit`, `page` et `format=json`. Choisissez le nombre de résultats puis utilisez **Précédent / Suivant**. Les changements de critères demandent une nouvelle recherche. Les réponses sans MBID reçoivent un identifiant interne stable ; aucun MBID, ISRC, album, genre, durée ou extrait absent n’est fabriqué. Les liens Last.fm sont conservés pour consulter la source. [Documentation officielle `track.search`](https://www.last.fm/api/show/track.search).

Pour activer Last.fm, ajouter `LASTFM_API_KEY` au `.env` privé **du serveur** ou à son environnement, puis redémarrer le service API. `.env.example` contient uniquement une variable vide. `track.search` ne nécessite ni session utilisateur ni `LASTFM_SHARED_SECRET`. La clé reste côté serveur ; elle n’est jamais enregistrée dans les propositions ni renvoyée au navigateur. Sans clé, Last.fm affiche une erreur de configuration explicite et les autres sources fonctionnent. Aucun identifiant réel n’est versionné.

**lyrics.ovh** suit deux étapes : `/suggest/{recherche}` fournit des suggestions musicales issues de Deezer, puis **Charger les paroles** demande `/v1/{artiste}/{titre}` pour la suggestion choisie. Les chemins sont encodés et les liens de pagination renvoyés par le fournisseur ne sont pas suivis. Une suggestion n’est pas présentée comme contenant les paroles et ne peut pas être validée sans texte. Un 404 ou un texte vide affiche une absence ; une panne affiche une erreur réessayable. Les paroles validées conservent leur source **lyrics.ovh** dans la fiche musicale. [API et fonctionnement du fournisseur](https://github.com/NTag/lyrics.ovh#api).

Les routes admin `POST /api/catalog/{id}/candidate-artwork` et `/candidate-lyrics` acceptent uniquement l’identifiant d’une proposition enregistrée et la révision courante. Elles utilisent la session et la protection CSRF et refusent les modifications concurrentes. Aucun appel fournisseur n’a lieu pendant les lectures publiques du catalogue. Aucune migration de base n’est requise pour cette phase.

Vérification ciblée : `python -m pytest tests/test_providers.py` et `PYTHONPATH=. .venv/bin/python tests/browser_providers.py`. Les tests contrôlent les paramètres, le débit, les redirections, les réponses incomplètes, les erreurs, la pagination, l’absence de fuite de clé et les parcours navigateur en 1280 et 402 pixels. Lors de la vérification réelle du 25/10/2026, la recherche « Comme un sheguey » — GRADUR a renvoyé un enregistrement MusicBrainz et une suggestion lyrics.ovh, puis les paroles étaient disponibles. L’accès Cover Art Archive a rencontré une erreur TLS depuis l’environnement de travail ; son parcours est vérifié avec réponses simulées. Last.fm est testé avec une clé factice et des réponses simulées ; l’activation et la vérification réelles nécessitent une clé serveur configurée.

## Export personnalisable

Depuis les classements ou la recherche avancée, ouvrez **Export personnalisable**.
Les filtres courants sont repris et restent modifiables : radio, dates, artiste,
titre, genre, source, tri et filtres avancés. Choisissez les musiques regroupées
ou les passages, un nombre de résultats (vide pour tous, au maximum 10 000),
les champs et le format CSV ou JSON. Les exclusions personnelles de l’appareil
et la blacklist globale s’appliquent avant le classement et la limite, y compris
pendant une session administrateur.

Pour exporter le top 10 d’une radio avec seulement le titre et l’artiste :
choisissez la radio, **Musiques**, **Plus diffusé**, **10**, puis **Titre et artiste
uniquement**. **Tout sélectionner** permet un export complet ; le champ
`metadata` conserve l’objet des métadonnées musicales et les paroles sélectionnées.
En CSV (UTF-8 avec BOM, séparateur `;`), les listes et objets sont encodés en JSON
et les cellules pouvant être interprétées comme formules sont neutralisées.

L’API `GET /api/export` reprend les filtres de `/api/explore` et accepte
`format=csv|json`, `limit=1..10000` et `fields=title,artist` (champs séparés par
virgules, dans l’ordre voulu). Sans `fields`, tous les champs sont exportés ;
`GET /api/export/fields` décrit les champs disponibles pour chaque vue. L’export
ignore la pagination de l’écran et refuse les champs invalides, dupliqués ou
incompatibles avec la vue. Le JSON conserve l’enveloppe `view`, `snapshot_id`,
`count`, `items` ; chaque élément contient uniquement les champs choisis.

Vérification : `PYTHONPATH=. .venv/bin/pytest tests/test_exports_exclusions.py`
et `PYTHONPATH=. .venv/bin/python tests/browser_exports.py` (Chromium).

### Classement de plusieurs radios

Dans « Les plus diffusés », « Composer ma sélection de radios » permet de cocher plusieurs stations. Les diffusions des radios sélectionnées sont réunies avant le classement et les exclusions sont appliquées avant les calculs. Aucune case cochée signifie toutes les radios ; cocher une seule case limite le classement à cette station. L’URL conserve la période et la sélection, et les caches distinguent chaque groupe. L’export personnalisable reprend la même sélection et permet de la modifier.

Les API de classement, de recherche avancée et d’export acceptent `station_ids=1,3` (au maximum 100 identifiants positifs, normalisés et dédupliqués). Le paramètre historique `station_id` reste utilisable. Si les deux sont fournis, leurs filtres se combinent par intersection.

### Radios supplémentaires et horaires JSON

RFM utilise son historique officiel (`nowplaying.0.artist`, `nowplaying.0.title`, `nowplaying.0.time`). FIP utilise le lecteur musical Radio France `livemeta/live/7/transistor_musical_player` : le libellé contient artiste et titre, et `now.startTime` identifie l’événement. Hit West utilise l’API officielle `TitleDiffusions` : `0.title.artist`, `0.title.title`, `0.timestamp`. Son paramètre `date={now_ms}` est remplacé à chaque requête par l’heure UTC courante en millisecondes. Le flux M3U fourni est une liste ; le lecteur utilise son point d’entrée audio stable sans suffixe `.m3u`.

Le champ facultatif `json_started_path` permet de conserver l’horaire source indépendamment du libellé. Les dates ISO avec fuseau et les epochs en secondes/millisecondes sont normalisés ; une date future est ignorée. La réponse brute conserve l’horaire et le contenu JSON pour les contrôles administratifs. La migration `0011` ajoute cette configuration sans modifier ni supprimer les diffusions existantes.

Le flux `radiotrg-high.mp3`, également référencé par le site officiel d’ENJOY 33, est présenté sous ce nom. La lecture MP3 a été vérifiée ; aucun titre ICY exploitable n’a été renvoyé lors du contrôle initial. La station reste écoutable, et l’absence de titres n’est pas compensée par des données inventées.

Les quatre nouvelles radios utilisent des flux MP3, décodés lors des essais Chrome en formats ordinateur et mobile. RFM propose aussi un flux AAC répondant à 128 kbit/s ; le MP3 à 128 kbit/s fournit ici une configuration homogène. Le lien HLS du brouillon contient des paramètres de session et n’est pas retenu comme adresse persistante.

L’alternative Instant.audio pour RFM/Fun a renvoyé HTTP 403 avec défi d’accès pendant l’étude. Elle n’est pas utilisée comme repli automatique. Fun Radio conserve sa source actuelle et ses protections de détection ; une panne reste isolée à la station et son retour ne doit pas réinsérer un événement déjà observé.

Pour de futures découvertes, [TuneIn Music](https://tunein.com/radio/music/) propose notamment des catégories pop, rock et jazz. [Instant.audio](https://instant.audio/) référence des annuaires par pays, dont la France : cette page publique ne constitue pas un contrat d’accès à une API. Pistes proposées : une station jazz pour diversifier les genres suivis, une station rock pour comparer les répertoires, et une radio locale pour élargir la couverture régionale. Les sources officielles, la stabilité du flux, les titres horodatés et les conditions d’accès devront être vérifiés avant chaque ajout ; aucune station supplémentaire n’est ajoutée automatiquement.

Exemples de futures stations à étudier, sans ajout automatique : [Jazz Radio](https://www.jazzradio.fr/) dispose d’une rubrique « Titres diffusés », piste pour diversifier le suivi jazz/soul ; [Radio FG](https://www.radiofg.com/) propose « Retrouver un morceau », piste pour comparer les rotations dance ; [Radio Nova](https://www.nova.fr/) propose « C’était quoi ce titre », piste pour des répertoires complémentaires. Ces pages ont été consultées ; leurs flux et API n’ont pas encore été validés pour la collecte.

### Fiches et navigation mobile

Les résultats de recherche avancée affichent une petite pochette ouvrant la fiche musicale. Sans image ou en cas d’échec de chargement, une icône reste disponible. Le texte du lecteur permanent ouvre également la fiche du morceau courant, qu’il s’agisse d’une radio ou d’un extrait ; l’action est désactivée lorsqu’aucun titre n’est connu.

Sur mobile (jusqu’à 760 px), le bouton Navigation à gauche ouvre un panneau modal contenant les liens et la recherche rapide. Échap, le bouton Fermer, un clic extérieur ou le choix d’une page le referment. Le passage à un écran plus large restaure la barre latérale. Les pages internes conservent le lecteur et la session administrateur.

Le podium mobile empile les pochettes à la largeur du conteneur. Les filtres de période, les radios et les outils restent disponibles dans « Filtres et outils », replié initialement sur mobile et ouvert sur ordinateur ; son résumé indique la période et le nombre de radios choisies. Le bloc décoratif inférieur de la barre latérale est retiré. Contrôles dédiés aux formats 414×846 et 412×883, ainsi qu’à 320 px de largeur.


### Recherche personnalisable et logos de radios

La recherche avancée s’ouvre sur les musiques regroupées. Le sélecteur « Afficher » donne accès aux diffusions individuelles. « Colonnes à afficher » permet de choisir les informations visibles pour chaque vue ; la musique reste toujours présente. Le navigateur mémorise séparément les préférences ordinateur et mobile, ainsi que chaque vue. Le bouton « Colonnes par défaut » restaure la présentation initiale. Ces choix concernent la présentation : l’export conserve son propre choix de champs.

Sur mobile, seules la musique (avec son artiste) et les diffusions apparaissent initialement. Les filtres supplémentaires restent dans « Affiner la recherche » ; les horaires, stations et sources peuvent être ajoutés à la demande. Les tableaux détaillés peuvent défiler horizontalement dans leur conteneur.

Europe 2, NRJ, RFM, Mouv’ et FIP utilisent les logos identifiés du répertoire Ecouterradioenligne.com, servis par `cdn.instant.audio`. Les autres radios conservent la découverte d’image depuis leur site officiel. Si une image ne charge pas, les commandes et le nom de la station restent disponibles. Les cinq URL ont répondu HTTP 200 avec un contenu PNG lors du contrôle du 28 octobre 2026.

L’API proposée `https://api.instant.audio/data/playlist/37/enjoy-33-bordeaux` renvoie un JSON exploitable dans le navigateur de l’utilisateur, mais la sonde normale depuis l’environnement de diagnostic a reçu HTTP 403, `content-type: text/html` et `cf-mitigated: challenge`. Ce dernier en-tête [identifie une page de vérification Cloudflare](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/) : le collecteur n’a pas reçu le JSON, et le problème observé ne porte donc pas sur son décodage. L’accès du navigateur et celui d’un serveur peuvent différer ; ces essais ne prouvent pas à eux seuls le statut réseau du VPS de production. Aucun défi ni contrôle d’accès n’est contourné.

Le schéma fourni est compatible avec l’adaptateur JSON existant : `result.0.track_artist`, `result.0.track_title`, `result.0.track_played` (epoch en secondes, normalisé en millisecondes). Une intégration éventuelle doit prendre le premier événement et son horaire source, sans réinsérer l’historique retourné. Avant activation, il reste à vérifier un accès JSON stable depuis le serveur de collecte ; en cas de blocage, demander un accès serveur autorisé au fournisseur ou utiliser une autre source autorisée. Ces premiers essais précédaient l’activation explicitement demandée par l’utilisateur ; l’état actuel d’ENJOY 33 et le contrôle du 6 novembre sont décrits plus bas. Les huit autres sources configurées ont chacune renvoyé un titre extractible lors de la sonde initiale sans écriture ; ce constat ponctuel ne garantit pas leur disponibilité permanente.

### YouTube à la demande et synthèse Stats

Le bouton YouTube de la fiche recherche une vidéo uniquement après activation. La clé `YOUTUBE_API_KEY` reste côté serveur. La recherche utilise l’artiste et le titre de la fiche, quel que soit le fournisseur choisi, puis vérifie les statuts des vidéos : publiques, intégrables, accessibles en France et compatibles avec le morceau. Les versions manifestement différentes sont écartées et les chaînes portant le nom de l’artiste, VEVO ou Topic sont privilégiées ; la mention « Official » seule ne suffit pas à déclencher une sélection automatique. En cas d’ambiguïté, l’utilisateur choisit parmi les propositions. Une absence de résultat, une panne ou une intégration refusée restent explicites.

La fiche présente une seule action YouTube, près de la préécoute. Les boutons des listes ouvrent cette même action ; les liens des autres plateformes restent dans leur section dédiée. Le lecteur utilise `youtube-nocookie.com`. Il affiche principalement la vidéo : le titre, la chaîne et le hint du panneau applicatif ont été retirés. Seuls le chargement, le choix de propositions et les erreurs nécessitent encore un message.

La taille se règle avec le bouton à flèches ou la poignée : glissement à la souris ou au tactile, flèches du clavier (32 pixels), Début/Fin pour les bornes. Les largeurs intégrée et flottante sont conservées séparément pendant la session, dans les limites de l’écran. Le lecteur respecte un viewport minimal de 200 × 200 pixels aux largeurs testées (320 pixels et plus). La croix apparaît au survol sur ordinateur et reste disponible au focus clavier et sur écran tactile. Ces commandes occupent une bordure extérieure à l’iframe et ne recouvrent pas les commandes YouTube.

Fermer la fiche ou naviguer conserve uniquement une vidéo chargée, y compris en pause, dans le mini-lecteur flottant. Une recherche en attente, un quota atteint, une absence de résultat, des propositions sans choix ou un lecteur en erreur sont fermés avec la fiche ; une réponse tardive ne doit pas faire réapparaître le panneau. Fermer explicitement la vidéo détruit son lecteur. Sur les navigateurs compatibles avec [`Element.moveBefore`](https://developer.mozilla.org/en-US/docs/Web/API/Element/moveBefore), le même iframe est déplacé sans recréation. Sans cette API, la recherche et les erreurs restent dans la fiche ; lorsqu’une vidéo sélectionnée peut être intégrée, elle s’ouvre en flottant et la fiche se ferme. Le bandeau commun conserve lecture/pause, volume et progression ; reprendre une radio ou un extrait arrête la vidéo.

Les anciens boutons applicatifs « Réduire la vidéo » et « Plein écran » sont supprimés. Le [paramètre officiel `controls=0`](https://developers.google.com/youtube/player_parameters#controls) allège les commandes natives ; le clavier du lecteur et les contrôles communs restent utilisables. Cela ne garantit pas la disparition du titre, de l’avatar, du logo ni des écrans de pause/fin gérés par YouTube : `showinfo` et `modestbranding` sont obsolètes, et `rel=0` limite les recommandations à la chaîne sans les supprimer. Le parent ne peut pas modifier le DOM de l’iframe d’une autre origine. Le plein écran ou le PiP natif dépendent des fonctions exposées par YouTube et le navigateur ; aucun accès au `<video>` interne ni PiP automatique n’est tenté. Options et limites vérifiées dans la [documentation des paramètres YouTube](https://developers.google.com/youtube/player_parameters), consultée le 6 novembre 2026.

Validation du 6 novembre 2026 : `tests/browser_youtube_player.py` vérifie 1280 × 900, 414 × 846, 320 × 846 et 846 × 390 pixels, en thèmes clair/sombre, avec une API YouTube simulée et des données synthétiques. Les essais couvrent la poignée souris/tactile, le clavier, les limites de taille, la fermeture, les réponses tardives, les erreurs/quota, les propositions et le repli sans `moveBefore`. `tests/browser_persistent.py` vérifie aussi un média audio natif silencieux dans l’iframe d’une autre origine : il continue d’avancer après fermeture de la fiche et déplacement de l’iframe. Les contrôles communs, la navigation, l’administration et FR/EN sont couverts aux largeurs 1280/414/412/320 pixels. Les comportements propres au service YouTube réel restent soumis aux limites documentées ci-dessus. Captures locales : `/tmp/radio-youtube-phase2/`.

Les candidats YouTube, l’identifiant canonique du lien choisi, le choix manuel/automatique, la révision et les dates de demande/vérification sont enregistrés dans `youtube_matches`. Une fiche déjà recherchée est servie depuis la base, sans nouvelle requête. L’administration permet de rechercher de nouvelles propositions, choisir une vidéo, refuser les vidéos ou rétablir le choix automatique, indépendamment de la pochette et des paroles. Les révisions empêchent une recherche en cours d’écraser une correction manuelle. Un identifiant hors des propositions vérifiées est refusé.

La limite `YOUTUBE_SEARCHES_PER_DAY` (80 par défaut) est réservée atomiquement dans `service_quotas`, partagée par l’API et le worker, persistante après redémarrage. La journée suit `America/Los_Angeles`, comme la [remise à zéro quotidienne du fournisseur](https://developers.google.com/youtube/data/determine_quota_cost). Une erreur de recherche suspend les nouveaux appels pendant 15 minutes. Une réservation de deux minutes empêche les recherches concurrentes du même morceau ; son expiration permet de reprendre après un arrêt du processus. Les résultats enregistrés restent consultables sans consommer de recherche. Cette limite applicative ne représente pas les éventuels usages de la même clé hors du projet.

Le worker YouTube prend au maximum une demande par cycle de 30 secondes. Seuls les nouveaux passages collectés après cette migration sont automatiquement mis en file ; aucune migration ne recherche les vidéos de tout l’ancien catalogue. Les anciennes fiches sont résolues à la demande. Le script Google et le lecteur ne sont chargés dans le navigateur qu’au clic, même si le serveur a préparé une correspondance. Les exclusions personnelles et globales sont contrôlées avant les réponses concernées, y compris en administration ; la blacklist bloque aussi le traitement automatique des titres masqués.

Stats présente une synthèse sur 24 heures : passages, titres distincts, radios avec des données et couverture des genres. Le titre le plus diffusé utilise une fenêtre distincte de 6, 12 ou 24 heures, indiquée explicitement. Les autres analyses sont chargées à l’ouverture de leurs panneaux. Les exclusions personnelles et globales sont appliquées avant tous les agrégats, y compris en administration.


Validation YouTube du 28 octobre 2026 : après autorisation explicite de l’utilisateur pour l’envoi du titre/artiste d’un morceau de production, les essais sur le domaine public ont réussi dans Chrome à 1280×900 et 414×846. Recherche HTTP 200, réponse du lecteur `playabilityStatus: OK`, événement `PLAYING` (`1`), progression réelle du temps vidéo vérifiée sur mobile, audio radio en pause et fermeture du lecteur vérifiée. Le référent était présent et aucun blocage CSP n’a été observé. Aucune configuration supplémentaire de clé n’a été nécessaire.

Les essais isolés précédents avaient reçu le code 150 pour d’autres vidéos et dans un autre contexte : ils ne démontraient pas une panne générale de l’intégration. La [documentation officielle du lecteur](https://developers.google.com/youtube/iframe_api_reference#onError) définit 101/150 comme un refus de lecture intégrée par le propriétaire ; 153 signale l’absence de référent ou d’identification équivalente du client. Une vidéo refusée reste susceptible de nécessiter une autre proposition ou l’ouverture sur YouTube. Ces restrictions ne peuvent pas être levées par une autre clé de recherche. Les cas d’erreur, le démarrage différé et le cycle de vie ont aussi été vérifiés avec un lecteur simulé sur ordinateur/mobile.


### Reprise facultative après un extrait

Sans radio en écoute au lancement, les préécoutes suivent l’ordre de la liste affichée : classement (20 titres au plus), page de recherche avancée (100 lignes au plus) recherche rapide (12 résultats au plus) ou page des répétitions dans les statistiques. Une fiche ouverte depuis une de ces listes conserve ce contexte, tout comme le lecteur commun et la navigation interne. L’ordre est capturé au lancement, à partir du morceau choisi, et ne change pas lors des rafraîchissements automatiques. Il n’y a ni boucle ni chargement automatique de la page suivante. Les doublons d’une même identité musicale sont préécoutés une seule fois ; les versions distinctes restent distinctes et aucun passage enregistré n’est modifié. Une fiche isolée et une proposition de correction sans liste de lancement jouent un seul extrait.

Les morceaux sans extrait sont sautés. Un extrait en erreur est essayé une seule fois avant de passer au suivant ; une erreur de validation réseau ou un refus de lecture automatique par le navigateur arrête la file. Avant chaque passage automatique, la révision des exclusions est actualisée et le morceau est revérifié via la recherche locale, avec les exclusions personnelles et globales, y compris en administration. Le lien de préécoute normal contrôle à nouveau ces exclusions avant et après l’éventuel rafraîchissement de son URL fournisseur. Un changement d’exclusions arrête la file et l’extrait courant ; une réponse tardive ne peut pas reprendre la lecture. Une pause manuelle, un démarrage YouTube ou un changement de radio interrompent aussi une transition en attente.

Si une radio était réellement en écoute, elle est prioritaire sur tout enchaînement musical après la fin naturelle de l’extrait. Une ancienne station sélectionnée mais déjà mise en pause n’est pas une cible de reprise. Le bouton « Réglages d’écoute » permet de désactiver cette reprise : l’extrait se termine alors sans lancer d’autre musique. La reprise est activée par défaut pour un navigateur sans préférence ; un refus explicitement enregistré reste respecté après recharge. La cible est conservée si l’utilisateur choisit un autre extrait pendant la même session. Une pause manuelle ou une erreur ne lance pas automatiquement une radio. La connexion de la station reste muette pendant les extraits ; sa reprise ne change pas son URL, tandis que choisir une autre station décharge l’ancien flux.


ENJOY 33 — activation demandée par l’utilisateur : la collecte utilise désormais l’API Instant.audio, avec les chemins `result.0.track_artist`, `result.0.track_title` et `result.0.track_played`. Seul le premier événement est traité ; son horaire source protège contre les réinsertions après redémarrage. Le flux MP3 reste utilisé pour l’écoute. Observation du 6 novembre 2026 : plusieurs événements JSON récents et distincts, ainsi qu’une dernière observation actualisée, sont visibles sur l’API publique. La collecte est donc active à cet instant ; la sonde externe depuis l’environnement de diagnostic reçoit toujours un défi HTTP 403. Un refus HTTP reste isolé à cette station et ne supprime aucun historique.


### Radio maintenue en sourdine

Validation de l’enchaînement : `PYTHONPATH=. python tests/browser_preview_sequence.py` utilise uniquement des données synthétiques et de vrais éléments audio alimentés par des WAV silencieux. Les fins naturelles sont accélérées avec `playbackRate`, sans simuler l’événement `ended`. Le scénario couvre les classements, recherches, fiches et répétitions Stats, les extraits absents/en erreur, la fin de liste, les pauses et validations tardives, la priorité radio, le refus enregistré, la station déjà en pause, le changement de station, le démarrage vidéo, les exclusions et le refus de lecture automatique à 1280/414/320 pixels. Les restrictions de préécoute sont aussi vérifiées côté serveur avec et sans administration dans `tests/test_preview_exclusions.py`. Les parcours YouTube/Stats et de persistance restent couverts par leurs scripts dédiés. Captures locales : `/tmp/radio-preview-sequence/`.

Le flux radio et les extraits utilisent deux éléments audio distincts. Mettre la radio en pause coupe son son tout en conservant la connexion et l’avancement du direct. Pendant un extrait ou une vidéo, une radio déjà lancée continue en sourdine. La reprise de cette même station rétablit son son, sans réaffecter son URL. Choisir une autre station arrête et décharge l’ancien flux ; les extraits précédents sont aussi déchargés. Un navigateur, le système ou une panne réseau peut interrompre un flux : le maintien de la connexion ne garantit pas l’absence de publicité côté diffuseur après une reconnexion.

### Supervision et langues

`/admin/workers` affiche les états persistants des trois workers (collecte, enrichissement, YouTube), l’heure du dernier signal, le nombre d’éléments prévus, terminés ou en échec dans le cycle, le quota YouTube et les demandes en attente. Rafraîchissement toutes les dix secondes ; les délais de péremption distinguent une activité interrompue d’un cycle encore actif. Le journal est limité à 200 événements en base, les 100 derniers sont affichés. Il ne contient que des codes techniques autorisés, sans titre, URL, payload de fournisseur ni exception brute.

Les réglages proposent Français / English. Le français est la valeur par défaut ; `?lang=en` permet un lien partageable et un cookie conserve le choix entre les pages et rechargements. Le changement explicite de langue recharge la page. Les catalogues `app/locales/fr.json` et `en.json` partagent les mêmes clés stables, utilisées par Jinja (`app/i18n.py`) et JavaScript (`RadioI18n`). Leurs clés `ui.*` identifient les messages d’origine par une empreinte stable : modifier une traduction ne demande pas de modifier le code appelant. Les messages avec paramètres sont interpolés sans construire de HTML ; le français sert de repli. Les pluriels utilisent `Intl.PluralRules`, les dates et nombres la langue sélectionnée. Noms de radios, artistes, titres, paroles et données source restent intacts.

Validation reproductible : `python -m pytest`, `ruff check .`, `ruff format --check .`, `node --test tests/*.test.cjs`, contrôle `node --check` des scripts, migrations Alembic et `PYTHONPATH=. python tests/browser_persistent.py`. Cette dernière suite utilise exclusivement des données synthétiques et un lecteur YouTube simulé ; elle vérifie le même iframe après navigation, le contrôle du flux radio en sourdine, le choix vidéo en administration, la supervision, les langues et les largeurs 1280/414/412/320 px. Les captures sont écrites dans `/tmp/radio-final-captures/`.

Les exports de sauvegarde incluent aussi les correspondances YouTube, les choix manuels, le quota partagé et la supervision. Les archives antérieures à ces tables restent importables. La sauvegarde ne remet pas le quota à zéro lors d’une restauration. Le contrôle YouTube du déploiement utilise la session de base de données et respecte une temporisation ou un quota épuisé.


### Index des agrégations SQLite

La migration `0013` ajoute trois index couvrants : passages par artiste/titre, passages par date, et métadonnées par identifiant/genre. Les agrégations peuvent ainsi lire les champs nécessaires dans les index sans charger les gros payloads source ni les JSON/paroles. Les requêtes, exclusions personnelles/globales, limites de cache et règles de rediffusion sont conservées ; aucune donnée d’historique n’est modifiée. PostgreSQL n’est pas ciblé par cette optimisation mesurée sur SQLite.

La création des index parcourt les données une fois au déploiement ; elle est suivie d’un `ANALYZE` limité aux nouveaux index. Le retour vers `0012` retire seulement ces index. Le [rapport et les outils de profiling](https://github.com/Hicha-m/track-radio/tree/profiling) restent sur la branche de test, hors du code livré. Les essais synthétiques comparent aussi les résultats avant/après, sans utiliser de données de production.

### Playlists composées depuis les radios (phase 5)

Depuis les outils du classement, « Composer une playlist » reprend la période et les radios sélectionnées. Choisir de 1 à 50 morceaux, initialement les plus diffusés (ou par date/artiste/titre), puis décocher et déplacer les morceaux avec les flèches. Une identité musicale occupe une seule ligne ; les versions Live/remix restent distinctes. Les exclusions personnelles et globales sont appliquées avant agrégation et limite, sans exception administrateur. La sélection reste en mémoire jusqu’à fermeture ; elle ne modifie ni la collecte ni l’historique.

« Chercher les vidéos YouTube » interroge séquentiellement le service existant, uniquement sur action utilisateur. Cache, plafond quotidien local et temporisation sont partagés avec les fiches ; aucune recherche automatique au montage. Une réponse ambiguë propose les candidats, sans en choisir arbitrairement un. Les morceaux introuvables, refusés ou encore en attente restent identifiés. Une erreur réseau, un timeout ou un quota arrête le lot, en conservant les choix déjà obtenus ; annulation explicite et changements de filtres invalident les réponses tardives. Le plafond local de recherches demeure volontairement conservateur et ne constitue pas une garantie du quota Google disponible.

« Lire la sélection YouTube » utilise le lecteur partagé et ses commandes. Chaque morceau est revérifié côté serveur avant son lancement. Les identifiants vidéo doivent appartenir aux propositions enregistrées pour ce morceau ; les identités exclues ou supprimées ne sont plus renvoyées. Une vidéo répétée est sautée, les morceaux sans vidéo sont ignorés, la fin de sélection ne boucle pas. Une erreur de vidéo passe au morceau suivant une seule fois ; un échec de vérification arrête la lecture. Fermeture du compositeur, modification de sélection ou filtres arrêtent cette session. Une radio ou un extrait lancé manuellement garde la priorité. Refus d’autoplay : les commandes habituelles du lecteur permettent de reprendre ; il n’y a pas de boucle de tentatives.

L’export CSV propose YouTube ou Spotify et respecte l’ordre coché. Le serveur revérifie les exclusions au téléchargement, retire identités inconnues/masquées et doublons, et neutralise les cellules pouvant être des formules. Colonnes : position, artist, title, status, url. `ready` signifie lien vidéo choisi et encore autorisé, ou lien Spotify exact déjà connu ; `unresolved` signifie lien de recherche à vérifier manuellement. Les liens Spotify sont exclusivement des URLs de pistes officielles validées ou des recherches ; aucun rapprochement approximatif n’est présenté comme certain. Un fichier CSV est un export portable : il n’est pas un format d’import natif garanti par YouTube ou Spotify. Aucun jeton utilisateur, connexion OAuth ni écriture dans un compte tiers n’est implémenté dans ce parcours.

API : `GET /api/playlists/selection` (période, `station_ids`, `sort`, `limit`, `exclusions`) ; `POST /api/playlists/export?exclusions=…` avec `items` (au plus 50 identifiants musicaux et choix vidéo), `destination` et `format` (`json` pour validation, `csv` pour téléchargement). Réponses `no-store`. Pas de nouvelle table, migration ou secret.

**Faisabilité externe vérifiée le 8 novembre 2026.** [YouTube impose OAuth pour les écritures](https://developers.google.com/youtube/data/guides/authentication) ; la clé API de recherche ne donne pas accès au compte d’un utilisateur. [Créer une playlist](https://developers.google.com/youtube/data/reference/rest/v3/playlists/insert) coûte 50 unités, puis [ajouter chaque vidéo](https://developers.google.com/youtube/data/reference/rest/v3/playlistItems/insert) coûte 50 unités. Une intégration enregistrant les playlists demanderait un client OAuth, un consentement adapté et sa validation, une gestion des jetons et des créations partielles. Ce périmètre n’est pas déclaré disponible par l’interface.

[Spotify permet de créer une playlist avec autorisation utilisateur](https://developer.spotify.com/documentation/web-api/reference/create-playlist). Le client credentials utilisé pour le catalogue n’autorise pas ces écritures. Le [guide de migration Development Mode](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) impose notamment Premium au propriétaire de l’application, un Client ID pour les nouvelles applications et cinq utilisateurs, avec des droits acquis pour certaines applications existantes. L’accès réel dépend donc de l’application enregistrée ; il n’a pas été supposé ni testé sur un compte de production. La solution retenue est composition/lecture locale et export, sans annoncer de création de playlist dans un compte.

**Validation reproductible :** `tests/test_playlists.py` couvre identité, versions distinctes, périodes/radios, exclusions combinées y compris administrateur, ordre, identifiants inconnus et vidéo falsifiée/refusée, liens Spotify et sécurité CSV. `tests/browser_playlists.py` vérifie le parcours à 1280/414/320 px, avec API applicative réelle sur historique synthétique et API YouTube simulée (événements fin/erreur), sélections et réorganisation, téléchargements pour les deux destinations, absence de média avant clic, enchaînement et arrêt, quota, remise à zéro des filtres. Captures locales `/tmp/radio-playlist-evidence/`. Ces essais ne prouvent pas la lecture de chaque vidéo externe ni une création dans un compte tiers.
