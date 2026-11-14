# pagerank-graph

[![Tests](https://github.com/J2K0000/pagerank-graph/actions/workflows/tests.yml/badge.svg)](https://github.com/J2K0000/pagerank-graph/actions/workflows/tests.yml)
[![Licence MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

Calcul du **PageRank interne pondéré** (modèle du surfeur raisonnable), croisement avec la
**valeur business** des pages et **visualisation en graphe** (sigma.js + ForceAtlas2).

Le problème résolu : sur la plupart des sites, le gabarit (mégamenu, footer) concentre
l'autorité interne sur les pages les moins utiles (mentions légales, panier, contact) et affame
les pages qui rapportent. L'outil confronte la matrice des priorités business à la matrice de la
puissance algorithmique réelle, puis montre le résultat sous une forme qu'un CTO ou un chef de
produit comprend en dix secondes.

## Ce que l'outil calcule

| Besoin | Réponse |
|--------|---------|
| Reasonable Surfer | Poids par position (content 1.0, breadcrumb 0.5, sidebar 0.25, header/nav 0.2, footer 0.05), fusion des multi-arêtes A→B (poids le plus visible conservé) |
| Nettoyage préalable | Chaînes 301 et canonical résolues, calcul sur les URL canoniques finales en 200, lien vers 4xx/5xx = équité perdue, nofollow interne = jus jeté (reste au dénominateur) |
| Culs-de-sac | Masse des pages sans issue et des fuites réinjectée : uniforme, vers le vecteur de téléportation ou vers l'accueil |
| Téléportation biaisée | `--restart` : entrées observées (pages d'atterrissage GSC, backlinks) au lieu du saut uniforme |
| Échelle | Table URL → entier, matrice CSR scipy, puissance itérée vectorisée (1 500 pages / 110 000 arêtes : 0,02 s ; conçu pour 10^5 à 10^6 pages en mémoire) |
| Normalisation log 0-100 | `(ln PR - ln PRmin) / (ln PRmax - ln PRmin) × 100` |
| CheiRank | PageRank du graphe inversé, quadrants puits / hubs / passoires / nœuds morts |
| Alignement business | Part de la valeur contre part du PageRank, par page (famine, gouffres) et par segment (écart en points) |
| Silos | Matrice des flux de PageRank entre segments, part de fuite par segment, communautés Louvain dans le visualiseur |
| Trous noirs, pages sur-liées | Culs-de-sac les mieux alimentés, pages utilitaires en tête de score |
| Cannibalisation | Ratio de PageRank entre pages d'un même groupe d'intention |
| Clics observés | `--transitions` : part observée des transitions mélangée à la part heuristique par position (`--data-blend 0.7`, jamais 1.0), plancher pour les liens jamais cliqués, couverture reportée |
| Composante sémantique | `--embeddings` : poids multiplié par max(0, cos(source, cible)) ** `--semantic-decay`, renormalisé par source ; déperdition thermique par segment |
| What-if | Sous-commande `simulate` (Python) et panneau de simulation dans le rapport HTML (recalcul JavaScript en moins d'une seconde) |
| Pelote de laine | Vue contractée par segment (méta-nœuds et flux), filtres par position de lien, échantillonnage au-delà de `--max-nodes` |

## Verdict et plan d'action

Chaque URL reçoit une **classe business** décidée sur ses segments de chemin, jamais sur des sous-chaînes
(`/blog/legal-tech` est un article, `/legal` une page légale) : un premier segment de rubrique (blog,
glossaire, guides, ebooks, webinars, cas clients...) classe `acquisition` quoi que contienne la suite ;
sinon un segment qui est entièrement un mot légal / support (legal, privacy-policy, terms, cart, account,
jobs...) ou qui finit par un suffixe légal (`-policy`, `-terms`, `-notice`...) classe `legal_support`
(`/privacy-first-analytics` ou `/job-board-software` n'en sont pas), un segment de confiance (about, team,
press, faq) classe `confiance`, un segment qui commence ou finit par un mot money (pricing, demo, contact,
product, platform, software, `-vs-`... ; `/p/` et `/c/` seulement en segment entier) classe `money`, un mot d'acquisition
classe `acquisition`, le reste est `neutre` ; la page d'accueil est `accueil`. Surcharge : `--labels
regles.tsv` (`nom<TAB>regex`, à l'ancienne) ou CSV `url,label` ; les pages de `--strategic` sont classées
`money`. Les classes sont listées dans `pages.csv` (colonne `classe`) : relire celles du site avant de
remettre un rapport.

**Score d'alignement business** (0-100) = 100 × (1 − ½ Σ |part du PageRank de la classe − part cible|),
calculé hors accueil (ses clics sont des recherches de marque, pas une valeur que le maillage doit
aligner). La cible est la répartition de la valeur business par classe quand elle existe (revenu,
conversions, clics), sinon une cible par défaut (money 38 %, acquisition 38 %, confiance 4 %, neutre 12 %,
légal 0 %). Le score est toujours accompagné de son démenti : le segment le plus sous-alimenté (part de
la valeur contre part du PageRank) et la corrélation de rang valeur ↔ score, avec un barème (sous 70
désaligné, 70 à 85 à corriger, au-dessus de 85 aligné par classe, vérifier les segments), la même
rubrique toutes langues, et la part de l'autorité reçue par **les pages qui portent 80 % de la valeur**,
la seule mesure qui dit si l'autorité va à ce qui rapporte. Avec des clics Search Console seuls, la
mesure sous-estime les pages de conversion (démo, tarifs, solutions), qui reçoivent peu de clics
organiques : le rapport le dit à côté de chaque chiffre de valeur ; préférer des conversions ou un revenu
par page (GA4).

Le **plan d'action** simule chaque chantier sur le graphe (même moteur que `simulate`) et le classe par
gain de PageRank sur les classes money et acquisition : reléguer les liens vers les pages légales dans un
sous-pied de page (global et page par page ; le gain affiché est celui de ce déplacement, jamais celui
d'une suppression des liens), regrouper les liens légaux derrière une page centre légal, sauver les pages
en famine avec trois liens contextuels depuis les meilleures pages de leur langue, raccrocher les pages à
valeur inatteignables, plafonner les menus, faire rebondir les culs-de-sac (pages, jamais des fichiers),
retirer le nofollow interne. Sorties : `actions.csv`, `brief_dev.md` (tickets prêts pour Jira ou Notion),
section « 0. Verdict » de `findings.md`, onglet « Plan d'action » du rapport. Les efforts indiqués sont
des ordres de grandeur par défaut, à confirmer par l'équipe. Désactiver avec `--no-actions`, borner avec
`--max-sims`.

Règle transverse des consignes : aucun lien n'est masqué ni obfusqué (les mentions légales restent
accessibles et l'obfuscation de liens relève des pratiques de manipulation) ; on dédoublonne, on
déplace dans un sous-pied de page distinct de la navigation, on ajoute des liens éditoriaux.

Dans le rapport : KPI d'alignement en tête, bouton **Scénario zéro déchet** (retrait des liens de
gabarit vers les pages légales, plafond de 50 liens de menu, deux liens éditoriaux par page
d'acquisition vers les pages money de sa langue), mode couleur **Delta de simulation** (vert = gagne,
rouge = perd) avec bascule Avant / Après, bouton **Brief dev** (markdown).

## Pilotage par URL : cibles à pousser et gouvernance

Chaque fiche de page commence par une **lecture en clair** : verdict (sous-alimentée, correcte, très
bien alimentée, utilitaire), cause (liens de gabarit ou éditoriaux, profondeur), enjeu business, puis
« Quoi faire » avec les trois émetteurs du même silo et le gain simulé immédiatement (score et rang),
un bouton « + Pousser cette page » et « Voir l'effet sur le graphe ».

Dans le rapport, l'onglet **Cibles à pousser** reçoit les URL que vous voulez positionner (saisie,
regex, ou bouton « + Pousser cette page » depuis une fiche), quelle que soit leur classe automatique :
un article qui ranke sur une requête business y a sa place. Pour chaque cible : constat (score, rang,
liens entrants, profondeur, valeur), prescription (émetteurs du **même silo et de la même langue**,
puis même langue et classe productive) sous forme de **prescription** : le nombre de liens est
calculé, pas demandé. Les émetteurs sont ajoutés un à un, le PageRank recalculé à chaque lien, et
l'on s'arrête à l'objectif (pour une cible choisie : les 10 % les plus fortes de son silo, au moins
le quartile supérieur du site ; pour une famine détectée : le quartile supérieur du silo) ou quand le
lien suivant rapporte moins d'un point ; deux liens minimum pour une cible choisie. Chaque lien dit
depuis quelle page (score, même silo ou non, lien de menu à doubler), où dans la page (corps du
texte, première moitié, paragraphe sur le sujet), avec quelle ancre (canonique puis variantes
déclarées, jamais générique) et le score atteint. « Simuler la prescription » applique le lot entier
avec un garde-fou qui liste les pages money, stratégiques ou cibles qui reculent de plus de 2 points ;
« Exporter les liens à poser » donne un CSV source, cible, position, ancre.
« Recalculer le rapport avec ces cibles » relance le calcul Python avec ces URL en `--strategic` :
elles passent en classe money, entrent dans la cible d'alignement et le plan d'action les traite en
« Renforcer une cible choisie ». En ligne de commande : `--strategic cibles.csv` (`url,poids`).

Chaque action porte une étiquette d'**effort** : 🧩 gabarit (un composant modifié touche toutes les
pages, coût dev faible, levier ×N) ou ✍️ éditorial (liens posés page par page). Le plan est classé par
priorité puis par gain / effort ; les cinq meilleurs rapports sont marqués ⚡ victoire rapide.

Le PageRank interne est à somme nulle : pousser une page en fait descendre d'autres. Pour éviter le
sculptage en boucle (une décision défaite plus tard pour pousser une autre URL) :

1. décider en **portefeuille** (`--strategic`, onglet Cibles), jamais une URL à la fois, et simuler le
   lot entier ; lire la section Baisses et le garde-fou avant de poser le moindre lien ;
2. préférer les leviers de **gabarit** (dédoublonner, sortir le légal, plafonner les menus), qui
   relèvent toutes les pages productives sans arbitrage entre elles, aux transferts éditoriaux ;
3. garder les transferts éditoriaux **dans le silo** de la cible (Topic-Sensitive) ;
4. consigner chaque lien posé (source, cible, ancre, raison, date) et vérifier à chaque nouvelle
   analyse avec `prgraph diff --before <analyse N> --after <analyse N+1> [--protected cibles.csv]` :
   le rapport `diff.md` liste les pages protégées qui reculent (score de 5 points ou plus, part d'équité
   d'un quart ou plus, ou rang de 10 % des pages ou plus : le score log 0-100 dépend du minimum et du
   maximum du site, la part d'équité non), les liens disparus vers elles et les
   pages protégées absentes de la nouvelle analyse. Codes de sortie, utilisables en intégration
   continue : 1 = régression détectée, 2 = comparaison incomplète ou impossible (dossier sans
   `pages.csv`, ou `edges.csv` absent : les liens disparus ne sont alors pas contrôlés), 0 = rien à
   signaler. `diff` lit `pages.csv` et `edges.csv` dans le **même dossier** : lancer `compute` avec
   `--out` sur le dossier du crawl (ou `prgraph crawl ... --run`). Il ne compare que des URL
   identiques et ne suit pas les redirections : après une refonte qui change les URL, ou face à une
   recette sur un autre hôte, les pages protégées apparaissent « absentes » et se contrôlent par le
   plan de redirection. Le serveur expose la même comparaison sur `POST /api/diff`.

Mode **Delta de simulation** : couleur = sens du delta, intensité et taille des nœuds = **delta absolu
de PageRank** (part du total), jamais le pourcentage relatif : une page qui gagne 0,0001 % reste grise.

## Installation

Python 3.11+. Le paquet s'installe avec ses dépendances (numpy, scipy, pandas, beautifulsoup4, lxml,
openpyxl, requests) et fournit la commande `prgraph` (équivalent : `python -m prgraph`).

```bash
# Depuis GitHub
pip install "git+https://github.com/J2K0000/pagerank-graph.git"
# Avec les connecteurs Search Console et GA4
pip install "pagerank-graph[google] @ git+https://github.com/J2K0000/pagerank-graph.git"

# Développement : copie de travail installée en mode éditable, les modifications sont prises en compte sans réinstaller
git clone https://github.com/J2K0000/pagerank-graph.git && cd pagerank-graph
pip install -e ".[test,google]"
python -m pytest -q
```

Vérification : `python -c "import prgraph; print(prgraph.__version__)"` et `pip show pagerank-graph` doivent
donner la même version (en mode éditable, relancer `pip install -e .` après une montée de version : les
métadonnées installées ne suivent pas `__init__.py` toutes seules). La version change à chaque
modification du calcul ou de l'interface (`CHANGELOG.md`) ; la citer avec les résultats pour qu'une analyse reste reproductible.
Les fichiers produits (`findings.md`, `brief_dev.md`, `diff.md`, `simulation.md`) commencent par
`Outil : prgraph <version> · <date>`.


## Licence

MIT, voir `LICENSE`. Utilisation, modification et redistribution libres, y compris dans un produit
commercial, à condition de conserver la mention de licence.

## Contribuer, signaler une faille

Le guide de contribution est dans `CONTRIBUTING.md` (installation pour le développement, tests, conventions,
données de test synthétiques seulement). Une faille de sécurité se signale en privé, jamais par une issue
publique : voir `SECURITY.md`. Les tests tournent automatiquement sur GitHub à chaque modification (Linux et
Windows, Python 3.11, 3.13 et 3.14).

## Application locale (champ URL)

```bash
python serve.py --port 8765        # ou : python -m prgraph serve --port 8765 --runs runs
```

Ouvrir http://localhost:8765/ : saisir l'adresse d'un site, les **pages à pousser** (URL, chemins ou
regex, une par ligne : elles deviennent les cibles stratégiques du rapport dès le premier calcul),
le nombre de pages maximum, le délai, le profil de poids et la politique des culs-de-sac, puis
« Analyser ». Le site est crawlé
(robots.txt respecté, jeton `pagerank-graph` et `*` ; un robots.txt en 5xx ou injoignable refuse le crawl ;
HTML servi sans rendu JavaScript ; sitemap lu pour compléter l'inventaire, ses URL non crawlées faute de
budget sont écrites avec le statut `uncrawled`), le PageRank est calculé et le rapport s'ouvre. Le délai
s'applique par requête quel que soit le nombre de requêtes parallèles (plancher 0,1 s) ; 429 et 503 sont
retentés après `Retry-After`. Le serveur écoute sur 127.0.0.1 seulement, exige un jeton anti-CSRF sur
chaque action et refuse les requêtes d'une autre origine. Chaque analyse est conservée dans `runs/<hôte>-<date>/`
(edges.csv, urls.csv, pages.csv, findings.md, report.html). Le même crawl existe en ligne de
commande : `python -m prgraph crawl --url https://monsite.fr --out audits/monsite --max-pages 500 --run`.

La section « Données business » du lanceur propose trois sources pour croiser la valeur et le PageRank :

- **CSV local** : chemin d'un fichier `url,revenue,conversions,clicks`, export Search Console « Pages »
  ou export GA4 « Pages et écrans » (lu sur cette machine, rien n'est envoyé) ;
- **Search Console** : propriété à saisir (`sc-domain:exemple.fr` ou `https://www.exemple.fr/`, vide =
  `sc-domain:` du site analysé) ou à choisir dans la liste renvoyée par « Lister mes propriétés »
  (`sites.list`), période en jours ;
- **GA4** : identifiant de propriété, dimension (page d'atterrissage avec ou sans requête), période.

Pendant la phase « calcul », le serveur interroge l'API, écrit `business.csv` dans le dossier du run
et passe `--business` à compute. Le journal indique le nombre d'URL récupérées, le nombre d'URL jointes
au graphe et la couverture (part des pages du graphe avec une valeur) ; si l'API échoue (identifiants,
quota), le calcul continue sans alignement business et le journal le dit.

## Sources business : Search Console et GA4

Identifiants Google lus depuis la variable d'environnement `GOOGLE_APPLICATION_CREDENTIALS` (ou
`--credentials FICHIER`), **jamais stockés dans le projet** : fichier JSON de compte de service (à ajouter
en lecteur sur la propriété Search Console et sur la propriété GA4), identifiants OAuth « application
installée » (un navigateur s'ouvre la première fois, le jeton est mis en cache dans `%APPDATA%\pagerank-graph\`)
ou fichier `authorized_user` (format gcloud). Bibliothèques optionnelles : `pip install
google-api-python-client google-analytics-data` (import paresseux, message explicite si absentes).

```bash
# Search Console : dimension page, type web, pagination par 25 000 lignes → url, clicks, impressions, ctr, position
python -m prgraph gsc-sites                                   # propriétés accessibles
python -m prgraph fetch-gsc --site sc-domain:exemple.fr --days 90 --out audits/exemple/business.csv

# GA4 Data API : pages d'atterrissage → url (chemin préfixé par --base-url), sessions, conversions (keyEvents), revenue
python -m prgraph fetch-ga4 --property 123456 --base-url https://exemple.fr --days 90 --out audits/exemple/ga4.csv

# Fusion par URL des deux sources (clics + impressions + sessions + conversions + revenu) dans un seul CSV
python -m prgraph fetch-ga4 --property 123456 --base-url https://exemple.fr --days 90 \
    --merge audits/exemple/business.csv --out audits/exemple/business.csv
python -m prgraph compute --edges edges.csv --business audits/exemple/business.csv --out audits/exemple
```

`--days N` couvre N jours terminés trois jours avant aujourd'hui (données des derniers jours incomplètes) ;
`--start` / `--end` fixent la fenêtre. La fusion joint par URL normalisée, somme les métriques, moyenne
CTR et position par les impressions et complète à zéro les colonnes absentes d'une source.

Lecture quand seules des données Search Console existent : la colonne de valeur retenue est `clicks`
(**clics = valeur réalisée**, ce que la page capte) et les impressions sont lues comme la **demande**
(ce que Google montre). La famine est calculée sur les clics ; `findings.md` § 4 bis et l'onglet Famine
ajoutent la liste des pages à fortes impressions et PageRank faible, hors famine, où un gain de maillage
est le plus directement mesurable. La colonne retenue et les colonnes disponibles sont rappelées en tête
du § 4 et de l'onglet.

## Démarrage rapide

```bash
# Site de démonstration synthétique (mégamenu, footer géant, pagination, PDF, 301, 404, nofollow)
python -m prgraph demo --out examples/demo --run
# puis ouvrir examples/demo/out/report.html
# Même site, avec les clics observés et les vecteurs sémantiques générés (sortie dans examples/demo/out-data/)
python -m prgraph demo --out examples/demo --run --with-data
```

```bash
# Export Screaming Frog : Bulk Export > Links > All Inlinks, et Internal > HTML
python -m prgraph compute --edges all_inlinks.csv --urls internal_html.csv \
    --business gsc_pages.csv --segments segments.tsv --strategic strategiques.csv \
    --site-name "monsite.fr" --out audits/monsite
```

```bash
# CSV d'arêtes produit par n'importe quel crawler : source,target,position,nofollow,anchor
python -m prgraph compute --edges edges.csv --out audits/monsite
```

```bash
# Dossier HTML statique (dist Astro, export Next) : positions détectées dans le DOM
python -m prgraph crawl-static --dir dist --base-url https://monsite.fr --out audits/monsite
python -m prgraph compute --edges audits/monsite/edges.csv --urls audits/monsite/urls.csv --out audits/monsite/out
```

```bash
# What-if en ligne de commande : retirer le footer, plafonner le mégamenu à 30 liens, ajouter des liens
python -m prgraph simulate --edges edges.csv --urls urls.csv --business business.csv \
    --remove-position footer --cap-position header:30 --add-edges ajouts.csv --out audits/monsite/sim
```

## Fichiers d'entrée

| Option | Format | Notes |
|--------|--------|-------|
| `--edges` (obligatoire) | `source,target,position,nofollow,anchor` (positions) · export Screaming Frog All Inlinks (`Source, Destination, Follow, Link Position, Status Code`) · `source,target[,weight]` | Détection automatique par en-tête. Les statuts de cible de l'export Screaming Frog sont exploités |
| `--urls` | `url,status[,redirect_target][,canonical][,segment][,title]` ou export SF `internal_html` | Sans ce fichier tout nœud est supposé en 200. Les statuts littéraux `robots` et `uncrawled` (écrits par le crawler) désignent une URL connue mais jamais récupérée : ni nœud, ni cible valide. Fournir l'inventaire complet (sitemap, logs, GSC) fait apparaître les vraies orphelines |
| `--business` | `url` + `revenue` / `conversions` / `clicks` / `sessions` / `impressions` / `crawl_hits` | Export GSC « Pages » et export GA4 « Pages et écrans » acceptés (FR ou EN, lignes de commentaire `#` ignorées). La convention numérique (virgule décimale « 12,5 » ou séparateur de milliers « 1,234 ») est décidée par colonne. Priorité : revenue > conversions > clicks > sessions ; `--value-col` force. Sortie de `fetch-gsc` / `fetch-ga4` directement utilisable |
| `--business-period` | texte libre, ex. `9 août au 8 novembre 2026` | Période des données business, affichée avec la valeur dans le rapport et le classeur ; lue automatiquement dans la ligne `# période : ...` que `fetch-gsc` et `fetch-ga4` écrivent en tête du CSV |
| `--business-base-url` | `https://exemple.fr` | Préfixe les chemins relatifs du CSV business (`/produits/a/`) pour les joindre au graphe ; les lignes `(not set)` sont ignorées |
| `--segments` | `nom<TAB>regex` (premier match gagnant) ou CSV `url,segment` | Défaut : premier segment de chemin |
| `--strategic` | `url[,poids]` | Pages à surveiller même sans donnée business |
| `--restart` | `url,poids` | Vecteur de téléportation observé |
| `--cannibalisation` | `url,group` | Pages en concurrence sur une même intention |
| `--transitions` | `source,target,transitions` (ou `count`, `clicks`, `sessions`) | Clics observés page à page : logs serveur (referrer same-origin), export BigQuery GA4 (`page_referrer` → `page_location`), API Matomo Transitions. Les URL sont normalisées comme les arêtes ; les couples absents du graphe sont comptés et ignorés |
| `--embeddings` | `url` puis colonnes numériques, ou `url,vector` avec un vecteur JSON par ligne | Un vecteur par page, même dimension partout. Les pages sans vecteur ne sont pas pénalisées |

Pondération au-delà de la position (`prgraph/weights.py`), appliquée après le nettoyage et avant le PageRank :

- **Clics observés**, `--transitions FILE.csv --data-blend 0.7`. Pour chaque page source qui possède au
  moins une transition, le poids d'une arête devient `blend × part observée + (1 − blend) × part
  heuristique`, la masse sortante de la page étant conservée (sa part de fuite ne bouge pas). Une arête
  jamais cliquée sur une page qui a des données garde `(1 − blend)` × son poids heuristique : c'est le
  plancher qui permet de conclure « lien mal placé » plutôt que « lien inexistant ». Les pages sans
  donnée gardent l'heuristique seule. **`--data-blend 1.0` est refusé** (piège de circularité : les
  clics décrivent le design en place, pas une cible : un lien jamais cliqué tomberait à zéro et ne
  pourrait plus être diagnostiqué comme mal placé). La couverture
  (part des arêtes et de la masse heuristique couvertes, pages sources avec donnée, couples non
  retrouvés) est reportée dans `findings.md` § 2 bis et dans l'onglet Modèle.
- **Composante sémantique**, `--embeddings FILE.csv --semantic-decay 0.5`. Le poids de chaque arête
  est multiplié par `max(0, cos(source, cible)) ** decay`, puis les poids sont renormalisés par page
  source : la masse émise ne change pas, un lien hors sujet en transmet moins et ses voisins
  thématiques récupèrent la différence. `decay 0` désactive la pénalité, une valeur plus forte la
  durcit. Une arête dont une extrémité n'a pas de vecteur garde un facteur 1 ; si toutes les arêtes
  d'une page tombent à zéro, ses poids d'origine sont conservés. La **déperdition thermique** mesure,
  par segment, la masse qui aurait circulé sans la pénalité contre la masse effective (même PageRank
  stationnaire) : un écart positif = le segment recevait du jus par des liens hors sujet. Elle est
  reportée dans `findings.md`, `graph.json` (`thermal`, champs `thermal_*` des segments) et l'onglet
  Modèle. Les deux options se cumulent (clics d'abord, sémantique ensuite ; la référence de la
  déperdition est alors le graphe déjà mélangé).

Options du modèle : `--weights-profile raisonnable|modere|uniform`, `--weights "footer=0.02,nav=0.3"`,
`--alpha 0.85`, `--dangling uniform|teleport|home`, `--home URL`, `--strip-query`,
`--utility-regex`, `--money-share 0.8`, `--starving-percentile 40`, `--overfed-percentile 80`,
`--max-nodes 3000`, `--no-excel`, `--cdn`, `--brand-name`, `--brand-logo`, `--brand-color`, `--uncrawled leak|nodes` (cibles découvertes mais absentes de la table d'URL :
fuite par défaut), `--boilerplate-share 0.6`, `--boilerplate-min-links 200`, `--no-boilerplate-inference`.

**Inférence du gabarit.** Un mégamenu en `<div>` ou un bandeau de logos dans `<main>` sont déclarés
`content` par la détection DOM. Pour compenser, les liens `content` ou `unknown` vers une cible citée par
au moins 60 % des pages émettrices (plancher 20 pages, plafond 200 liens) sont requalifiés en gabarit, avec
un poids graduel `max(poids gabarit, poids × (1 − part des pages émettrices))`. Les pages stratégiques et
les pages money ne sont jamais requalifiées (un lien « demander une démo » dans chaque article est un choix
éditorial). Les cibles touchées, le seuil et les cibles épargnées sont listés dans `findings.md` § 1 et
l'onglet Modèle : relire cette liste avant de conclure, et désactiver avec `--no-boilerplate-inference`
pour comparer.

**Fuites.** Un lien vers une 4xx/5xx, vers une redirection qui sort du site (autre hôte), vers une URL
connue mais jamais récupérée (`robots`, `uncrawled`) ou vers une cible découverte mais absente de la table
d'URL perd son équité ; le poids reste au dénominateur de la page source. Les compteurs `lost_404`,
`lost_external`, `lost_uncrawled` sont dans `findings.md` § 1.

Profils : `raisonnable` (défaut : content 1.0, breadcrumb 0.5, sidebar 0.25, header / nav 0.2,
footer 0.05), `modere` (header / nav 0.35, footer 0.10 : moins sévère avec les liens de gabarit) et
`uniform`, le PageRank naïf de 1998. Les anciens noms `brief` et `library` restent acceptés.

## Sorties (`--out dossier/`)

| Fichier | Contenu |
|---------|---------|
| `report.html` | Rapport autonome, un seul fichier à envoyer : mode **Analyse** (explorateur) et mode **Présentation** (deck client imprimable en PDF). Données et bibliothèques embarquées : il s'ouvre hors ligne, sans appel à un tiers (`--cdn` pour charger les bibliothèques depuis le CDN) |
| `report.xlsx` | Classeur mis en forme pour Excel : Synthèse, Plan d'action, Pages, Famine, Gouffres, Segments, Flux entre silos, Modèle, Lisez-moi. **Dans un Excel français, ouvrir ce classeur plutôt que les CSV** (séparés par des virgules, ils s'ouvrent en une seule colonne). `--no-excel` pour s'en passer |
| `findings.md` | Rapport en français : résumé, où part l'équité par zone, alignement par segment, famine, gouffres, trous noirs, étanchéité des silos, quadrants, cannibalisation, limites |
| `pages.csv` | Une ligne par URL canonique : score, PageRank brut, CheiRank, quadrant, liens, profondeur de clic réelle, valeur, drapeaux |
| `segments.csv`, `flows_segments.csv` | Alignement et flux entre segments |
| `graph.json` | Données du visualiseur (réutilisables ailleurs) |
| `simulation.md` | Avec `simulate` : avant / après, plus fortes hausses et baisses (même vecteur de téléportation que le calcul de référence) |
| `CHANGELOG.md` (dépôt) | Ce qui change à chaque version |

## Le rapport : mode Analyse et mode Présentation

`report.html` a deux modes, bascule dans l'en-tête ; `report.html#presentation` ouvre directement le second.

**Mode Présentation**, pour le client : un deck qu'on feuillette (flèches du clavier, boutons à l'écran),
une idée par écran, sans jargon, imprimable en PDF avec « Exporter en PDF » (une diapositive par page,
16:9). Couverture, sommaire, verdict en trois tuiles, ce qui rapporte contre ce qui reçoit, carte des
silos, coût du gabarit, pages rentables mal reliées, plan d'action en cartes « on saura que ça marche »,
avant / après des chantiers de gabarit, une seule diapositive Méthode. Une diapositive dont les données
manquent (pas de valeur business, pas de scénario) est omise. Marque facultative, rien par défaut :

```bash
python -m prgraph compute ... --brand-name "Mon agence" --brand-logo logo.png --brand-color "#1640ef"
```

**Mode Analyse**, pour le consultant : le verdict d'abord (score, barème, pire écart de segment,
corrélation de rang, part du jus dans le gabarit), puis le plan d'action ; pendant une simulation, le
score affiche « avant → après ». Tableaux triables, filtrables, paginés, exportables en CSV ; thème clair
ou sombre ; utilisable sur mobile (verdict d'abord, graphe sur demande) ; feuille d'impression.

- **Vue silos** (par défaut) : un méta-nœud par famille de rubriques (`blog`, `fr/blog` et `uk/blog`
  forment la famille « blog »), flèches = part du flux, étiquettes en pourcentage ; couleurs fixes de
  la palette validée pour les daltoniens, « autres » au-delà de sept familles. Double-clic pour entrer.
- **Vue pages** : un nœud par URL, taille = score log, couleur au choix (famille, alignement
  business, quadrant PR × CheiRank, profondeur de clic, communautés Louvain). Les liens de gabarit
  sont masqués par défaut pour voir le maillage éditorial ; ils comptent toujours dans le calcul.
- **Panneaux** : fiche de page (d'où vient son jus, où il part), famine, gouffres, trous noirs,
  matrice des silos, nuage PR × CheiRank, cannibalisation, modèle et nettoyage (avec, le cas échéant,
  le mélange clics / position, la couverture, le decay sémantique et la déperdition par segment).
- **What-if** : retirer des positions de liens (éventuellement vers certaines cibles), plafonner
  header/nav à N liens par page, ajouter k liens éditoriaux de pages sources vers des cibles
  (regex, par défaut les pages en famine). Le PageRank est recalculé dans le navigateur et les
  deltas apparaissent dans les tableaux et sur le graphe.
- **Exporter CSV** : le tableau courant, avec les colonnes de simulation si elle est active.

Au-delà de `--max-nodes`, le visualiseur reçoit un échantillon (accueil, stratégiques, anomalies,
meilleurs scores, échantillon stratifié par segment). Les CSV, `findings.md` et la vue silos
restent calculés sur le graphe complet ; la simulation navigateur est alors marquée approximative.

## Lecture des résultats

- Les poids par position sont une **heuristique de praticien** [Consensus] inspirée du brevet du
  surfeur raisonnable US 7,716,225 [Confirmé]. Google ne publie pas les siens. Lire les scores comme
  un **ordre relatif de pages à renforcer**, jamais comme une prédiction de positionnement.
- Les orphelines détectées sont un **plancher** : un crawl ne trouve pas ses propres orphelines.
- La pondération est relative à la page source : ajouter des liens contextuels sur une page dilue
  ses liens de navigation ; c'est un levier en soi.
- Le déficit prime sur le classement : les pages à pousser sont celles dont l'équité reçue est
  inférieure à leur importance business, pas celles qui sont déjà en tête.
- Le score log 0-100 est une position relative **dans ce rapport** : il dépend du minimum et du maximum
  du site et n'est pas comparable d'une analyse à l'autre. Pour suivre une page dans le temps, lire sa
  part d'équité (`pr_share_pct`) et son rang ; `diff` fait de même.
- Les quadrants PageRank × CheiRank décrivent une position dans le graphe, jamais une décision : une
  page « peu reliée et peu reliante » s'instruit avec ses clics et ses impressions avant toute action.

## Hors périmètre, points d'extension

Rendu JavaScript (le crawl lit le HTML servi), décroissance
temporelle, calcul distribué. Côté sémantique, l'outil ne calcule pas les vecteurs : les produire en
amont (TF-IDF, modèle d'embeddings, au choix)
et les fournir en CSV ; la pénalité `max(0, cos) ** decay` est une fonction documentée, pas une
reconstitution du Topic-Sensitive PageRank de Google. Côté clics, pas d'extraction depuis les logs
ou BigQuery : fournir le triplet `source,target,transitions` (referrer same-origin des logs serveur,
export BigQuery GA4 ou API Matomo Transitions).
Points d'entrée : `prgraph/loaders.py` (nouveaux formats), `prgraph/connectors.py` (autres sources
business : Matomo, BigQuery), `prgraph/weights.py` (autres pondérations d'arêtes), `prgraph/pagerank.py`
(variantes de téléportation), `prgraph/analysis.py` (nouveaux diagnostics), `prgraph/viewer/template.html` (vues).

## Structure

```
prgraph/
  cli.py          compute | simulate | demo | crawl | crawl-static | serve | fetch-gsc | fetch-ga4 | gsc-sites
  crawl_live.py   crawl live (robots, 301, canonical, sitemap, positions DOM)
  server.py       serveur local : lanceur, crawl + connecteurs business + calcul en arrière-plan, rapports
  connectors.py   Search Console (Search Analytics, pagination 25 000), GA4 (Data API), identifiants, fusion par URL
  loaders.py      formats d'entrée (dont exports GSC / GA4, chemins relatifs préfixés), profils de poids, segments
  clean.py        normalisation, résolution 301 / canonical, fuites, fusion des multi-arêtes
  weights.py      clics observés (mélange avec plancher), composante sémantique (cos ** decay, renormalisation)
  graph.py        matrice CSR, sommes sortantes, flux par arête
  pagerank.py     puissance itérée, culs-de-sac, CheiRank, score log
  analysis.py     profondeur réelle, quadrants, alignement, silos, trous noirs, cannibalisation
  simulate.py     règles de modification du graphe et comparaison
  actions.py      classes business, plan d'action simulé (effort, quick wins), brief développeur
  diff.py         comparaison de deux analyses : régressions sur les cibles protégées, liens disparus
  export.py       CSV, findings.md, graph.json, report.html
  demo.py         site synthétique, transitions.csv et embeddings.csv de démonstration
  crawl_static.py dossier HTML → arêtes avec position DOM
  viewer/template.html  visualiseur (sigma.js 3, graphology, ForceAtlas2, Louvain), livré avec le paquet
  viewer/present.html   mode Présentation (deck client), inséré dans report.html
  viewer/vendor/        bibliothèques embarquées (graphology, graphology-library, sigma.js) et leurs licences MIT
  workbook.py     classeur Excel mis en forme (report.xlsx)
  viewer/launcher.html  page de lancement de l'application locale
pyproject.toml  paquet installable, commande `prgraph`
tests/                tests pytest (modèle comparé à networkx, nettoyage, analyses, pondérations, connecteurs simulés, bout en bout)
docs/design.md        note de conception
LICENSE               licence MIT
```
