# Recherche automatique des pochettes et vidéos

La découverte est effectuée par `python -m app.discovery_worker`, dans un
conteneur distinct de l’API et du collecteur. L’utilisateur n’a pas à rechercher
ou coller un lien pour lancer cette tâche. Les lecteurs utilisent les résultats
persistants existants, sans attendre une recherche pendant le chargement.

## Sources et sélection

Le worker cherche d’abord les vidéos avec [yt-dlp](https://github.com/yt-dlp/yt-dlp),
en mode recherche de métadonnées : trois résultats, aucun téléchargement audio
ou vidéo, aucun accès direct aux flux. Les titres doivent correspondre exactement
après retrait des seules mentions de présentation (« clip officiel », par exemple).
Les variantes live, remix ou version étendue ne sont pas assimilées à l’original.
La chaîne doit correspondre à l’artiste, à sa chaîne VEVO ou Topic ; oEmbed
revérifie les métadonnées avant sélection. Une égalité de score reste ambiguë.

Pour les pochettes manquantes, [DDGS](https://github.com/deedy5/ddgs) recherche
`artiste titre site:open.spotify.com/track`. Les moteurs actifs configurés sont
Yahoo et DuckDuckGo, avec une seule recherche simultanée. Bing est désactivé dans
la version figée de DDGS ; il n’est pas présenté comme une source. Si aucun
moteur configuré n’est disponible, la recherche échoue sans repli automatique
vers d’autres moteurs.

Les trois premières pages de morceau Spotify sont vérifiées : domaines et
chemins limités, artiste/titre/version exacts et album concordant lorsqu’il est
connu. Le contenu est limité à 512 Kio. L’image de la page et celle d’oEmbed
doivent correspondre à la même pochette ; leur résolution peut différer. Les
résultats de recherche et leurs vignettes ne suffisent pas à valider une image.
Cette lecture de pages publiques n’ajoute ni compte ni lecteur Spotify.

Si un élément manque encore, le worker recherche un enregistrement dans l’[API MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API),
compare artiste et titre/version et utilise l’ISRC et la durée lorsqu’ils sont
disponibles. Le score du moteur de recherche ne suffit pas. Les résultats
désambiguïsés ou trop nombreux restent sans sélection automatique.

Les liens YouTube complémentaires proviennent des relations de streaming et du
[clip correspondant à l’enregistrement](https://musicbrainz.org/relationship/ce3de655-7451-44d1-9224-87eb948c205d).
Ils sont vérifiés via oEmbed avec les contrôles artiste/titre/version existants.
Seule une correspondance suffisamment étayée et sans égalité de score est
sélectionnée. Cela ne garantit pas l’absence de publicité ni la lecture dans
toutes les régions : le lecteur gère toujours les refus du fournisseur.

En dernier recours pour les pochettes, le worker cherche la face avant approuvée dans
[Cover Art Archive](https://musicbrainz.org/doc/Cover_Art_Archive/API), sur une
édition officielle du même album ou un single portant le titre exact. Une
compilation quelconque n’est pas choisie. L’image est enregistrée comme pochette
temporaire : une pochette du fournisseur musical validé reste prioritaire.
Une miniature YouTube n’est pas présentée comme une pochette d’album.

Ces nouvelles recherches n’utilisent aucun service payant, compte ou clé privée.
Ce mécanisme ne consomme pas de recherche YouTube Data API et peut fonctionner
sans sa clé. Le worker YouTube existant continue ses recherches dans son budget
partagé. Il n’y a pas de recherche Google automatisée : son API Custom Search
est [fermée aux nouveaux clients](https://developers.google.com/custom-search/v1/overview).
La couverture dépend des catalogues publics ; aucun résultat n’est inventé
quand une association fiable manque.

## File, concurrence et ressources

La migration `0018` ajoute `media_discovery` : état, dates, bail et provenance
compacte. Les nouveaux titres sont inscrits une fois dans leur transaction.
Le catalogue antérieur est repris par lots de 100 identifiants, sans lire
l’historique des diffusions. Un passage de rattrapage complet est suivi d’une
pause de six heures. Les entrées les plus récemment inscrites sont traitées
en premier, une par cycle de 30 secondes.

Chaque recherche est limitée à deux minutes ; les appels MusicBrainz sont
espacés d’au moins 1,1 seconde, conformément à ses
[limites d’usage](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting).
Un bail de trois minutes empêche les recherches en double et permet la reprise
après arrêt. Les erreurs sont réessayées après 15 minutes, les autres états
après une journée. Les temporisations fournisseur sont également respectées.
Les éléments déjà disponibles ne déclenchent aucun appel externe.

Chaque outil synchrone s’exécute dans un sous-processus jetable, avec 25 secondes
au maximum, trois résultats et une sortie bornée. L’arrêt ou le dépassement de
délai tue et attend le processus, plutôt que de laisser ses threads tourner.
Les secrets du serveur ne lui sont pas transmis. Les étapes YouTube, pochette
et complément MusicBrainz disposent chacune de 35 secondes. Un résultat valide
est conservé même si une autre source échoue ; une temporisation protège cette
dernière pendant les cycles suivants.

La provenance et la stratégie sont persistées dans la file. Le passage à cette
nouvelle stratégie autorise une nouvelle tentative des anciens échecs avant
leur échéance précédente, une seule fois par version. Les choix manuels et les
médias déjà validés gardent leur priorité. Le contrôle de déploiement vérifie
qu’une tentative de la stratégie gratuite a effectivement été persistée.

Les exclusions globales sont vérifiées avant les appels et avant publication.
Les lectures existantes continuent à cumuler exclusions globales et personnelles.
Les révisions protègent les corrections et choix manuels, y compris un refus
explicite. Une recherche YouTube déjà en cours n’est pas écrasée. Aucune
transaction SQLite n’est gardée pendant les appels distants ; les opérations SQL
du nouveau worker passent par des threads. L’historique reste intact.

La supervision affiche « Recherche automatique des médias ». Le conteneur possède
son propre contrôle de santé et des limites de 256 Mio et 0,25 CPU. La file est
incluse dans les sauvegardes ; les anciennes archives restent importables.

Le profil conteneur avec une supervision simultanée a confirmé un arrêt OOM
à 128 Mio. À 256 Mio, il termine sans OOM ; la somme RSS échantillonnée des
processus atteint environ 202 Mio (elle peut compter des pages partagées plusieurs
fois). Seule la limite du worker de découverte est augmentée.

## Vérifications

Les tests couvrent la validation des sources et versions, l’ISRC, le rythme,
les relations de clips, l’idempotence, le bail expiré, les exclusions pendant
les appels, les choix manuels, les images reçues entre-temps, les recherches
YouTube concurrentes, les erreurs et la restauration. Les parcours navigateur
vérifient la supervision et les médias aux formats 1280/414/412/320 px.

Un essai public réel sur « Été avec toi » d’Adèle Castillon a trouvé une pochette
validée, mais aucune vidéo automatiquement sélectionnable. Une réponse 503 de
MusicBrainz a également été observée : elle illustre la nécessité des reprises,
sans démontrer une couverture universelle. Les nouveaux essais yt-dlp ont aussi
trouvé et vérifié les clips officiels d’Adèle Castillon et Lady Gaga. La lecture
directe d’une page Spotify connue a validé la pochette d’Adèle Castillon ; les
résultats DDGS restent variables et une absence n’est pas transformée en choix.
La CI et le déploiement doivent
être vérifiés avant d’annoncer la livraison en production.

## Pistes complémentaires proposées

Ces idées ne sont pas ajoutées à cette livraison :

- Donner la priorité aux éléments manquants de la daily et de l’écoute courante,
  puis parcourir l’historique aux heures creuses.
- Réemployer une pochette pour les morceaux portant le même identifiant d’album
  déjà validé, plutôt que refaire une recherche par titre.
- Conserver une correspondance par ISRC pour les libellés de radio différents
  qui désignent exactement le même enregistrement, sans fusionner les versions.

Les accès gratuits restent soumis aux limitations et changements des sites.
Les versions de yt-dlp et DDGS et leurs dépendances sont figées avec hashes ;
une mise à jour doit conserver les contrôles de correspondance et de ressources.
