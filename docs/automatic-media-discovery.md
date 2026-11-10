# Recherche automatique des pochettes et vidéos

La découverte est effectuée par `python -m app.discovery_worker`, dans un
conteneur distinct de l’API et du collecteur. L’utilisateur n’a pas à rechercher
ou coller un lien pour lancer cette tâche. Les lecteurs utilisent les résultats
persistants existants, sans attendre une recherche pendant le chargement.

## Sources et sélection

Le worker recherche un enregistrement dans l’[API MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API),
compare artiste et titre/version et utilise l’ISRC et la durée lorsqu’ils sont
disponibles. Le score du moteur de recherche ne suffit pas. Les résultats
désambiguïsés ou trop nombreux restent sans sélection automatique.

Les liens YouTube proviennent des relations de streaming et du
[clip correspondant à l’enregistrement](https://musicbrainz.org/relationship/ce3de655-7451-44d1-9224-87eb948c205d).
Ils sont vérifiés via oEmbed avec les contrôles artiste/titre/version existants.
Seule une correspondance suffisamment étayée et sans égalité de score est
sélectionnée. Cela ne garantit pas l’absence de publicité ni la lecture dans
toutes les régions : le lecteur gère toujours les refus du fournisseur.

Pour les pochettes, le worker cherche la face avant approuvée dans
[Cover Art Archive](https://musicbrainz.org/doc/Cover_Art_Archive/API), sur une
édition officielle du même album ou un single portant le titre exact. Une
compilation quelconque n’est pas choisie. L’image est enregistrée comme pochette
temporaire : une pochette du fournisseur musical validé reste prioritaire.
Une miniature YouTube n’est pas présentée comme une pochette d’album.

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

Les exclusions globales sont vérifiées avant les appels et avant publication.
Les lectures existantes continuent à cumuler exclusions globales et personnelles.
Les révisions protègent les corrections et choix manuels, y compris un refus
explicite. Une recherche YouTube déjà en cours n’est pas écrasée. Aucune
transaction SQLite n’est gardée pendant les appels distants ; les opérations SQL
du nouveau worker passent par des threads. L’historique reste intact.

La supervision affiche « Recherche automatique des médias ». Le conteneur possède
son propre contrôle de santé et des limites de 128 Mio et 0,25 CPU. La file est
incluse dans les sauvegardes ; les anciennes archives restent importables.

## Vérifications

Les tests couvrent la validation des sources et versions, l’ISRC, le rythme,
les relations de clips, l’idempotence, le bail expiré, les exclusions pendant
les appels, les choix manuels, les images reçues entre-temps, les recherches
YouTube concurrentes, les erreurs et la restauration. Les parcours navigateur
vérifient la supervision et les médias aux formats 1280/414/412/320 px.

Un essai public réel sur « Été avec toi » d’Adèle Castillon a trouvé une pochette
validée, mais aucune vidéo automatiquement sélectionnable. Une réponse 503 de
MusicBrainz a également été observée : elle illustre la nécessité des reprises,
sans démontrer une couverture universelle. La CI et le déploiement doivent
être vérifiés avant d’annoncer la livraison en production.
