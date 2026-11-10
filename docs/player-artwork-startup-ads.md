# Lecteur, pochettes temporaires et publicités de démarrage

Travail du 10 novembre 2026, points 5 à 7 du TODO local. Le benchmark k6 est différé. Ce travail ne démontre aucun gain de latence du premier chargement.

## Commandes clavier

Hors champs de saisie et contrôles interactifs, `Espace` alterne lecture/pause ; `Q` baisse et `D` augmente le volume de 0,02. Le volume reste entre 0 et 1 ; le réglage utilise les mêmes commandes que la souris, pour les radios, extraits et vidéos. Les combinaisons Ctrl/Alt/Meta et la composition de texte sont ignorées. Maintenir Espace ne provoque pas des bascules répétées ni un défilement quand le raccourci est actif. Une bulle d’aide traduite et `aria-keyshortcuts` indiquent les commandes.

La pause d’une radio conserve la connexion et coupe le son. Cela permet de reprendre le direct sans ouverture volontaire d’une nouvelle session. Les reconnexions après erreur restent bornées à trois essais ; une connexion interrompue par le fournisseur peut néanmoins déclencher une nouvelle annonce lors de sa réouverture.

## Pochette temporaire : choix et limites

La recherche Google ciblant Spotify a été étudiée : [Custom Search JSON API](https://developers.google.com/custom-search/v1/overview) est fermée aux nouveaux clients et annonce une transition avant le 1er janvier 2027. Une image isolée de résultat Google ne prouve pas la correspondance d’un morceau.

Le traitement de fond réutilise les résultats structurés Deezer/iTunes déjà obtenus. À défaut de correspondance exploitable, il peut utiliser la [recherche officielle Spotify](https://developer.spotify.com/documentation/web-api/reference/search), uniquement avec les identifiants serveur configurés. Aucun appel fournisseur n’est ajouté aux lectures HTTP du catalogue, aux classements ni au chargement de la page. Aucune identité d’enregistrement, durée ou préécoute n’est déduite d’une image.

Le score sur 100 est une règle explicable, **pas une probabilité de justesse** :

| Élément vérifié | Points |
| --- | ---: |
| Artiste identique après normalisation de casse, accents et ponctuation | 45 |
| Titre identique après la même normalisation | 45 |
| Fournisseur connu, identifiant, album, lien et image HTTPS présents | 10 |

Seuil retenu : **100/100**. Les mentions live, remix et invités sont conservées. Si plusieurs correspondances au seuil proposent des images différentes, aucune n’est choisie. Si elles désignent la même image, celle-ci peut décorer le titre sans sélectionner arbitrairement un enregistrement. Le score et un lien vers sa source sont affichés dans la fiche.

Une pochette validée conserve la priorité. Une pochette transmise par la radio pendant la recherche ne peut pas être écrasée par le résultat tardif. Les anciennes entrées automatiques dépourvues d’image sont mises en attente lors des passages existants de la collecte, du classement prioritaire ou du rattrapage, puis traitées par lots de deux. Cela évite un nouveau balayage périodique du JSON du catalogue. Les étapes de recherche d’enregistrement déjà terminées et enregistrées ne sont pas relancées. Les corrections manuelles et exclusions globales restent respectées. La fiche publique applique désormais ensemble les exclusions personnelles et globales, y compris avec une session administrateur ; les endpoints dédiés à l’inspection administrative restent disponibles. Les résultats négatifs/ambigus sont conservés ; les erreurs sont réessayées au plus tôt au cycle différé de 15 minutes. Les refus Spotify 401/403 mettent aussi les recherches partagées en attente 15 minutes (autres erreurs : au moins 60 secondes).

Échantillon réel de cinq recherches :

| Recherche | Résultat prudent observé |
| --- | --- |
| Phil Collins — You Can't Hurry Love | Plusieurs pochettes, refus de choisir malgré des correspondances exactes |
| BEN PLG — Histoire sans fin (feat Mathilde) | Pas de correspondance au seuil ; repli Spotify en erreur, aucune image devinée |
| Mosimann — Soon | Pas de correspondance au seuil ; repli Spotify en erreur, aucune image devinée |
| Phil Collins — You Can't Hurry Love (Live) | Correspondance iTunes au seuil, album et lien cohérents avec la version live |
| Artiste/titre synthétiques inexistants | Zéro candidat Deezer/iTunes ; repli Spotify en erreur, aucune image devinée |

Le repli Spotify est vérifié par réponses simulées, y compris refus et indisponibilité ; **sa disponibilité réelle n’est pas établie par cet échantillon**. La stratégie privilégie l’absence d’image à une pochette douteuse. Les liens distants peuvent expirer ; le repli visuel existant demeure nécessaire.

## Publicités : méthode de vérification

Signalement utilisateur : publicités au démarrage sur « tous on va dire », sans liste précise ni confirmation sur pause/reprise. Des prélèvements ont été effectués sur les **26 flux configurés**, sans modifier leurs URL. Une requête par radio, trois simultanées au maximum, arrêt après environ 35 secondes réseau ou environ 640 Kio ; les tampons du fournisseur peuvent produire davantage de secondes audio que de secondes réseau. Les 26 réponses sont HTTP 200 et fournissent des trames MP3/AAC décodables. La coupure volontaire au milieu d’un paquet AAC produit une erreur de fin de décodage sur certains extraits ; ce constat local ne constitue pas un diagnostic de panne de la radio.

Outils installés uniquement dans l’environnement local : PyAV/FFmpeg et Faster Whisper, modèles `small` et `tiny` pour compléter les extraits, calcul CPU int8. Les fichiers audio et transcriptions restent dans `.plan/`, ignoré par Git. Les transcriptions automatiques servent d’indices et peuvent contenir des erreurs ; elles ne prouvent pas qu’un extrait musical sans texte reconnu soit exempt de publicité.

Pour distinguer antenne et démarrage, un second prélèvement compare deux connexions à la même URL : B démarre 15 secondes après A ; A reste connectée 85 secondes, B 65 secondes. Une annonce recommençant au début de B alors que A poursuit le programme indique une insertion par session. Un simple statut HTTP, le nom d’un chemin contenant `adwz2`, une redirection ou une annonce entendue une seule fois ne suffisent pas à cette conclusion.

Résultats du premier passage (départs entre 08:28 et 08:33 UTC) :

| Radio | Annonce identifiée dans l’extrait initial |
| --- | --- |
| Skyrock national | Atol |
| Skyrock Paris Île-de-France | Atol |
| Skyrock Lille | Atol |
| Skyrock Béthune | Atol |
| Skyrock Marseille | Atol |
| Skyrock 100 % Français | Atol |
| Skyrock Klassiks | Atol |
| Skyrock Abidjan | Atol |
| Skyrock Alger | Atol |
| Skyrock Casablanca | Atol |
| Skyrock Tunis | Atol |
| Skyrock La Nocturne | Atol |
| Skyrock PLM | Partenariat/promotion de la station, classification commerciale limitée |
| Hit Skyrock | Identification incertaine, transcription insuffisante |
| Skyrock Urban Music Non-Stop | Aucune annonce identifiée sur la fenêtre ; absence non prouvée |
| Skyrock Hit US | Aucune annonce identifiée sur la fenêtre ; absence non prouvée |
| Skyrock Premier sur le Rap | Aucune annonce identifiée sur la fenêtre ; absence non prouvée |
| Skyrock Rap & RnB Non Stop | Aucune annonce identifiée sur la fenêtre ; absence non prouvée |
| NRJ | Promotion de l’application NRJ |
| Europe 2 | Ford |
| Fun Radio | Lidl |
| RFM | Ixina |
| Hit West | La Poste |
| FIP | Aucune annonce identifiée sur la fenêtre ; absence non prouvée |
| Mouv’ | Aucune annonce identifiée sur la fenêtre ; absence non prouvée |
| ENJOY 33 | Aucune annonce identifiée sur la fenêtre ; absence non prouvée |

Deuxième passage avec connexions A/B décalées : comparaison d’enveloppes audio RMS à 20 ms et transcriptions, sans conserver les messages dans ce rapport. Le décalage audio inclut les tampons réseau et n’est pas une mesure de latence d’antenne.

| Radio | Observation A/B | Conclusion bornée à cet essai |
| --- | --- | --- |
| Skyrock national | Corrélation 0,93–0,97, décalage stable 16,24 s, annonces continuant au milieu de B | Publicité intégrée au programme de cette fenêtre ; aucun nouveau pré-roll isolé dans B |
| NRJ | Corrélation 0,94–0,99, décalage stable 15,80 s | Même programme, aucun nouveau pré-roll isolé dans B |
| Europe 2 | Corrélation 0,99–1,00, décalage stable 16,80 s | Même programme, aucun nouveau pré-roll isolé dans B |
| Fun Radio | Cuisinella dans A, Aldi dans B ; corrélation des débuts < 0,30, puis > 0,96 après 35 s de B au décalage stable de 17,68 s | Insertion propre à la session observée avant jonction au même direct |
| RFM | Corrélation 0,90–0,96, décalage stable 16,40 s | Même programme, aucun nouveau pré-roll isolé dans B |
| Mouv’ | Corrélation 0,93–0,97, décalage stable 16,42 s | Même programme, aucun nouveau pré-roll isolé dans B |
| FIP | Corrélation 0,95–0,97, décalage stable 16,70 s | Même programme, aucun nouveau pré-roll isolé dans B |

Le premier passage Skyrock trouve le même spot au début de plusieurs connexions espacées sur des radios différentes. Cela est compatible avec du pré-roll, mais le second passage national montre aussi des publicités dans le programme commun : ces deux phénomènes ne doivent pas être confondus. La fréquence des annonces dépend potentiellement de la session, de l’adresse réseau, du créneau et du fournisseur ; ces facteurs n’ont pas été isolés.

## Possibilités et décision sur les flux

Les flux configurés sont déjà directs : domaines Skyrock, NRJ Audio, LMN (Europe 2/RFM), Fun Radio redirigeant vers Audiomeans, Radio France, Revma (Hit West) et Infomaniak (ENJOY 33). Les redirections observées sont suivies sans modification des paramètres. La [page officielle des radios Skyrock](https://skyrock.fm/radios) ne fournit pas une garantie générale de flux équivalent sans publicité. [NRJ Global](https://www.nrjglobal.com/cgv-radio-audio-2027-nrj-global-facilite-lacces-au-media-radio-audio-et-ouvre-de-nouveaux-leviers-de-performance-aux-annonceurs/) décrit son offre de pré-roll digital ; la disponibilité de ce format ne prouve pas son insertion sur chaque session NRJ observée.

La distinction FM/digital compte : [Radio France](https://www.radiofrance.com/professionnels/regie-publicitaire/fip) indique que les espaces numériques FIP sont ouverts à la publicité. L’absence de publicité sur une antenne ne garantit donc pas son absence dans un flux numérique. [Infomaniak](https://www.infomaniak.com/fr/support/faq/1831/gerer-la-publicite-du-streaming-radio) documente également l’insertion d’annonces directement dans le flux avant le direct : retirer un lecteur publicitaire de la page ne suffirait pas dans ce cas.

Aucun flux officiel alternatif préservant le même programme et démontré sans publicité n’a été validé lors de cette étude. **Aucune URL n’est modifiée.** Retirer un paramètre d’origine ou inventer une route sans publicité ne garantit ni la continuité du programme ni la disparition des annonces. Une éventuelle variante officielle devrait être comparée en audio avec le flux actuel avant toute bascule ; passer à HLS impliquerait aussi de valider la compatibilité des navigateurs, sans garantie publicitaire.

La mesure déjà disponible est de conserver la connexion lors de pause/reprise et de limiter les reconnexions après panne ; les parcours navigateur le vérifient sans nouvelle requête audio. Elle réduit les occasions de relancer une session, **sans supprimer les publicités au premier lancement ni celles diffusées à l’antenne**. Il n’y a pas de nouvelle solution de suppression retenue : la dernière étape conditionnelle du point 7 reste non cochée.

## Validation

Parcours Chrome à 1280/414/320 px : raccourcis, bornes, saisie, boutons, répétition d’Espace, absence de défilement ; reconnexions, annulations et navigation persistante. Parcours des pochettes : score/source, image temporaire, remplacement par image validée et absence de débordement. 429 tests Python et 16 tests JavaScript réussis localement, Ruff lint/format, syntaxe JavaScript et configuration des 26 radios validés. La suite Python inclut deux tests de contrôleur k6 préexistants hors de cette livraison. Les statuts CI et déploiement sont consignés dans le TODO local après vérification.
