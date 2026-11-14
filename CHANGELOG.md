# Journal des versions

Format inspiré de Keep a Changelog ; numérotation SemVer. La version du paquet est `prgraph.__version__`
(relancer `pip install -e .` après une montée de version pour mettre à jour les métadonnées installées).

## 0.5.0 (2026-11-11)

Le rapport devient exploitable par le consultant et présentable au client.

### Rapport HTML

- Deux modes dans le même fichier `report.html` : **Analyse** (l'explorateur, pour travailler) et **Présentation**
  (un deck qu'on feuillette devant le client, une idée par écran, imprimable en PDF, une diapositive par page).
  Bascule dans l'en-tête ; `report.html#presentation` ouvre directement le deck.
- Mode Présentation : couverture, sommaire, verdict en trois tuiles, ce qui rapporte contre ce qui reçoit, carte
  des silos, coût du gabarit, pages rentables mal reliées, plan d'action en cartes « on saura que ça marche »,
  avant / après des chantiers de gabarit, une seule diapositive Méthode. Options `--brand-name`, `--brand-logo`,
  `--brand-color` (neutre par défaut).
- Mode Analyse : verdict lisible d'abord, score qui affiche « avant → après » pendant une simulation, vue silos
  par défaut au niveau des familles de rubriques (variantes de langue regroupées), palette validée pour les
  daltoniens, onglets accessibles au clavier, une ligne de définition sous chaque onglet et un onglet « Lire ce
  rapport », tableaux triables, filtrables, paginés et exportables, thème clair ou sombre, mobile utilisable,
  feuille d'impression.
- Hors ligne : les bibliothèques du graphe (graphology, graphology-library, sigma.js, licences MIT dans
  `prgraph/viewer/vendor/`) sont embarquées dans le rapport ; plus aucun appel à un tiers à l'ouverture (police
  système). `--cdn` garde les balises CDN, avec empreintes d'intégrité.

### Données

- `graph.json` : `families` (rubriques sans préfixe de langue, emplacement de couleur fixe, « autres » au-delà de
  sept), `segments[].family`, `scenario` (chantiers de gabarit appliqués ensemble : alignement, part des pages qui
  rapportent, part du segment le plus sous-alimenté, avant et après), `meta.brand`.
- `alignment.value_pages_pr_share` : part de l'autorité reçue par les pages qui cumulent 80 % de la valeur mesurée,
  la seule mesure qui dit si l'autorité va à ce qui rapporte (la part des classes money + acquisition décrit des
  types d'adresses, elle n'est plus présentée comme « ce qui rapporte »). Écrite dans `findings.md` § 0 et le classeur.
- `alignment.worst_family` : pire écart de rubrique toutes langues, le chiffre de tête du mode Présentation, écrit
  aussi dans `findings.md` § 0 et l'onglet Synthèse pour qu'il ait sa pièce.
- Le scénario avant / après applique exactement les chantiers de gabarit du plan (`scenario.tickets`), avec le
  volume de chaque étape (`scenario.changes`), au lieu d'un jeu de règles à part.
- Période des données business : `--business-period`, ou ligne `# période : ...` écrite par `fetch-gsc` et
  `fetch-ga4` en tête du CSV ; `meta.value_kind` distingue une valeur de trafic (clics, impressions, sessions),
  que le rapport qualifie comme telle, d'une valeur business (revenu, conversions).
- Textes des gains au format français (« +0,48 pt »).
- La jointure des données business, des cibles et des groupes utilise la même normalisation que le graphe :
  avec `--strip-query`, une URL Search Console `?utm_source=...` ou `#ancre` rejoint sa page.

### Tableurs

- `report.xlsx` toujours produit (`--no-excel` pour s'en passer) : onglets Synthèse, Plan d'action, Pages,
  Famine, Gouffres, Segments, Flux entre silos, Modèle, Lisez-moi ; en-têtes en français, filtres, volets figés,
  pourcentages formatés, cellules texte jamais interprétées comme formules. Les CSV restent au format machine
  (virgule, point décimal) pour `diff` et les autres outils : dans un Excel français, ouvrir le classeur, pas le CSV.

### Projet

- Tests automatiques sur GitHub à chaque modification (Linux et Windows, Python 3.11, 3.13 et 3.14), actions épinglées
  sur un commit exact et droits en lecture seule ; mises à jour Dependabot avec 7 jours de délai.
- `CONTRIBUTING.md` (installation, tests, conventions, données de test synthétiques seulement) et `SECURITY.md`
  (signalement privé d'une faille, périmètre).
- Test du débit du crawler : l'écart entre deux requêtes est mesuré à l'envoi, la durée totale côté serveur ;
  il ne dépend plus de la charge de la machine.

### Lanceur et serveur local

- Barre oblique inverse refusée dans les chemins sous `/runs/` sur tous les systèmes (sous Linux, ce n'est pas un
  séparateur mais un caractère de nom de fichier) : la règle de sécurité est la même partout.
- Police système dans le lanceur ; CSP des rapports sans aucune origine tierce (le CDN n'est autorisé que pour un
  rapport produit avec `--cdn`) ; `favicon.ico` répond 204 ; avertissement si une bibliothèque embarquée manque.
- Liste des analyses limitée aux vrais dossiers d'analyse, message d'erreur sur une adresse vide ou invalide,
  délai présenté par requête, avertissement sur le budget de pages, erreur claire quand aucune page n'a été
  récupérée (robots.txt).

## 0.4.1 (2026-11-11)

Corrections issues de la relecture indépendante de 0.4.0.

- Inférence de gabarit : le seuil ne compte que les liens déclarés content / unknown (une page de menu citée
  trois fois dans un texte garde ses trois liens à plein poids) ; la part affichée est celle qui a décidé.
- Nombres : un export où une colonne prouve le séparateur de milliers (« 12,345,678 ») est lu entièrement
  ainsi, et une métrique de comptage « 1,234 » sans décimale à un ou deux chiffres est un millier.
- `--uncrawled leak` : décidé sur la table d'URL seule, plus jamais sur l'ordre des lignes.
- Classes : pages légales et de confiance reconnues sur le segment entier (ou un suffixe légal), plus sur un
  mot en tête de slug ; `/p/` et `/c/` sur le segment entier seulement ; racine d'une langue (`/fr/`) = accueil.
- Précision : UX-09 (tri, pagination, impression) n'est pas traité ; seul le barème du score l'est.

## 0.4.0 (2026-11-11)

Version issue d'un audit complet du projet : sécurité du serveur local, fiabilité du conseil, lecture du rapport.

### Sécurité

- Serveur local (`prgraph serve`) : résolution sûre des chemins sous `/runs/` (plus de traversée par chemin relatif
  au lecteur Windows `C:..\`), `HEAD` refusé, `POST` acceptés seulement en `application/json`, avec un jeton
  anti-CSRF émis au démarrage et vérifié sur `X-Prgraph-Token`, contrôle des en-têtes `Origin` et `Host`,
  `Content-Length` plafonné, erreurs renvoyées en JSON (400 / 500), en-têtes CSP et `nosniff`.
- Rapport HTML : les données injectées échappent `<`, `>`, `&`, U+2028 et U+2029 ; un titre de page `<!--<script>`
  ne vide plus le rapport. Les URL insérées dans le DOM sont échappées et limitées à `http(s)`.
- Lanceur : toutes les valeurs affichées sont échappées.
- Exports CSV et Excel : les cellules commençant par `=`, `+`, `-`, `@` sont neutralisées (injection de formule).

### Modèle et conseil

- Classes business décidées sur les segments de chemin, jamais sur des sous-chaînes (`/blog/legal-tech` est un
  article, `/legal` une page légale) ; nouvelle classe `confiance` (à propos, équipe, presse, FAQ) ; `contact` en money.
- Inférence de gabarit graduelle (poids `max(gabarit, poids × (1 − part des pages émettrices))`), jamais appliquée
  aux pages stratégiques ni aux pages money, liste des cibles touchées dans `findings.md` et l'onglet Modèle ;
  option `--no-boilerplate-inference`.
- Tickets « gouffre » : le gain affiché est celui de l'action prescrite (liens relégués en pied de page, ou regroupés
  derrière une page centre légal), plus jamais celui d'une suppression des liens.
- Une redirection vers un autre hôte est une fuite (`lost_external`), pas un nœud ; les cibles découvertes mais non
  crawlées sont des fuites par défaut (`--uncrawled leak|nodes`).
- Le vecteur de téléportation `--restart` est conservé par `simulate`, par le plan d'action et par le what-if du
  rapport (`meta.teleport`).
- Score d'alignement : l'accueil est exclu de la distance (clics de marque) ; le pire écart de segment et la
  corrélation de rang sont affichés à côté du score avec un barème.
- Trous noirs : seulement des pages réellement alimentées (au moins un lien entrant, PageRank au-dessus de la
  médiane), jamais des fichiers (PDF, images).
- Quadrants : plus de « candidates à la suppression » ; « à instruire avec les clics et impressions ».
- `diff` compare aussi la part d'équité et le rang (le score log 0-100 dépend du minimum et du maximum du site).
- Nombres : la convention « virgule = milliers » ou « virgule = décimale » est décidée par colonne (`1,234` anglais
  n'est plus lu 1,234).
- Efforts des tickets présentés comme des ordres de grandeur par défaut, à confirmer.

### Crawl

- `robots.txt` : jeton `pagerank-graph` respecté en plus de `*`, 5xx ou erreur réseau = crawl refusé (RFC 9309),
  URL interdites écrites avec le statut `robots` (jamais supposées en 200), aucun rapport sur un site sans page
  récupérée ; `stats` distingue `fetched`, `blocked`, `errors`.
- Délai appliqué par requête quel que soit le nombre de workers (plancher 0,1 s), repli sur 429 et 503 avec
  `Retry-After`, corps lu en flux et plafonné à 5 Mo, agent `pagerank-graph/<version>`.
- Sitemap : les URL non crawlées faute de budget sont écrites avec le statut `uncrawled`.
- `crawl-static` : pages `.html` inventoriées sous leurs deux formes (canonical de l'une vers l'autre), PDF et
  documents présents en 200, `index.html` retiré en dernier segment seulement, `rel="sponsored"` et `rel="ugc"`
  traités comme nofollow (fonction commune aux deux crawlers).

### Sorties

- `findings.md` : « inatteignable » au lieu de `-1`, matrice des silos limitée aux dix plus gros segments, libellé
  des zones de gabarit complet, chemins au lieu d'URL complètes, part d'équité (`pr_share_pct`) à côté du score.
- Erreurs d'entrée rendues en message (position inconnue, `--cap-position` mal formé, poids invalide, arêtes en
  chemins relatifs, règles de segments ignorées signalées).

### Tests

- Nouveaux tests : serveur HTTP (traversée, CSRF, jeton, bornes), crawl sur faux site local (robots, débit, 429,
  rel, sitemap, statique), export (échappement, formules, téléportation), corrections du modèle, version unique.

## 0.3.0 (2026-11-10)

- Profils de poids renommés `raisonnable` et `modere` (anciens noms acceptés), licence MIT, publication du dépôt.

## 0.2.0 (2026-11-03)

- Pondération par clics observés et composante sémantique, `diff` entre analyses, connecteurs Search Console et GA4.
