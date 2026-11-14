# Contribuer à pagerank-graph

Merci de vouloir améliorer l'outil. Ce guide dit comment l'installer pour le développement, lancer les tests et
proposer une modification.

## Installer pour le développement

Python 3.11 ou plus récent.

```bash
git clone https://github.com/J2K0000/pagerank-graph.git
cd pagerank-graph
pip install -e ".[test,google]"
python -m pytest -q
```

L'installation en mode éditable (`-e`) prend en compte vos modifications sans réinstaller. Après une montée de
version dans `prgraph/__init__.py`, relancez `pip install -e .` : les métadonnées installées ne suivent pas toutes
seules (un test le vérifie).

## Avant de proposer une modification

1. **Un test pour chaque correction ou fonction.** La suite (`python -m pytest -q`) doit passer. Les tests tournent
   automatiquement sur GitHub, sous Linux et Windows, pour Python 3.11, 3.13 et 3.14.
2. **Données de test synthétiques seulement.** Jamais de crawl, d'export Search Console ou de nom d'un site réel
   dans le dépôt, ni dans les tests, ni dans les exemples, ni dans les commentaires. Le générateur `prgraph demo`
   produit un site fictif complet ; les tests construisent leurs petits graphes eux-mêmes (`https://s.fr/...`).
3. **Version et journal.** Toute modification du calcul ou de ce qu'affichent les sorties monte la version
   (`prgraph/__init__.py`, numérotation SemVer) et ajoute une entrée à `CHANGELOG.md`. Une analyse cite la version
   qui l'a produite : sans cela, elle n'est pas reproductible.
4. **Textes en français**, phrases courtes, sans tiret cadratin dans les textes produits (les tests le vérifient).
5. **Sécurité.** Toute donnée venant d'un site crawlé (titre, ancre, URL) est traitée comme hostile : échappée
   dans le rapport HTML, neutralisée dans les CSV et le classeur. Le serveur local n'écoute que sur 127.0.0.1 et
   exige un jeton sur chaque action. Une faille se signale en privé : voir `SECURITY.md`.

## Proposer la modification

Ouvrez une pull request sur `main` avec : ce qui change, pourquoi, et comment vous l'avez vérifié (tests, capture
du rapport si l'affichage change). Pour une modification importante (nouveau calcul, nouveau format d'entrée),
ouvrez d'abord une issue pour en discuter.

Points d'entrée du code : `prgraph/loaders.py` (formats d'entrée), `prgraph/clean.py` (nettoyage du graphe),
`prgraph/weights.py` (pondérations), `prgraph/pagerank.py` (calcul), `prgraph/analysis.py` (diagnostics),
`prgraph/actions.py` (plan d'action), `prgraph/export.py` (sorties), `prgraph/viewer/` (rapport HTML),
`prgraph/workbook.py` (classeur Excel). La note de conception est dans `docs/design.md`.

## Licence

En contribuant, vous acceptez que votre contribution soit publiée sous la licence MIT du projet (`LICENSE`).
