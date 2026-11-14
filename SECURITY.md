# Sécurité

## Versions suivies

Seule la dernière version publiée (`main`) reçoit des correctifs de sécurité.

## Signaler une faille

**N'ouvrez pas d'issue publique.** Utilisez le signalement privé de GitHub : onglet **Security** du dépôt, puis
**Report a vulnerability**. Décrivez la faille, la version concernée (`prgraph --version`) et, si possible, les
étapes pour la reproduire. Le signalement reste privé jusqu'à la publication d'un correctif.

## Ce qui est dans le périmètre

- **Le serveur local** (`prgraph serve`) : il n'écoute que sur 127.0.0.1, vérifie l'origine et l'hôte des
  requêtes et exige un jeton sur chaque action. Toute requête acceptée depuis un autre site, toute lecture de
  fichier hors du dossier des analyses ou toute exécution de commande est une faille.
- **Le rapport HTML** (`report.html`) : les titres, ancres et URL viennent de sites crawlés et sont traités comme
  hostiles. Tout code exécuté ou tout rapport rendu inutilisable par une donnée crawlée est une faille.
- **Les exports** (`*.csv`, `report.xlsx`) : une cellule interprétée comme formule par un tableur est une faille.
- **Le crawler** : un non-respect de `robots.txt` ou du délai entre requêtes est traité comme un défaut sérieux.

Les identifiants Google (Search Console, GA4) ne sont jamais stockés dans le projet : ils sont lus depuis la
variable d'environnement `GOOGLE_APPLICATION_CREDENTIALS` ou un fichier passé en option.
