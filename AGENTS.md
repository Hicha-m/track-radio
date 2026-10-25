# Consignes du projet

- Répondre en français et réaliser uniquement la phase ou la tâche demandée.
- Lire `.plan/TODO.md`, le mettre à jour et ne cocher que les tâches vérifiées. Ce dossier reste local et ignoré par Git.
- Préserver les modifications préexistantes et les fichiers sans rapport avec la tâche.
- Appliquer ensemble les exclusions personnelles et globales, y compris en mode admin, avant tous les calculs statistiques et résultats concernés.
- La blacklist globale bloque les nouvelles diffusions. Conserver l’historique ; aucune suppression automatique liée au masquage.
- Préserver les vraies rediffusions. La cause des anomalies Mouv’ reste une hypothèse ; attendre un nouveau signalement pour reprendre ce diagnostic.
- Ne jamais publier de secrets, de fichier `.env` ou de données de production.
- Valider les changements avec les tests adaptés, Ruff et les contrôles JavaScript ; vérifier les parcours navigateur si l’interface change.
- Après validation, créer un commit clair et pousser sur GitHub (`origin/master`), sans push forcé ni fichiers étrangers à la tâche. Ne pas demander à nouveau cette autorisation.
- Vérifier la CI après le push et corriger les échecs liés au travail. Le push déclenche un déploiement automatique après succès de la CI.
- Terminer avec le lien du commit et le statut réel de la CI et du déploiement ; ne jamais annoncer un succès non vérifié.
