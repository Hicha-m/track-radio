# Revue BMad — grille de cohérence et lisibilité

Document évalué : `ARCHITECTURE.md`, 315 lignes. Revue du 9 novembre 2026.
Périmètre : qualité documentaire et capacité à guider des développements séparés ; aucun changement de code, aucun accès à la production. Référence annoncée par le document : `d3ba132`.

**Verdict : bonne cartographie technique de l’existant ; insuffisant comme contrat de cohérence autonome pour construire plusieurs évolutions indépendamment.** Aucun défaut critique établi par cette revue. Deux lacunes hautes concernent le guidage des prochains développements, et non une panne démontrée de l’application actuelle.

## Constats prioritaires

### R1 — Haute : les invariants ne sont pas distingués des détails descriptifs

**Preuves :** le paradigme est correctement nommé aux lignes 21–26. L’identité de diffusion et l’identité musicale sont séparées aux lignes 89–101 ; l’interdiction de supprimer l’historique lors du masquage apparaît aux lignes 92–93 et 181–185 ; l’application des exclusions avant agrégation apparaît aux lignes 229–233. Le registre des lignes 277–294 recense surtout des options futures, sans règles adoptées identifiées par des AD et sans Binds/Prevents/Rule.

**Divergence possible :** une future route admin pourrait traiter l’absence d’exclusions personnelles comme une permission de contourner la blacklist, ou une importation pourrait dédupliquer tous les passages par identité musicale. Le texte permet d’en déduire que ce serait incorrect, mais son organisation ne donne pas un petit contrat vérifiable auquel rattacher les développements.

**Traitement :** discuter puis extraire les invariants déjà approuvés dans une courte section, avec identifiants stables, périmètre et règle. Ne pas transformer les valeurs de timeout ou le choix actuel du moteur en invariants immuables. Les règles d’exclusions sont étayées par `app/services/exclusions.py:48`, qui combine condition globale et liste personnelle.

### R2 — Haute : les propriétaires des mutations et les frontières restent implicites

**Preuves :** API et worker partagent base, modèles et services (lignes 21–24) ; les routes accèdent directement à SQLAlchemy (58–60) ; le catalogue conserve choix manuels et compléments automatiques (109–111). Le tableau des tables (134–143) décrit les données sans préciser qui arbitre leurs modifications. La structure future reste ouverte en D05b/D05c (289–290).

**Divergence possible :** un traitement de fond pourrait considérer une fiche sélectionnée comme remplaçable tandis qu’un module admin la considère comme protégée ; deux nouveaux traitements pourraient chacun s’attribuer les changements de statut d’une même fiche. Ce risque documentaire ne démontre pas que le code actuel écrase des choix manuels.

**Traitement :** discuter une matrice minimale données / acteurs autorisés / service de mutation / règle de conflit. Ratifier les conventions réelles avant d’imposer une nouvelle couche d’abstraction. Documenter les frontières actuelles et les exceptions ; laisser la restructuration générale ouverte.

## Autres constats

### R3 — Moyenne : le registre des arbitrages n’est pas complètement réconcilié avec les résultats

**Preuves :** D02 indique encore des objectifs à valider au point 2 (ligne 285), tandis que les lignes 300–301 mentionnent l’objectif admin confirmé de moins de 2 s et des échantillons l’atteignant. La migration 0016 et l’agrégation sont décrites comme existantes aux lignes 173–177.

**Traitement :** correction documentaire ciblée : distinguer la décision admin déjà acquise des objectifs encore ouverts pour classement et titres courants. Garder l’historique chiffré dans `docs/performance-loading.md` et un lien depuis l’architecture. Pas de changement applicatif nécessaire.

### R4 — Moyenne : les environnements et les conditions d’évolution opérationnelle restent peu définis

**Preuves :** la production, les limites CPU/mémoire, la sauvegarde avant migration et la CD sont couvertes aux lignes 252–269. Le document souligne correctement l’absence de garantie de sauvegarde indépendante et de délai de restauration (187–191). En revanche, la stratégie de test/staging et les conditions nécessaires avant multiplication des processus ou migration de stockage ne sont pas explicitement décidées ou différées avec un déclencheur.

**Traitement :** compléter un court tableau « actuel / à décider / événement déclencheur ». Ne pas inventer un staging ni des objectifs de reprise. Le Compose confirme la topologie API/collector/cloudflared (`compose.production.yaml:26`, `:39`, `:54`) ; la CD attend le succès CI (`.github/workflows/cd.yml:15`).

### R5 — Faible : densité et paramètres qui vieillissent vite

**Preuves :** tables détaillées de fichiers, tables et caches (45–56, 134–143, 199–206), paramètres de reconnexion (119–124), détails d’index (173–177) et mesures (298–310). Ils sont utiles pour comprendre le projet, mais allongent le chemin vers ses règles durables. Un seul schéma illustre les processus ; aucune vue de responsabilité des mutations.

**Traitement :** conserver la cartographie existante comme référence humaine, ajouter au début un contrat court ; ajouter une vue de mutations seulement si elle éclaire les responsabilités. Éviter une réécriture cosmétique en plusieurs documents sans besoin identifié.

## Passage de la grille good-spine

| Dimension | Évaluation |
| --- | --- |
| Paradigme nommé | Satisfait : monolithe modulaire, deux processus |
| Points de divergence réels couverts | Partiel : règles métier présentes mais dispersées ; propriété des mutations implicite |
| AD exécutables et vérifiables | Non satisfait au format spine : aucun AD structuré ; ne signifie pas que les descriptions sont fausses |
| Deferred ne laisse pas diverger les unités | Partiel : restructuration raisonnablement différée, règles de collaboration actuelles encore à ratifier |
| Réalité brownfield | Étayée sur les échantillons examinés : base, exclusions, Compose et déclenchement CD ; revue exhaustive des assertions déléguée au regard dédié |
| Versions et technologies actuelles | Pas de nouvelle technologie choisie ici ; vérifier les dépendances effectives dans la revue dédiée, sans exiger une montée de version |
| Exigences du ticket | Non applicable : aucun ticket Requirements fourni ; le PRD est lié mais la revue n’est pas une validation exhaustive du PRD |
| Spine parent | Non applicable : aucun parent fourni |
| Enveloppe opérationnelle | Bien couverte pour la production ; environnements et déclencheurs d’évolution à expliciter |
| Lisibilité | Bonne progression et liens vers le code ; améliorer l’accès aux invariants et la cohérence du registre |

## Limites et suite proposée

Cette revue ne valide ni la restauration réelle, ni les performances actuelles, ni toutes les routes de l’application. Elle ne propose aucune migration PostgreSQL ni nouvelle architecture cible. Les constats R1/R2 demandent une ratification des règles avec le propriétaire ; R3 peut être corrigé mécaniquement ; R4 peut rester ouvert avec conditions de reprise ; R5 est facultatif. Pour ce petit monolithe, quelques règles explicites suffisent : aucun besoin démontré de microservices ou de multiplication des ADR.
