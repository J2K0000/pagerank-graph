# pagerank-graph : note de conception

## 1. Problème résolu

Confronter la matrice des priorités business (marge, conversions, clics) à la matrice de la
puissance algorithmique réelle (PageRank interne pondéré) pour montrer où le jus coule
vraiment, et le faire voir sous forme de graphe à des équipes produit et tech.

## 2. Ce que l'outil apporte

Un PageRank pondéré par position se calcule en quelques lignes ; ce qui manque d'ordinaire, c'est
l'échelle, le nettoyage préalable, la lecture business et la visualisation. L'outil lit un CSV
d'arêtes `source,target,position,nofollow,anchor` (ou l'export « All Inlinks » de Screaming Frog)
et ajoute :

| Besoin | Réponse |
|-----------------|---------|
| Échelle (10^5 à 10^6 pages) | Table de hachage URL → entier, matrice CSR scipy, puissance itérée vectorisée |
| Reasonable Surfer | Poids par position, fusion des multi-arêtes (poids le plus visible conservé) |
| Nettoyage préalable | Résolution des chaînes 301 / canonical, 404 = équité perdue, nofollow = jus jeté |
| Culs-de-sac | Masse des pages sans issue redistribuée (uniforme, vecteur de téléportation ou accueil) |
| Téléportation biaisée | `--restart` : vecteur d'entrée observé (GSC, backlinks) |
| Normalisation log 0-100 | `score = (ln PR - ln PRmin) / (ln PRmax - ln PRmin) * 100` |
| CheiRank | Même calcul sur le graphe inversé, quadrants puits / hubs / passoires / nœuds morts |
| Alignement business | Croisement part de valeur vs part de PageRank, par page et par segment |
| Étanchéité des silos | Matrice des flux de PageRank entre segments, part de fuite par segment |
| Trous noirs, pages sur-liées | Pages sans issue à forte entrée, pages utilitaires en tête de score |
| What-if | Sous-commande `simulate` (Python) et panneau de simulation dans le visualiseur (JS) |
| Pelote de laine | Contraction en méta-nœuds par segment, export plafonné des nœuds détaillés |
| Communication | `report.html` autonome (sigma.js + ForceAtlas2), partageable sans serveur |

## 3. Architecture

```
prgraph/
  cli.py          compute | simulate | demo | crawl | crawl-static | serve | fetch-gsc | fetch-ga4 | gsc-sites
  connectors.py   sources business branchées : Search Console (Search Analytics), GA4 (Data API), identifiants, fusion
  loaders.py      lecture des CSV (format à positions, Screaming Frog, GSC, GA4, chemins relatifs), règles de segments
  clean.py        normalisation d'URL, résolution 301 / canonical, filtrage des statuts, fusion des arêtes
  weights.py      pondération au-delà de la position : clics observés (mélange), composante sémantique (cos ** decay)
  graph.py        table URL → id, matrice CSR pondérée, sommes sortantes, flux par arête
  pagerank.py     puissance itérée, culs-de-sac, téléportation, CheiRank, score log
  analysis.py     segments, profondeur de clic réelle, quadrants, alignement business, fuites, trous noirs
  simulate.py     règles de modification du graphe et comparaison avant / après
  export.py       pages.csv, segments.csv, flows.csv, findings.md, graph.json, report.html
  demo.py         site synthétique (mégamenu, footer géant, pagination, PDF, 301, 404, cannibalisation)
  crawl_static.py dossier HTML statique (dist Astro) → arêtes avec position détectée dans le DOM
  viewer/template.html  visualiseur, données injectées dans la page (livré avec le paquet)
tests/                pytest
```

Pipeline : `loaders → clean → graph → [weights : clics, sémantique] → pagerank (PR, CheiRank) → analysis → export`.

## 4. Modèle mathématique

Pondération par défaut (profil `raisonnable`) : content 1.0, breadcrumb 0.5, sidebar 0.25,
header / nav 0.2, footer 0.05, unknown 0.6. Profil `modere` : header / nav 0.35, footer 0.10.
Surcharge : `--weights "footer=0.02,nav=0.3"`.

Pour une page source v de somme sortante S(v) (y compris les poids des liens nofollow et
des liens vers 404, qui comptent au dénominateur mais ne transmettent rien) :

```
PR(u) = (1 - d) * t(u) + d * [ sum_v w(v,u) / S(v) * PR(v) + m * r(u) ]
```

t = vecteur de téléportation (uniforme ou `--restart`), m = masse des culs-de-sac et des
fuites à l'itération courante, r = vecteur de redistribution (`--dangling uniform|teleport|home`).
Arrêt quand la norme L1 de la différence est < 1e-9 ou après 200 itérations. Somme = 1 à
chaque itération.

CheiRank : même procédure sur la transposée (les arêtes inversées, mêmes poids).

Fuites (poids au dénominateur, rien transmis) : lien vers 4xx/5xx, lien nofollow / sponsored / ugc, lien
dont la cible finale est sur un autre hôte, lien vers une URL connue mais non récupérée (statut `robots`,
`uncrawled`) ou découverte mais absente de la table d'URL (`--uncrawled leak`, défaut).

Inférence du gabarit (`clean.py`) : une cible liée en `content` / `unknown` depuis au moins
`boilerplate_share` des pages émettrices (plancher 20, plafond `boilerplate_min_links`) voit ces liens
requalifiés en `boilerplate` avec le poids `max(w_boilerplate, w × (1 − part))` ; jamais pour une page
stratégique ou money (`protect`). Liste des cibles dans `stats["boilerplate_target_list"]`.

Le vecteur de téléportation (`--restart`) est conservé par `simulate`, le plan d'action et le what-if
du visualiseur (`meta.teleport`).

### 4bis. Pondération au-delà de la position (`weights.py`)

Deux ajustements optionnels des poids w(v,u), appliqués dans cet ordre avant le PageRank.

**Clics observés** (`--transitions`, `--data-blend b`, défaut 0,7). Pour toute page source v qui a au
moins une transition observée :

```
w'(v,u) = S_e(v) × [ b × T(v,u) / T(v) + (1 − b) × w(v,u) / S_e(v) ]
```

T(v,u) = transitions observées v→u, T(v) = transitions sortantes de v, S_e(v) = somme heuristique des
poids des arêtes de v (fuites exclues). La masse émise par v est conservée, sa part de fuite aussi. Une
arête jamais cliquée sur une page qui a des données garde (1 − b) × w : le plancher évite le piège de
circularité (un lien invisible disparaîtrait du modèle et ne pourrait plus être diagnostiqué), d'où le
refus de b = 1. Les pages sans donnée gardent l'heuristique. Couverture reportée : part des arêtes et
de la masse heuristique couvertes, pages sources avec donnée, couples non retrouvés.

**Composante sémantique** (`--embeddings`, `--semantic-decay γ`, défaut 0,5). Avec e(v) le vecteur
normalisé de la page v :

```
f(v,u) = max(0, e(v) · e(u)) ** γ        w''(v,u) = w'(v,u) × f(v,u) × S'(v) / Σ_u w'(v,u) f(v,u)
```

La renormalisation par source conserve la masse émise : un lien hors sujet transmet moins, ses voisins
thématiques récupèrent la différence. Facteur 1 pour une arête dont une extrémité n'a pas de vecteur ;
poids d'origine conservés si toutes les arêtes d'une page tombent à zéro ; γ = 0 désactive la pénalité.
Une arête de poids nul reste dans le graphe mais ne transmet rien (le dénominateur est protégé).

**Déperdition thermique** (`analysis.py`, `w_ref` = poids avant pénalité). Au PageRank stationnaire
effectif, flux de référence `α PR(v) w'(v,u) / S'(v)` contre flux effectif `α PR(v) w''(v,u) / S''(v)`,
agrégés par segment cible : `thermal_in_ref`, `thermal_in_eff`, `thermal_loss` (positif = le segment
recevait du jus par des liens hors sujet), plus la masse totale réacheminée et le flux entre segments
différents avant / après. Reportée dans `findings.md` § 2 bis, `graph.json` (`thermal`) et l'onglet
Modèle du visualiseur.

## 5. Entrées

- `--edges` : CSV `source,target,position,nofollow,anchor` (positions), export Screaming
  Frog « All Inlinks » (colonnes `Source, Destination, Follow, Link Position, Type`), ou
  `source,target[,weight]`. Détection automatique par en-tête.
- `--urls` : CSV `url,status[,redirect_target][,canonical][,segment][,title]` ou export
  Screaming Frog `internal_html` (`Address, Status Code, Redirect URL, Canonical Link Element 1`).
  Sans ce fichier, tout nœud est supposé en 200. Statuts littéraux `robots` et `uncrawled` = URL non
  récupérée (statut 0 : ni nœud ni cible valide).
- Classes business (`analysis.class_of`) : décidées par segments de chemin (rubrique de contenu en premier
  segment, puis segment légal entier ou à suffixe légal, segment de confiance entier, mot money ou
  d'acquisition en début ou fin de segment ; racine d'une langue = accueil), six classes
  (`money`, `acquisition`, `confiance`, `legal_support`, `neutre`, `accueil`) ; `--labels` garde des regex.
- Données business : convention numérique décidée par colonne (`loaders.comma_is_thousands`).
- `--business` : CSV `url` + colonnes de valeur (`revenue, conversions, clicks, sessions,
  impressions, crawl_hits`), export GSC « Pages » ou GA4 « Pages et écrans » accepté (`--business-base-url`
  préfixe les chemins relatifs). `--value-col` force la colonne.

### 5bis. Sources business branchées (`connectors.py`)

- **Search Console** : `searchanalytics.query` avec `dimensions=[page]`, `type=web`, `rowLimit=25000`,
  `startRow` incrémenté tant qu'une page pleine revient ; sortie `url, clicks, impressions, ctr, position`.
  `sites.list` alimente la liste des propriétés du lanceur ; sans propriété saisie, `sc-domain:` du site.
- **GA4** : `runReport` sur `landingPagePlusQueryString` (ou `landingPage`) avec `sessions, keyEvents,
  totalRevenue`, `limit` 100 000 et `offset` ; les chemins sont préfixés par l'origine du site (`(not set)`
  ignoré) ; sortie `url, sessions, conversions, revenue`.
- **Identifiants** : `GOOGLE_APPLICATION_CREDENTIALS` ou `--credentials` ; compte de service, OAuth
  « installed » (jeton en cache dans le dossier utilisateur) ou `authorized_user`. Rien dans le projet.
- **Fusion** (`--merge`, `merge_business`) : clé = URL normalisée, métriques additives sommées, CTR et
  position moyennés par les impressions, colonnes absentes complétées à zéro.
- **Période** : `--days N` = N jours terminés trois jours avant aujourd'hui (données récentes incomplètes).
- **Serveur** : `prepare_business` appelle le connecteur pendant la phase « calcul », écrit `business.csv`
  dans le dossier du run ; `business_coverage` relit `pages.csv` pour reporter URL jointes et couverture.
  Un échec du connecteur n'interrompt pas l'analyse (journal explicite, calcul sans business).
- **Lecture** : quand `impressions` est disponible sans être la colonne de valeur, `analysis.py` calcule
  une liste secondaire « demande non servie » (80 % cumulés des impressions, score sous le seuil de famine,
  hors famine) exposée dans `findings.md` § 4 bis, `graph.json` (`top.demand_starving`, champ `demand`
  des nœuds, `meta.demand_col`, `meta.business_cols`) et l'onglet Famine.
- `--segments` : règles `nom<TAB>regex` (premier match) ou CSV `url,segment`. Défaut :
  premier segment de chemin.
- `--strategic`, `--restart`, `--home`, `--utility-regex`, `--cannibalisation` (CSV `url,group`).
- `--transitions` : CSV `source,target,transitions|count|clicks|sessions` (clics observés page à page).
- `--embeddings` : CSV `url` + colonnes numériques, ou `url,vector` (vecteur JSON par ligne).

## 6. Sorties (`--out dossier/`)

`pages.csv`, `segments.csv`, `flows_segments.csv`, `findings.md` (français, version de
l'outil et date en tête), `graph.json`, `report.html`. Le visualiseur embarque `graph.json` : un seul
fichier à envoyer.

## 7. Visualiseur

- Graphe sigma.js, layout ForceAtlas2 (worker), taille = score log, couleur au choix :
  segment, alignement business (famine, gouffre, aligné, neutre), quadrant PR × CheiRank,
  profondeur de clic.
- Filtres : positions de liens, segments, score minimum, recherche d'URL.
- Vue contractée : un méta-nœud par segment, arêtes = flux de PageRank, étiquette = part de
  fuite ; retour à la vue détaillée par double-clic.
- Panneaux : fiche de page (liens entrants / sortants avec position), tableaux famine,
  gouffres, trous noirs, matrice des fuites, nuage PR × CheiRank.
- What-if côté client : retirer une position de lien, plafonner les liens de navigation,
  ajouter des liens par regex ; recalcul en JS (CSR, typed arrays) et deltas affichés.
- Au-delà de `--max-nodes` (3000 par défaut) le détail est échantillonné (accueil,
  stratégiques, anomalies, top score, échantillon par segment) ; la vue contractée reste
  exacte et le what-if client est marqué « approximatif ».

## 8. Hors périmètre (volontairement)

Rendu JavaScript, calcul des vecteurs sémantiques
(fournis en CSV, produits en amont), extraction des transitions depuis les logs ou BigQuery (fournies
en CSV), décroissance temporelle, calcul distribué Spark. Les points d'extension sont indiqués dans
le README.
