# PRD — Radio Tracker

Version de travail du 9 novembre 2026. Référence applicative examinée :
`b4d012d77af690c245f37e48d846a33ba73b34e1`. Documents associés :
[architecture actuelle et arbitrages](ARCHITECTURE.md),
[guide technique](docs/guide-technique.md), [diagnostic de collecte](docs/diagnostic-collecte.md).

## 1. Finalité et gouvernance

Radio Tracker aide à retrouver un morceau entendu à la radio, consulter ses
passages et l’écouter dans son contexte. Il collecte des métadonnées de diffusion ;
il ne conserve ni ne redistribue les flux audio.

**Priorité confirmée par le product owner le 9 novembre 2026 : usage personnel,
retrouver des titres et écouter les radios.** Les comparaisons statistiques et
l’administration restent des fonctions existantes au service de cet usage.
L’ouverture à plusieurs comptes utilisateurs n’est pas un objectif approuvé.

L’utilisateur est le product owner : il définit les besoins, arbitre les compromis
et valide les décisions qui structurent le code. Les documents distinguent :

- **Existant** : comportement constaté dans les fichiers référencés.
- **Besoin** : résultat attendu, sans imposer sa réalisation technique.
- **Proposition** : option à discuter, non approuvée par sa présence ici.
- **Décision** : choix explicitement confirmé, avec date et conséquences.

Cette version prépare la revue commune. Les objectifs chiffrés et l’architecture
future restent à décider ; publier ce document ne les valide pas.

## 2. Utilisateurs et parcours prioritaires

| Rôle | Besoin | Parcours attendu |
| --- | --- | --- |
| Auditeur personnel, prioritaire | Retrouver un titre entendu | Ouvrir l’historique, choisir radio/période, rechercher artiste ou titre, ouvrir la fiche |
| Auditeur personnel, prioritaire | Écouter une radio et découvrir un morceau | Choisir une station, lire son flux, lancer un extrait ou une vidéo, revenir à la radio |
| Auditeur personnel | Adapter les résultats à ses goûts | Masquer personnellement un titre, retrouver des résultats et statistiques cohérents |
| Administrateur | Maintenir la qualité de son outil | Activer une radio, superviser la collecte, compléter une fiche ou prévisualiser une correction |
| Utilisateur des statistiques | Comprendre ses radios | Comparer répétitions, diversité ou recoupements sur le périmètre visible |

Les préférences personnelles restent attachées au navigateur. L’administration
est un accès privilégié distinct ; elle ne constitue pas un système de comptes
d’auditeurs ou de synchronisation entre appareils.

## 3. Périmètre fonctionnel existant

| ID | Fonctionnalité | Comportement existant | Critère d’acceptation à préserver |
| --- | --- | --- | --- |
| F01 | Accueil et classement | Titres les plus diffusés, choix de radios et période | Les comptes correspondent au périmètre filtré et aux exclusions appliquées avant classement |
| F02 | Catalogue radios | Page `/radios`, recherche de stations ; annexes distinctes de l’accueil | Une recherche permet de choisir la bonne station ; une navigation interne conserve la lecture |
| F03 | Historique et recherche | Recherche par artiste/titre, radio, dates, genre et fréquence | Retrouver une diffusion puis sa fiche sans perdre les filtres ; pagination cohérente |
| F04 | Fiche musicale | Informations enrichies, version choisie, provenance, paroles et derniers passages | Une information absente ou ambiguë n’est pas présentée comme confirmée |
| F05 | Lecteur commun | Radio, extraits, YouTube, volume, retour radio et file d’extraits | Un extrait et la radio ne sont pas audibles simultanément ; reprise conforme au réglage ; pas de rechargement par simple navigation |
| F06 | Reprise radio | Trois réessais automatiques, puis action manuelle | Une erreur ne crée pas une boucle ; pause et changement de source annulent les réessais |
| F07 | Statistiques | Répétitions, diversité, genres, recoupements, décalages et activité horaire | Les chiffres représentent les observations collectées et le périmètre visible, avec leur période |
| F08 | Exclusions | Masquage personnel et blacklist globale cumulés | Les exclusions s’appliquent aussi en administration aux résultats et calculs concernés ; aucune suppression historique par masquage |
| F09 | Export et sélection musicale | CSV/JSON filtrés et préparation de sélections jusqu’à 50 titres | Les titres exclus restent absents ; un export de sélection ne prétend pas créer une playlist sur un compte externe |
| F10 | Administration | Radios, catalogue, exclusions, prévisualisation/application de corrections | Les écritures sont protégées et une correction explicite ne se confond pas avec le masquage |
| F11 | Supervision | État des workers, tentatives, erreurs, réessais et quota YouTube | Distinguer processus actif, observation utile et changement de titre ; une API saine seule ne prouve pas la collecte |
| F12 | Présentation et préférences | Mobile/ordinateur, thèmes clair/sombre, français/anglais | Commandes utilisables aux formats couverts et préférences conservées lorsque le stockage navigateur est disponible |
| F13 | Daily et écoute entière | Sélection datée, vidéos validées réutilisées, vérification d’un lien fourni | Exclusions revalidées avant chaque lecture ; vidéos indisponibles ou ambiguës explicites ; aucune garantie universelle de disponibilité |
| F14 | Réglages d’enchaînement | Autoplay, fondu des extraits et visibilité CSS YouTube mémorisés | Pause/source/filtres annulent le fondu ; le masquage conserve l’iframe ; l’arrêt de l’autoplay termine la suite sans boucle |

La configuration versionnée comprend 26 radios, dont 18 Skyrock. Ce nombre décrit
le catalogue du dépôt ; il ne garantit pas la disponibilité permanente de chaque
flux ni l’activation effective de toutes les radios à un instant donné.

## 4. Règles métier non négociables

1. Une observation répétée d’un même événement ne crée pas une nouvelle diffusion.
   Une vraie rediffusion doit rester distincte, même si l’artiste et le titre sont identiques.
2. Une réponse datée ancienne ne doit pas devenir un nouveau passage courant.
   L’heure de détection et l’heure déclarée par une source ont des significations différentes.
3. Les exclusions personnelles et globales sont cumulées avant les calculs,
   classements, totaux, paginations et résultats concernés, y compris en administration.
   Un accès de maintenance à une fiche ne constitue pas une exemption pour ces calculs.
4. La blacklist globale bloque les nouvelles diffusions concernées ; elle ne
   supprime pas automatiquement les anciennes. Le masquage personnel agit sur
   la visibilité, sans arrêter la collecte pour les autres usages.
5. Une absence de titre courant doit rester explicite. Un précédent visible peut
   être affiché comme dernier titre connu, sans être présenté comme « en cours ».
6. Une pochette temporaire doit correspondre au morceau. La pochette officielle
   sélectionnée reste prioritaire ; un enrichissement ne remplace pas arbitrairement
   une version ou des paroles choisies manuellement.
7. Les indisponibilités et limites de fournisseurs ne justifient ni faux résultat
   ni promesse d’écoute permanente, de suppression des publicités ou de disponibilité des extraits.
8. Les secrets et données de production restent privés. Les diagnostics publiés
   se limitent aux informations techniques nécessaires.
9. Le diagnostic Mouv’ ne reprend que sur nouveau signalement ; sa cause reste
   une hypothèse. Les quatre régions Skyrock écartées précédemment ne sont pas ajoutées.

La conservation historique n’est pas une conservation infinie dans la base active :
une politique d’archivage existe, décrite dans l’architecture. Son évolution est à arbitrer.

## 5. Besoins et ordre de travail

| Priorité | Besoin | État et frontière |
| --- | --- | --- |
| Cadrage actuel — point 1 | Comprendre le produit et les choix techniques | Ces deux documents sont préparés pour revue commune ; aucune restructuration engagée |
| Chantier du 10/11 — chargement | Retrouver et écouter sans attente excessive au premier chargement | Objectif titres/commandes en 2 s confirmé ; compteurs, vues préparées et bundle réalisés, mesures après livraison requises |
| Réalisé — points 3 et 4 | Collecte et écoute Skyrock plus fiables | JSON groupé et reprise du lecteur livrés ; erreurs ponctuelles et limites de réseau restent possibles |
| Livré — point 5 | Piloter le lecteur au clavier | Espace, Q/D par pas de 2 %, saisie et contrôles préservés |
| Livré — point 6 | Améliorer les pochettes de remplacement | Score explicable et seuil prudent, ambiguïtés refusées, priorité aux pochettes validées ; disponibilité Spotify réelle non établie |
| Exploratoire — point 7 | Réduire les publicités au démarrage | Distinguer publicité ajoutée et programme diffusé ; solution non établie |

Les prochains choix doivent d’abord servir la recherche personnelle et l’écoute.
Une refonte générale, une migration de base, des microservices, un système de
comptes ou une nouvelle infrastructure ne sont pas engagés par ce cadrage.

## 6. Résultats attendus et mesure à convenir

Les lenteurs signalées concernent le classement, les titres courants, les images
de station et le tableau de bord administrateur. Elles justifient une mesure ;
elles ne prouvent pas une cause SQL ou un défaut d’architecture.

| Indicateur | Comment le vérifier lors du point 2 | Cible |
| --- | --- | --- |
| Temps avant titres courants et lecture utilisables | Chronométrer le parcours navigateur et séparer les requêtes bloquantes | 2 s, y compris à cache navigateur froid ; recherche particulière mesurée séparément |
| Latence API cache froid/chaud | Comparer les mêmes routes, paramètres, jeux de données et conditions ; documenter échantillon et percentiles | À décider, D02 |
| Fraîcheur acceptable des résultats | Distinguer délai fournisseur, collecte, cache et affichage | Classement courant : jusqu’à 1 min de retard ; avertissement et dernier résultat compatible conservé ensuite |
| Fidélité des résultats | Comparer résultats filtrés et diffusions attendues sur données contrôlées | Aucune régression des règles métier |
| Fiabilité d’écoute | Vérifier décodage natif et reprise après interruption, en précisant le réseau testé | Reprise bornée ; disponibilité fournisseur non garantie |
| Conservation et restauration | Vérifier ce qui reste consultable en ligne et ce qui peut être restauré | Durée et objectifs de restauration à décider, D04 |

Le protocole détaillé, les mesures, plans SQL et corrections relèvent du point 2.
Aucun audit de performance n’est lancé dans ce point 1.

## 7. Décisions du product owner

| ID | Question | État |
| --- | --- | --- |
| D01 | Quel usage guide les décisions ? | **Confirmé le 09/11/2026 : usage personnel, retrouver des titres et écouter les radios** |
| D02 | Quels parcours doivent être rapides en premier, et avec quel délai acceptable à froid ? | Titres courants et commandes utilisables en 2 s, y compris sans cache navigateur ; statistiques/pochettes peuvent suivre. Objectif à revérifier après livraison |
| D03 | Quel retard d’affichage accepter selon le type d’information ? | Classement courant préparé automatiquement, jusqu’à 1 min de retard ; date visible, avertissement ensuite et réessai automatique, aucune vue incompatible avec les exclusions |
| D04 | Quelle période doit rester consultable immédiatement ? Quelles garanties de sauvegarde/restauration ? | À décider ; le réglage d’archivage existant n’est pas une exigence produit nouvellement validée |
| D05 | Quels changements techniques sont justifiés par les mesures ? | Compteurs maintenus et vues préparées acceptés comme direction ; mise en œuvre demandée avant reprise de k6. SQLite et les deux processus sont conservés |

Le [registre technique](ARCHITECTURE.md#8-registre-des-arbitrages) précise les
options et leurs conséquences. Pour chaque décision, conserver choix, raison,
date, critères d’acceptation et conséquences ; ne pas remplacer un « à décider »
par une recommandation supposée approuvée.

## 8. Validation de ce cadrage

La préparation est vérifiable par la présence des fonctions, règles, priorités,
critères et sources dans les deux documents. La revue produit reste distincte :
le product owner doit encore relire le périmètre et arbitrer D02 à D05 au moment
où les preuves nécessaires existent. D01, D02, D03 et la direction de D05 sont confirmées dans les actualisations ci-dessous ; la revue complète n’est pas présumée achevée.

Sources de l’existant : [routes et pages](app/main.py), [catalogue](app/catalog_api.py),
[administration](app/admin_api.py), [statistiques](app/metrics_api.py),
[exports](app/export_api.py), [sélections](app/playlist_api.py),
[configuration des radios](config/stations.json), [lecteur](app/static/player.js),
[exclusions](app/services/exclusions.py), [tests](tests).


## Actualisation du 10 novembre 2026 — travail hors k6

La demande « tout sauf k6 » inclut explicitement les notes à intégrer : sélection
quotidienne, écoute entière, fondu réglable, autoplay YouTube et visibilité de la
vidéo mémorisée. Spotify est exclu par le product owner ; il demande une option CSS
pour masquer le panneau vidéo en conservant la lecture.

Les critères et limites détaillés figurent dans
[chargement, daily et réglages](docs/prepared-loading-and-daily.md). Le chargement
rapide reste un objectif mesuré avant/après, sans garantie de disponibilité des
médias externes ni de capacité sous charge. Le benchmark k6 demeure différé.

La revue commune complète du PRD reste à faire ; cette actualisation ne la coche
pas à la place du product owner. D04 et les garanties de restauration restent
ouverts ; la conservation existante n’est pas modifiée par cette livraison.
