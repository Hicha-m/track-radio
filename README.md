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

L’accueil présente les 20 titres les plus diffusés sur toutes les radios ou sur une radio choisie, sur 24 heures, 7 jours (par défaut), 30 jours ou tout l’historique. Le classement utilise les passages réellement collectés et s’actualise toutes les 30 secondes lorsque la page est visible. Cliquer sur un titre ouvre ses passages dans la recherche avancée. Les pochettes proviennent du catalogue musical quand une correspondance est validée ; un visuel CSS prend le relais sinon. Les images restent dans un cadre carré et les numéros du podium restent superposés, y compris si une image échoue à charger. Cliquer sur un titre du classement ouvre sa fiche avec durée, fournisseur, extrait et paroles disponibles. Le bouton ▶ adjacent lance seulement l’extrait ; il est désactivé si le fournisseur n’en propose pas. Depuis la fiche, « Voir les diffusions de ce titre » ouvre sa recherche exacte.

La recherche spécialisée est accessible sur **`/recherche`** depuis la navigation. Le lecteur radio reste monté pendant la navigation interne : passer entre classements, recherche, statistiques et administration, ouvrir les passages d’un titre ou utiliser précédent/suivant conserve le même élément audio et le même flux. Une recharge complète de l’onglet ou un lien ouvert dans un autre onglet crée un nouveau lecteur.

## Cache du navigateur et catalogue musical

Chaque onglet mémorise jusqu’à 40 combinaisons de filtres dans `sessionStorage` (ou en mémoire si le stockage est refusé). Revenir à un filtre affiche immédiatement son résultat connu. Pendant 60 secondes, ce retour évite une requête ; les actualisations automatiques continuent à interroger uniquement notre API, toutes les 30 secondes pour le classement et toutes les 10 secondes pour la recherche lorsque le direct est activé. Un résultat mémorisé plus ancien peut s’afficher pendant sa mise à jour ou une panne, jusqu’à 5 minutes. Les réponses identiques ne reconstruisent pas les lignes. Les fichiers CSS/JS portent une empreinte de contenu : une nouvelle version invalide également ce cache.

Le collecteur enrichit les musiques **en arrière-plan**, indépendamment des filtres et de la collecte radio. Il traite au plus **2 morceaux**, puis attend **20 secondes**. Chaque morceau essaie Deezer, puis iTunes si aucune correspondance unique sûre n’est trouvée. Une correspondance Deezer peut demander sa fiche détaillée et les genres de son album ; les albums sont mémorisés pendant 24 heures, avec au plus 128 entrées. LRCLIB est ensuite interrogé pour les paroles. Les 100 titres du classement global des 7 derniers jours passent en priorité ; cette priorité est recalculée chaque minute. Le reste de l’historique est inscrit dans la file par lots de 200 passages, sans charger la base entière. Une erreur d’enrichissement n’arrête pas la collecte des radios.

Les résultats et tentatives par fournisseur sont conservés dans SQLite. Une absence ou ambiguïté définitive chez Deezer et iTunes arrête les recherches automatiques de la fiche. Les paroles ont leur propre état : un résultat absent ou ambigu attend une validation manuelle. Les erreurs temporaires sont réessayées après 15 minutes, uniquement pour les étapes non résolues ; elles ne retirent pas un enregistrement déjà validé. Une fiche complètement traitée n’est plus recherchée périodiquement. L’administrateur peut relancer explicitement l’automatisme. Une réponse HTTP 429 ou 503 suspend les requêtes vers ce fournisseur selon `Retry-After` (15 minutes par défaut, entre 1 minute et 24 heures). L’extrait validé est résolu au clic, avec un cache de 60 secondes, pour renouveler les URL Deezer temporaires. L’audio est lu depuis le fournisseur, sans être stocké par le serveur.

Un identifiant interne stable est calculé à partir de l’artiste et du titre, avec normalisation de la casse, des accents et des espaces. Un même titre sur plusieurs radios partage sa fiche ; chaque passage reste conservé. L’identifiant de l’enregistrement est son ISRC lorsque disponible, sinon `deezer:<id>` ou `itunes:<id>`. Il permet de repérer les libellés qui renvoient au même enregistrement. **Les classements continuent à regrouper par artiste/titre collectés** : aucune fusion approximative ou suppression des passages n’est effectuée.

La sélection automatique exige un seul candidat correspondant à l’artiste et au titre. Les mentions remix/live/radio edit ne sont pas retirées pour forcer un rapprochement. Plusieurs candidats, un invité différent, un artiste inconnu ou un résultat douteux restent à vérifier. Les paroles ne sont automatiquement liées qu’en cas de correspondance unique avec une durée à deux secondes près.

L’administration s’ouvre directement sur **`/admin`**, sans entrée dans la sidebar. La clé `ADMIN_TOKEN` sert uniquement à se connecter : elle n’est conservée ni dans `localStorage`, ni dans `sessionStorage`, ni dans une variable globale. Le serveur crée une session révocable dans SQLite et envoie un identifiant opaque dans un cookie `__Host-radio_admin`, `HttpOnly`, `Secure`, `SameSite=Strict`, limité au site et au chemin `/`. Le cookie dure la session du navigateur ; le serveur impose en plus une expiration absolue de **8 heures** par défaut (`ADMIN_SESSION_HOURS`, de 1 à 24). La table contient uniquement le hash de l’identifiant, un jeton CSRF et une signature liée à la clé serveur. Changer `ADMIN_TOKEN` invalide les sessions existantes. Au plus 20 sessions sont conservées ; elles sont exclues des exports de sauvegarde.

La connexion demande une origine du même site et un en-tête dédié. Les modifications et la déconnexion exigent la session, un jeton CSRF synchronisé et une origine concordante ; les réponses administratives sont `no-store`. Cette protection suit le [modèle de jeton synchronisé OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#synchronizer-token-pattern). Les tentatives de connexion sont limitées à 5 par adresse réseau observée sur 15 minutes, avec un plafond global de 100 et un stockage mémoire borné (un seul worker API). Les tokens `Authorization: Bearer …` invalides consomment le même quota, partagé entre les routes administratives et le formulaire de connexion. Une fois le quota épuisé, ces deux modes renvoient `429` avec `Retry-After` jusqu’à expiration de la fenêtre. Les appels Bearer valides ne consomment pas le quota. Les compteurs sont réinitialisés au redémarrage. Derrière un proxy non déclaré de confiance, cette adresse peut être celle du proxy. HTTPS reste nécessaire en public ; localhost sert au développement. Le déploiement HTTP public conserve l’administration désactivée.

La session fonctionne entre classement, recherche, Stats et `/admin/catalogue`, ainsi qu’après rechargement. Les fiches affichent **Modifier** uniquement après vérification de la session. Ce bouton ouvre le même composant de correction que la file administrative, sans formulaire de clé dans la fiche. La barre supérieure indique le mode admin et permet une déconnexion qui révoque la session côté serveur. Les clients serveur utilisant explicitement `Authorization: Bearer …` restent compatibles ; l’interface navigateur n’utilise plus ce mode.

La file de vérification de `/admin/catalogue` permet de rechercher les fiches par artiste/titre, de filtrer celles à vérifier et de parcourir les résultats par pages de 25. Les correspondances douteuses passent d’abord, puis les fiches sont classées par nombre de détections sur les sept derniers jours. Ouvrir une fiche permet de modifier la recherche, choisir Deezer, Apple Music via iTunes ou Spotify, choisir les paroles LRCLIB, retirer une mauvaise correspondance ou relancer l’automatisme. Les propositions de morceaux et de paroles sont séparées et ne sont plus exposées par la fiche publique. Une recherche manuelle suspend l’automatisme ; un choix manuel et les métadonnées brutes des radios sont conservés. Changer d’enregistrement retire les anciennes paroles. Une révision vérifiée atomiquement empêche un résultat automatique tardif d’écraser une intervention administrateur.

Le déploiement HTTP actuel désactive déjà l’administration dans `compose.production.yaml`. Les corrections y restent donc désactivées. Pour les activer, utiliser une configuration HTTPS avec un `ADMIN_TOKEN` non vide ; aucune clé n’est ajoutée automatiquement au VPS. Deezer et l’API de recherche iTunes ne nécessitent pas de secret dans cette intégration. Spotify est intégré pour la recherche manuelle et la résolution du premier résultat depuis une fiche : configurer `SPOTIFY_CLIENT_ID` et `SPOTIFY_CLIENT_SECRET` dans le `.env` privé ou dans l’environnement serveur. Le jeton OAuth est obtenu côté serveur, conservé uniquement en mémoire jusqu’à son expiration avec une marge de 60 secondes, puis renouvelé. Une réponse 401 provoque un seul renouvellement et nouvel essai ; un refus 403 n’est pas réessayé et ne devient pas un faux résultat « morceau absent ». La recherche utilise au maximum 10 résultats et ne fabrique pas d’extrait si Spotify n’en fournit pas. Les identifiants et jetons ne sont jamais renvoyés au navigateur. La collecte automatique utilise Deezer puis iTunes ; Spotify reste réservé aux recherches manuelles. Lors de la vérification du 19 octobre 2026, l’authentification Spotify a réussi mais la recherche de morceaux a renvoyé 403 : l’accès au catalogue reste à résoudre côté fournisseur. Voir la [documentation OAuth Spotify](https://developer.spotify.com/documentation/web-api/tutorials/client-credentials-flow). Aucun identifiant réel n’est versionné ; seules les variables vides figurent dans `.env.example`.

Les boutons des radios affichent l’image officielle détectée sur leur site, avec une commande lecture/pause superposée ; une image indisponible laisse la commande seule. Le lecteur affiche la pochette du morceau validé dans le catalogue, puis l’image officielle de la radio si la pochette est absente ou ne charge pas. Les extraits utilisent également leur pochette. Pour une nouvelle radio, renseigner `website_url` dans `config/stations.json` : le serveur détecte automatiquement `og:image`, `twitter:image`, puis une icône du site, sans maintenir une liste de logos. Sans `website_url`, il essaie `metadata_url` si cette page contient du HTML. La découverte limite la réponse à 256 Kio, vérifie les destinations publiques et conserve le résultat en mémoire 24 heures (15 minutes en cas d’échec). Une image indisponible laisse la note musicale. La migration `0005` ajoute le site officiel ; appliquer `alembic upgrade head`, puis `python scripts/seed.py` pour synchroniser les sites configurés.

Dans la fiche, « Masquer ce titre » se trouve dans le pied de fenêtre à droite. Le lien « Rechercher sur Spotify » ouvre directement la recherche publique Spotify, sans déclencher de résolution API à l’ouverture. La route `/api/catalog/spotify-match` est désormais réservée à l’administration ; le fournisseur reste accessible dans son sélecteur de recherche. Les identifiants Spotify restent côté serveur.

Les sauvegardes `scripts/transfer.py` comprennent le catalogue, les propositions, les paroles et les choix manuels. Les anciennes sauvegardes sans catalogue restent importables. La migration `0004` crée uniquement une table de métadonnées et conserve les passages existants.

## Compléter les informations avec un second catalogue

Un enregistrement Deezer reste l’enregistrement principal même si iTunes fournit ses genres manquants, et réciproquement. Le collecteur essaie le second catalogue lorsque les genres sont définitivement absents du premier, dans les mêmes petits lots qu’avant. Les absences et ambiguïtés sont mémorisées ; les erreurs temporaires restent réessayables. Aucun appel supplémentaire n’est déclenché à l’ouverture d’une fiche.

Le rapprochement exige une correspondance unique artiste/titre/version/invités et une durée à deux secondes près ; deux ISRC fournis doivent être identiques. Sans durée, un ISRC commun est nécessaire. Les champs déjà présents et le choix d’enregistrement ne sont pas remplacés. Une date de sortie issue d’un autre album ou d’une autre édition n’est pas attribuée à l’album principal. Les seuls compléments sont les genres, l’album, la date de sortie et les invités effectivement fournis. `field_sources` conserve la source de chaque champ et `additional_sources` les liens des enregistrements secondaires validés. La fiche présente cette provenance dans ses métadonnées.

Pour le complément uniquement, un crédit avec collaborateurs explicites séparés par ` & ` est aussi accepté lorsque l’artiste principal est identique et placé en premier, avec titre complet, album et durée concordants. Par exemple, **Tayc — MAMAN PRIE** sur Deezer peut recevoir le genre d’**iTunes : Tayc & RnBoi**, sans remplacer son identité Deezer. Cette exception exige un album et une durée renseignés des deux côtés et une correspondance unique ; elle ne modifie pas la sélection automatique de l’enregistrement principal.

Pour une fiche corrigée manuellement, **Modifier → Compléter les informations manquantes** lance explicitement la recherche. Ce bouton utilise la session et la protection CSRF habituelles ; les paroles et le choix manuel sont conservés. `POST /api/catalog/{track_id}/complete` renvoie le résultat et les champs complétés. Une révision protège la fiche contre une correction concurrente. La migration **0008** remet uniquement les anciennes correspondances automatiques sans genre dans cette file ; les fiches manuelles et les morceaux absents restent inchangés.

## Fiche musicale

La fiche présente d’abord le titre officiel et l’artiste, puis la pochette carrée et un résumé (album, durée, genre, source, statut, extrait). L’extrait réutilise le lecteur global et n’est résolu qu’au clic. L’ouverture de la fiche lit uniquement l’API locale.

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

Sans connexion admin, dans une fiche musicale, « Masquer pour moi » ajoute le couple artiste/titre à la liste de cet appareil. « Titres masqués » permet de réafficher un titre, de saisir un titre et son artiste exacts, ou d’ajouter les trois exemples courants : `Mouv’` avec artiste inconnu, `NRJ` / `Hit Music Only !` et `Les Dédicaces` / `SKYROCK`. Un artiste vide cible les passages sans artiste. La correspondance ignore la casse et les accents.

La liste est conservée dans `localStorage`, jusqu’à 50 titres ; si le stockage est indisponible, elle reste en mémoire pour la page courante. Elle ne supprime pas les passages collectés. Elle est transmise à notre API via le paramètre `exclusions`, puis appliquée en SQL **avant** les regroupements, le classement et la pagination. Elle s’applique au classement, à la recherche rapide, à la recherche avancée, aux statistiques (y compris diversité) et aux exports. Le masquage reconnaît les apostrophes droites et typographiques, notamment `Mouv'` et `Mouv’`, sans fusionner les identités musicales ni modifier l’historique brut. Les caches distinguent chaque liste pour éviter de réafficher des titres masqués. Il s’agit d’un filtre de présentation personnel, pas d’un contrôle d’accès aux données.

Les liens **CSV** et **JSON** exportent tous les résultats des filtres actifs, dans l’ordre choisi, et non uniquement la page visible. Sur le classement, ils exportent les musiques correspondant à la radio et à la période choisies, avec un tri par fréquence. La limite est de **10 000 lignes** ; si elle est dépassée, l’API demande de préciser les filtres. L’export fixe un instantané des identifiants et lit les lignes par lots de 50. Le CSV utilise UTF-8 avec BOM et `;` pour les tableurs français ; les textes pouvant être interprétés comme des formules sont neutralisés. Le JSON conserve le texte original. Les fichiers comprennent les métadonnées validées disponibles et un lien Spotify par titre.

Exemples : `GET /api/export?view=tracks&artist=Mosimann&format=csv` ou `GET /api/export?view=plays&station_id=1&since=2026-10-01T00:00:00Z&format=json`. Les dates, le tri, les filtres de source et d’artiste, ainsi que `exclusions`, suivent les mêmes règles que `/api/explore`. Aucun appel fournisseur n’est effectué pendant l’export.

## Spotify sans accès Web API

Chaque fiche propose « Rechercher sur Spotify », un lien ouvrant la recherche normale de Spotify à partir de l’artiste et du titre. Les exports contiennent également ce lien, ou le lien du morceau lorsqu’une correspondance Spotify est déjà validée. Cette ouverture ne nécessite ni jeton de notre application ni accès développeur Premium, et laisse l’utilisateur choisir la bonne version et l’ajouter à sa playlist dans Spotify.

La création automatique de playlists reste tributaire de la Web API et d’une autorisation OAuth utilisateur avec les droits `playlist-modify-private` ou `playlist-modify-public`. La [documentation des quotas](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) impose un compte Premium au propriétaire d’une application en mode développement : connecter un autre utilisateur ne lève pas cette condition. Le [Web Playback SDK](https://developer.spotify.com/documentation/web-playback-sdk/tutorials/getting-started) demande également Premium pour la lecture. Les SDK Android/iOS pilotent l’application Spotify et l’Ads API sert à la publicité ; ils ne remplacent pas l’accès Web API pour rechercher et créer des playlists. Aucun écran de connexion ou bouton de création automatique de playlist n’est ajouté tant que cet accès est bloqué. Deezer, iTunes et LRCLIB restent les fournisseurs utilisables pour les métadonnées et aperçus.

## Recherche rapide

Le raccourci de la barre latérale, la loupe de l’en-tête et `Ctrl+K` / `⌘K` ouvrent une fenêtre centrée. Elle recherche les morceaux collectés, par titre ou artiste, après 250 ms de pause et à partir de deux caractères. Les mots peuvent se répartir entre artiste et titre : « Mosimann Soon » retrouve le morceau, quel que soit leur ordre. Elle affiche jusqu’à 12 résultats, avec pochette, fiche au clic et aperçu disponible via ▶. « Voir tous les résultats » ouvre la recherche avancée avec la requête conservée. La saisie interroge notre API et son cache navigateur, sans appeler Spotify, Deezer ou iTunes. Fermer la fenêtre et ouvrir une fiche conservent la lecture. Démarrer explicitement un extrait remplace la radio dans le lecteur commun ; son volume reste inchangé. La recherche conserve son texte et ses résultats à la fermeture et propose « Retour à la recherche » depuis une fiche ouverte dans ses résultats. Les fiches et fenêtres se ferment aussi par Échap ou un clic à l’extérieur.

## Vérification de l’interface et du lecteur

Le même lecteur audio sert au direct radio et aux extraits, avec les mêmes réglages de volume et de sourdine. Un extrait remplace le direct dans le lecteur ; la barre affiche sa progression réelle, le temps écoulé et sa durée, avec déplacement dans l’extrait. « Reprendre la radio » relance le dernier flux sélectionné. Le passage entre pages, la fermeture d’une fiche ou de la recherche n’interrompt pas l’extrait. Le lecteur dispose de commandes lecture/pause, sourdine et volume sur ordinateur. Sur mobile, il privilégie les informations du morceau, lecture/pause et sourdine ; le volume se règle avec les boutons de l’appareil. La barre réserve l’espace nécessaire et la zone de sécurité en bas d’écran. Le thème clair/sombre continue de fonctionner pendant la navigation.

Un contrôle navigateur reproductible utilise une base temporaire, un serveur sur un port libre, une source WAV simulée et des images simulées. Il ne démarre pas le collecteur et ne modifie ni la base locale ni le VPS. Il vérifie les formats **402×874**, **412×915** et **1280×900**, les cadres carrés et rangs du podium, l’absence de débordement horizontal, la lecture réelle pendant les liens internes et l’historique du navigateur, les commandes et les erreurs de navigation. Il vérifie aussi Stats et Administration, la connexion, la session persistante pendant la navigation, la déconnexion et la non-persistance de la clé, les préférences radio, les 24 barres horaires et le lecteur commun aux extraits, le déplacement dans une piste, le volume conservé, le retour à la recherche, la fermeture par clic extérieur, le masquage/réaffichage et les téléchargements filtrés.

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

### Configurer Spotify dans GitHub Actions

Créer ou renouveler les identifiants dans le [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), puis utiliser le Client ID et le Client Secret de la **même application**. Si seul le secret est renouvelé, le Client ID reste celui de cette application. Remplacer les anciens identifiants partagés par les nouveaux, sans les copier dans le README, un commit, une issue ou une conversation.

1. Ouvrir les [secrets Actions du dépôt Hicha-m/track-radio](https://github.com/Hicha-m/track-radio/settings/secrets/actions).
2. Dans **Settings → Secrets and variables → Actions → Secrets**, cliquer sur **New repository secret**.
3. Créer les deux **Repository secrets** ci-dessous. Utiliser l’onglet Secrets, pas Variables ; aucun Environment GitHub spécifique n’est requis par le workflow actuel.

| Nom exact | Valeur à renseigner |
| --- | --- |
| `SPOTIFY_CLIENT_ID` | Client ID de l’application Spotify |
| `SPOTIFY_CLIENT_SECRET` | Client Secret de la même application |

Si ces noms existent déjà, utiliser **Update** pour remplacer leurs valeurs. Les deux doivent être présents ensemble. Deezer et iTunes restent utilisables sans eux.

Alternative avec GitHub CLI : les commandes suivantes demandent la valeur de façon interactive, sans la mettre dans la commande ou son historique :

```bash
gh secret set SPOTIFY_CLIENT_ID --repo Hicha-m/track-radio
gh secret set SPOTIFY_CLIENT_SECRET --repo Hicha-m/track-radio
# Vérifier uniquement les noms et dates, jamais les valeurs :
gh secret list --repo Hicha-m/track-radio
```

Le code est déjà prévu pour ces noms : `ci.yml` transmet les secrets au workflow réutilisable avec `secrets: inherit`, et `cd.yml` les lit via `${{ secrets.SPOTIFY_CLIENT_ID }}` et `${{ secrets.SPOTIFY_CLIENT_SECRET }}`. Le CD les transmet par SSH dans un fichier privé temporaire ; `scripts/deploy.sh` les installe dans le `.env` du VPS sans les afficher, puis supprime ce fichier. Une étape de nettoyage supprime aussi le fichier temporaire si le déploiement échoue. Ils ne sont inclus ni dans le code ni dans l’image Docker. Sans ces secrets, une configuration serveur existante reste conservée.

Ajouter ou modifier un secret ne déclenche pas un déploiement : les nouveaux identifiants seront pris en compte au prochain CD réussi **utilisant cette version des workflows**, après publication des changements sur la branche par défaut. Aucun changement manuel du VPS n’est nécessaire. Pour une rotation, remplacer les valeurs GitHub, déployer, puis vérifier le fonctionnement avant de retirer les anciens accès lorsque Spotify permet leur coexistence.

**Accès depuis un agent ou en local :** GitHub ne permet pas de relire les valeurs enregistrées dans les secrets. Un agent peut vérifier leurs noms et le résultat des jobs ; les workflows autorisés peuvent les utiliser sans qu’on les recopie dans le chat. Les secrets GitHub ne sont pas automatiquement injectés dans cet espace de travail ni dans Docker Compose local : pour tester localement, renseigner séparément les mêmes variables dans le `.env` privé ignoré par Git, ou les fournir via l’environnement d’exécution. Ne pas extraire les valeurs via les logs ou une sortie de workflow.

**Dépannage :** si un seul secret est renseigné, le CD refuse cette configuration partielle. Une authentification OAuth réussie suivie d’un `403` sur la recherche signale un refus d’accès au catalogue ; enregistrer les identifiants dans GitHub ne corrige pas à lui seul ce refus. Vérifier l’accès de l’application auprès de Spotify. La correction manuelle des fiches musicales reste soumise à `ADMIN_TOKEN` et à la configuration HTTPS décrite plus haut.

Référence : [utiliser les secrets dans GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

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

## Vérification du déploiement et référence visuelle

Le 19 octobre 2026, une comparaison HTTP de l’accueil, de `style.css` et de `charts.js` a donné les mêmes empreintes entre `http://127.0.0.1:8081/` et `http://129.151.250.229/`. Le dernier workflow du commit `51e8048` était réussi, déploiement compris. Aucune modification du VPS n’a été effectuée. La nouvelle route `/api/version` et les URL des assets avec empreinte permettront d’identifier les versions lors des prochains déploiements par CD. Une différence visuelle malgré des fichiers identiques peut notamment provenir du cache du navigateur, du thème ou de la largeur de la fenêtre ; cela n’établit pas à lui seul un échec du CD.

Le fichier Figma fourni (`7Otth1wuOkPBCOUzTUSGWt`) expose une page `Cover` avec un cadre de présentation et des images d’écrans iPhone. Il ne contient pas de bibliothèque de composants ou d’écrans éditables permettant une vérification détaillée. L’interface actuelle reprend des conventions Apple Music (accent rose, pochettes, listes, lecteur), mais une conformité écran par écran ne peut pas être certifiée avec cette seule couverture. Aucun écran n’a été reconstruit à partir des images de présentation.


## Masquage global et personnel

Une fois connecté en admin, les boutons **Masquer pour tous**, la saisie manuelle et les raccourcis modifient une liste globale persistante. Elle s’applique à tous les visiteurs et aux admins avant les classements, statistiques, recherches, historiques et exports. Les accès directs aux fiches masquées sont réservés aux admins. La collecte et les données enregistrées restent intactes.

Sans connexion admin, le masquage reste personnel dans `localStorage` (50 titres maximum). Ces préférences s’ajoutent à la liste globale : réafficher globalement un titre ne retire pas les exclusions personnelles. Les anciennes préférences ne sont jamais transférées automatiquement. L’admin peut les retirer dans une section distincte de **Titres masqués pour tous**, accessible aussi depuis l’administration.

La migration `0009` crée une liste globale vide. Les sauvegardes JSONL incluent cette liste et les anciennes sauvegardes restent importables. Les écritures passent par `POST /api/admin/exclusions` et `DELETE /api/admin/exclusions/{id}`, protégés par la session et le jeton CSRF existants ; la lecture admin utilise `GET /api/admin/exclusions`. `GET /api/exclusions/revision` expose une empreinte publique pour les caches. Les pages vérifient cette empreinte au chargement, à la navigation, au retour sur l’onglet et toutes les 30 secondes lorsque la page est visible.

Vérification dédiée : `PYTHONPATH=. python tests/browser_global_exclusions.py` (Playwright Chromium), plus `python -m pytest tests/test_global_exclusions.py` et `node --test tests/*.test.cjs`.
