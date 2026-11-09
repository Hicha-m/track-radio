# Benchmark local k6 — Radio Tracker

Ce benchmark représente des **sessions de visiteurs actifs**, pas un débit de requêtes fixé.
Le fichier `profile.json` contient le profil initial validé :

| Sessions | Part | Parcours HTTP |
|---|---:|---|
| Radios/écoute | 60 % | Accueil, radios, sélection d’une radio, titres du lecteur toutes les 20 s, cartes toutes les 30 s, consultation d’une fiche |
| Classement | 25 % | Accueil, période 1/7/30 jours, classement toutes les 30 s, consultation d’une fiche |
| Recherche | 15 % | Accueil, recherche des derniers passages, recherche sur un artiste/titre reçu, fiche et historique du titre |

Les pauses entre actions durent de 10 à 25 s. Une session dure au moins 120 s.
La répartition est probabiliste avec graine fixe : à faible concurrence, il faut suffisamment
longtemps pour couvrir tous les parcours. Le lanceur refuse une conclusion favorable si
une route a moins de cinq échantillons ou si l’ensemble a moins de vingt réponses API.
Ces minima empêchent les conclusions sur une route absente ; ils ne garantissent pas une
précision statistique du p90. Les effectifs restent visibles dans les résultats.

## Installation reproductible

Python 3.12+, Linux avec `/proc`, curl et k6 **2.3.0** sont nécessaires. Pour Linux x86_64 :

```bash
curl -fL https://github.com/grafana/k6/releases/download/v2.3.0/k6-v2.3.0-linux-amd64.tar.gz -o /tmp/k6-v2.3.0.tar.gz
printf '39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1  /tmp/k6-v2.3.0.tar.gz\n' | sha256sum -c -
tar -xzf /tmp/k6-v2.3.0.tar.gz -C /tmp
mkdir -p "$HOME/.local/bin"
install -m 755 /tmp/k6-v2.3.0-linux-amd64/k6 "$HOME/.local/bin/k6"
```

## Exécution depuis la racine du dépôt

Afficher le plan, **sans trafic** :

```bash
python3 scripts/loadtest/run.py
```

Un seul palier, avec trafic réel sur la cible configurée :

```bash
python3 -u scripts/loadtest/run.py --run --vus 1 --k6 "$HOME/.local/bin/k6" --output .plan/k6-pilot
```

Campagne par doublement, puis dichotomie entière, maximum 500 VUs :

```bash
python3 -u scripts/loadtest/run.py --run --k6 "$HOME/.local/bin/k6" --output .plan/k6-campaign
```

Un fichier alternatif se passe avec `--config chemin/profil.json`. Conserver ce fichier
avec les résultats. Utiliser un dossier de sortie différent par campagne.
Une exécution peut durer longtemps : chaque palier comporte 15 s de montée, 30 s de
préchauffage, 300 s de mesure et 5 s de descente, puis 30 s de récupération.
Le premier échec à 1 VU ne prouve pas une capacité de zéro utilisateur : il indique que
le profil ne respecte déjà pas le critère, à analyser avant toute montée supplémentaire.
Un verdict `inconclusive` arrête la campagne et ne resserre pas la dichotomie.
Les bornes sont rejouées deux fois ; si elles ne se reproduisent pas, ne pas annoncer
une limite stable. Une borne à 500 conforme indique seulement « au moins 500 selon ce profil ».

Le seuil convenu est **p90 ≤ 1 000 ms par route API** pendant la fenêtre stable de chaque
palier, avec moins de 1 % d’erreurs comme critère opérationnel initial explicitement
configuré. Le taux inclut aussi les réponses JSON invalides. Le seuil d’arrêt de sécurité
est de 5 % d’erreurs ; les pauses de préchauffage et de montée ne sont pas incluses dans
les percentiles API. Les premières visites restent donc à mesurer séparément.

Le générateur est observé toutes les deux secondes (CPU Linux, cœur le plus occupé,
mémoire, swap, débit réseau agrégé). Une CPU globale au-dessus de 80 % ou une mémoire
disponible sous 10 % pendant cinq observations consécutives arrête le test. Une hausse
du swap rend le résultat non concluant. Cette observation ne mesure pas toute la charge
de Windows sous WSL et ne démontre pas à elle seule la marge de la liaison réseau.

## Observation Oracle

Le workflow manuel `Capacity observation` utilise les secrets SSH et la clé d’hôte déjà
configurés dans GitHub. Il ne génère aucune charge. Il collecte de 10 à 3 600 secondes
les ressources agrégées de l’hôte et des services API/collector/tunnel, les limites
CPU/mémoire, leur santé et l’âge du heartbeat du collecteur. Télécharger son artefact
`server-aggregates` pour corréler les horodatages avec la campagne. Il ne remplace pas
une surveillance en direct et un mécanisme d’arrêt côté serveur.

## Périmètre et limites

- Le générateur doit être hors d’Oracle. Éviter les autres charges sur le poste ; garder
  le même réseau, versions, profil et paramètres entre essais, puis documenter les écarts.
- Scénario HTTP, sans moteur de rendu : il ne mesure pas les écrans utilisables ni la
  lecture audio. Les visiteurs sont anonymes, sans exclusions personnelles préchargées ;
  les exclusions globales sont appliquées par l’API. Aucune modification d’exclusion.
- Les ressources locales de la page sont demandées une fois par session. Les requêtes
  de navigation et de rafraîchissement sont reproduites. Les caches HTTP fins, le
  viewport et les requêtes simultanées du navigateur sont simplifiés (requêtes séquentielles).
- Aucune requête POST, aucune action administrateur, aucune purge de cache ni redémarrage.
- Aucun flux audio, YouTube, fournisseur de métadonnées ou image tierce n’est sollicité
  par le générateur. Les redirections ne sont pas suivies. La capacité mesurée concerne
  le site/API et son chemin réseau public, pas les hébergeurs audio externes.
- Les réponses restent en mémoire pour choisir un titre et valider leur structure ;
  seuls les compteurs et durées sont écrits. Les noms de titres/artistes, cookies,
  adresses IP/MAC, nom de machine, variables d’environnement et secrets ne sont pas exportés.
- Les résultats JSON restent locaux dans `.plan/`, ignoré par Git. Le manifeste contient
  le profil, les empreintes du scénario/configuration, la version de k6 et la configuration
  système minimale. Ne pas publier de traces HTTP brutes ou de fichiers `.env`.
- Un changement de version en production, de charge de fond, de cache ou de volume de
  données affecte la comparaison. Consigner ces éléments avant/après les essais.

Sources : [modèles de charge](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/),
[qualification du générateur](https://grafana.com/docs/k6/latest/testing-guides/running-large-tests/).
