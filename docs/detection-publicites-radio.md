# Détecter les publicités sur les radios

## Ce qu’on peut savoir aujourd’hui

Le site peut vérifier les titres collectés et les métadonnées de diffusion, mais cela ne prouve pas qu’un passage est une publicité. Les métadonnées ICY servent surtout à transmettre un titre et ne sont pas un marqueur publicitaire fiable ; la norme NRSC décrit ces métadonnées comme asynchrones et impropres au contrôle précis des insertions ([NRSC-G304, §11.3](https://www.nrscstandards.org/standards-and-guidelines/documents/guidelines/nrsc-g304.pdf)). Une radio peut aussi ne fournir aucun titre pendant une pub, un jingle, une émission ou une coupure technique.

**Conclusion :** une détection automatique est possible, mais une réponse certaine « il y a / il n’y a pas de publicité » ne l’est pas avec les données actuelles. Il faudrait écouter ou analyser le flux audio, ou obtenir des repères fournis par la radio.

## Options gratuites à envisager

| Option | Ce qu’elle détecte | Avantages | Limites |
|---|---|---|---|
| Repères fournis par la radio | Marqueur ou journal de régie, quand la station en fournit un | Le meilleur signal, sans classifier l’audio | Rarement accessible ; dépend de chaque radio |
| Signatures de spots connus | Une publicité déjà enregistrée et présente dans un petit catalogue local | Peut être précise pour les spots connus ; pas d’API payante nécessaire | Ne reconnaît pas les nouvelles pubs ; il faut constituer et maintenir les références |
| Analyse locale du son | Parole, musique, silences, jingles, changements de niveau et répétitions | Peut signaler un segment non musical sans service payant | Un animateur, un flash info et une pub sont tous de la parole ; faux positifs probables |
| Transcription locale et règles | Mots comme « découvrez », marque, URL, prix ou appel à l’action dans une séquence parlée | Peut préciser le motif d’une alerte et traiter des pubs jamais vues | Les pubs sans parole, la musique de fond, les accents et la reconnaissance imparfaite créent des ratés ; calcul plus lourd |
| Vérification manuelle sur une courte capture | L’utilisateur écoute le passage suspect | Utile pour confirmer les alertes et construire le catalogue local | Ne donne pas une réponse immédiate pour toutes les radios |

### Pistes techniques ouvertes

- Un détecteur de silence comme le filtre `silencedetect` de FFmpeg mesure les périodes sous un seuil de volume ; il ne sait pas décider qu’un passage est une publicité ([documentation FFmpeg](https://www.ffmpeg.org/ffmpeg-filters.html#silencedetect)).
- La détection par empreinte audio est adaptée aux spots déjà connus. Une étude sur 11 radios françaises et 2 172 publicités de référence rapporte 99 % de précision et 96 % de rappel dans son protocole ; ces chiffres ne garantissent pas le même résultat sur nos radios ou sur des pubs inconnues ([Khemiri et al., 2013](https://link.springer.com/article/10.1007/s11042-011-0914-y)).
- Une autre piste de recherche combine reconnaissance vocale et classification de texte sans connaître les publicités à l’avance. L’article RADIA rapporte un F1 macro de 87,76 sur son corpus : c’est encourageant pour produire des alertes, mais insuffisant pour promettre une certitude ([Álvarez et al., 2024](https://arxiv.org/abs/2403.03538)).

## Proposition pour ce projet

1. Commencer par une mesure expérimentale sur **une radio choisie** : un worker local ouvre le flux, examine de courts segments et ne conserve que les horodatages et scores. Les octets audio sont traités en mémoire et supprimés immédiatement.
2. Combiner d’abord des signaux peu coûteux : absence de titre connu, détection de parole/non-musique, ruptures sonores et répétition d’un même segment. Les règles seules produisent un état **« segment parlé/non musical »**, pas « publicité ».
3. Après revue manuelle des alertes, ajouter éventuellement les empreintes des spots confirmés. Une transcription locale peut ensuite améliorer les alertes de pubs inconnues, avec un coût CPU supérieur.
4. Afficher trois états : **publicité probable**, **contenu parlé ou non musical**, **inconnu**. Ajouter le niveau de confiance et les heures observées. Ne jamais transformer l’absence d’alerte en preuve d’absence de publicité.

### Décision à prendre

Choisir le périmètre avant toute implémentation :

- **A.** Ne rien enregistrer ; afficher seulement « titre détecté / information indisponible ».
- **B.** Essai local sans conservation audio, sur une radio, avec alertes « non musical probable » à confirmer manuellement — recommandation pour commencer.
- **C.** Constituer aussi un catalogue privé de spots confirmés pour reconnaître leurs rediffusions.
- **D.** Étudier ensuite une transcription locale pour tenter de classer les nouvelles publicités.

Cette note ne met pas en place de capture ou de détection audio. Les options B à D nécessitent ton choix.
