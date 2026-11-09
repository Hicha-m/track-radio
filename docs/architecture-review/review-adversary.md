# Revue adversariale — Architecture Radio Tracker

Date : 9 novembre 2026. Cible : `ARCHITECTURE.md`, sans modification. Examen documentaire et statique ; aucun accès à la production ni bug d’exécution démontré.

**Verdict : description utile et prudente de l’existant, mais contrat insuffisant pour des développements indépendants touchant les mêmes données.** Aucun constat critique. Trois lacunes de priorité haute et une précision moyenne.

## A1 — Haute : protocole d’écriture partagé du catalogue non contractuel

Document : lignes 109–111 et 138. « Les choix manuels restent distincts » ne fixe pas le comportement d’un résultat réseau calculé sur une ancienne version.

Contre-exemple : un module d’administration sélectionne manuellement une version pendant qu’un module d’enrichissement prépare un complément. Le second peut sauvegarder son ancien objet complet, tandis que le premier pense sa sélection prioritaire. Tous deux peuvent prétendre conserver la distinction manuel/automatique.

Preuves existantes : `app/services/catalog.py:523` capture une révision et un instantané ; lignes 547–551 conditionnent l’UPDATE à cette révision et à `manual=False`. `app/catalog_api.py:587` et lignes 593–608 recontrôlent la révision après I/O puis effectuent une mise à jour conditionnelle. Le code contient donc des protections que le contrat n’explique pas.

Traitement proposé : formaliser les auteurs autorisés par donnée, la priorité du choix manuel, la comparaison de révision avant écriture et le comportement de conflit. Reprendre les conventions prouvées ; ne pas imposer une exclusivité d’écriture fictive au worker.

## A2 — Haute : atomicité diffusion / état de collecte et enveloppe de concurrence absentes

Document : lignes 82–103 décrit la distinction observation/rediffusion et l’état durable, mais ne fixe ni unité transactionnelle ni hypothèse sur le nombre de collecteurs.

Contre-exemple : un adaptateur ajoute un passage puis actualise l’état dans une transaction séparée ; un autre fait les deux ensemble. Après interruption, une observation peut être retraitée différemment. Autre divergence : un nouveau déploiement multiplie les workers en supposant que l’état durable suffit à sérialiser deux collectes simultanées d’une station.

Preuve existante : `app/services/collector.py:172` à 191 ajoute le passage, flush, associe `play_id` à `station.collection_state`, puis commit dans la même session. `compose.production.yaml` déclare un service collector ; cela ne prouve pas la sûreté d’exécutions concurrentes supplémentaires.

Traitement proposé : écrire l’invariant transactionnel et l’enveloppe réellement supportée ; différer la multiplication des collecteurs jusqu’à un protocole de coordination et des essais dédiés. Ce constat n’est pas la preuve d’un doublon actuel.

## A3 — Haute : identité musicale partagée trop peu définie

Document : lignes 100–103 parle d’identité normalisée artiste/titre sans préciser les informations à préserver ni les fonctions à réutiliser.

Contre-exemple : un module de recherche supprime featuring et suffixes de version pour construire la clé, un autre reprend le libellé complet. Ils emploient tous deux une identité « normalisée », mais associent différemment catalogue, exclusions et regroupement statistique.

Preuves existantes : `app/services/track_identity.py:9` à 12 préserve explicitement versions et featuring ; `grouping_columns` lignes 15–27 conserve un repli séparé pour les lignes anciennes sans identité. `app/services/exclusions.py:66` emploie une normalisation dédiée au couple d’exclusion, qui ne doit pas être assimilée automatiquement au hash musical.

Traitement proposé : nommer la fonction d’identité commune et ses invariants ; distinguer identité musicale, identité de diffusion et correspondance des exclusions. Ne pas réimplémenter une normalisation différente dans chaque module.

## A4 — Moyenne : exceptions de visibilité et invalidation à préciser

Document : lignes 208–213 et 229–233 impose correctement exclusions avant agrégation et clés de cache sensibles à la visibilité. La phrase « résultats concernés » laisse toutefois le périmètre des accès directs implicite.

Contre-exemple : un nouvel écran d’administration traite un accès direct à une fiche comme une statistique et la rend introuvable ; un autre conserve l’inspection administrative pour corriger une fiche exclue. Les agrégats restent filtrés dans les deux cas, mais les parcours divergent.

Preuves existantes : `app/services/exclusions.py:95` à 105 autorise explicitement l’inspection authentifiée d’un titre globalement exclu. Le classement, lui, inclut les exclusions et la révision globale dans sa clé (`app/services/stats.py:16` à 27), avant application SQL des conditions (lignes 36–37). Cette exception d’inspection ne doit jamais devenir un contournement des exclusions dans les calculs administratifs.

Traitement proposé : ajouter une matrice courte consultation/agrégats/inspection/modification, puis lier chaque famille aux fonctions communes de contrôle et à son invalidation. La règle d’exclusion préalable déjà présente reste une force ; ne pas la présenter comme absente.

## Limites et suite

Les scénarios sont des tests de suffisance du document, pas des failles constatées dans l’application. Aucun changement de moteur, file externe, API ou structure de code n’est nécessaire pour corriger ces lacunes documentaires. Faire adopter les règles tirées du code, garder ouvertes les décisions de concurrence et de frontières qui ne sont pas encore établies.
