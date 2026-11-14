# -*- coding: utf-8 -*-
"""Ligne de commande : compute | simulate | demo | crawl | crawl-static | serve | fetch-gsc | fetch-ga4 | gsc-sites."""
from __future__ import annotations

import argparse
import io
import os
import sys
import time

import numpy as np

from urllib.parse import urlsplit

from . import __version__
from .analysis import AnalysisConfig, analyse, assign_segments, class_of, compile_class_rules, guess_home
from .clean import clean, normalize_url
from .graph import Graph
from .loaders import (DEMAND_COLUMN, WEIGHT_PROFILES, profile_name, choose_value_column, parse_weights, read_business, read_edges, read_groups,
                      read_business_period,
                      read_segment_rules, read_url_table, read_url_weight_list)
from .pagerank import cheirank, log_score, power_iteration
from .simulate import Rules, apply_rules, diff_table

def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


LOG_HOOK = None   # fonction optionnelle appelée avec chaque message (serveur web)


def log(msg: str) -> None:
    if LOG_HOOK is not None:
        try:
            LOG_HOOK(msg)
        except Exception:  # noqa: BLE001
            pass
    print(msg, file=sys.stderr, flush=True)


def add_common_inputs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--edges", required=True, help="CSV d'arêtes (source,target,position,nofollow,anchor ; Screaming Frog ; ou source,target[,weight])")
    p.add_argument("--urls", help="Table d'URL : url,status[,redirect_target][,canonical][,segment][,title] ou export SF internal_html")
    p.add_argument("--business", help="CSV url + revenue/conversions/clicks/sessions (export GSC Pages ou GA4 accepté ; "
                                      "voir aussi fetch-gsc / fetch-ga4)")
    p.add_argument("--business-period", help="Période des données business, affichée avec la valeur dans le rapport et le "
                                             "classeur (ex. « 9 août au 8 novembre 2026 ») ; lue dans le CSV s'il porte une "
                                             "ligne « # période : ... » (fetch-gsc, fetch-ga4)")
    p.add_argument("--business-base-url", help="Origine à préfixer aux chemins relatifs du CSV business "
                                               "(export GA4 « Pages et écrans ») : https://exemple.fr")
    p.add_argument("--value-col", help="Colonne business à utiliser (défaut : revenue > conversions > clicks > sessions)")
    p.add_argument("--segments", help="Règles nom<TAB>regex ou CSV url,segment (défaut : premier segment de chemin)")
    p.add_argument("--strategic", help="CSV url[,poids] des pages stratégiques")
    p.add_argument("--restart", help="CSV url,poids : vecteur de téléportation observé (entrées réelles, backlinks)")
    p.add_argument("--cannibalisation", help="CSV url,group : pages en concurrence sur une même intention")
    p.add_argument("--transitions", help="CSV source,target,transitions|count : clics observés page à page (logs, GA4 BigQuery, Matomo)")
    p.add_argument("--data-blend", type=float, default=0.7,
                   help="Part de la donnée observée dans le mélange avec la position (défaut 0.7 ; 1.0 refusé : piège de circularité)")
    p.add_argument("--embeddings", help="CSV url + colonnes numériques, ou url,vector (JSON) : vecteurs sémantiques des pages")
    p.add_argument("--semantic-decay", type=float, default=0.5,
                   help="Exposant de la pénalité max(0, cos) ** decay sur chaque arête (défaut 0.5 ; 0 = pas de pénalité)")
    p.add_argument("--home", help="URL de la page d'accueil (défaut : devinée)")
    p.add_argument("--weights-profile", default="raisonnable", type=profile_name, choices=list(WEIGHT_PROFILES))
    p.add_argument("--weights", help='Surcharge : "footer=0.02,nav=0.3"')
    p.add_argument("--alpha", type=float, default=0.85, help="Facteur d'amortissement d (défaut 0.85)")
    p.add_argument("--dangling", default="uniform", choices=["uniform", "teleport", "home"],
                   help="Redistribution de la masse des culs-de-sac et des fuites")
    p.add_argument("--tol", type=float, default=1e-9)
    p.add_argument("--max-iter", type=int, default=200)
    p.add_argument("--strip-query", action="store_true", help="Ignorer les paramètres d'URL")
    p.add_argument("--boilerplate-share", type=float, default=0.6,
                   help="Part des pages émettrices à partir de laquelle un lien content/unknown vers une même cible "
                        "est requalifié en gabarit (défaut 0.6 ; 0 désactive)")
    p.add_argument("--boilerplate-min-links", type=int, default=200,
                   help="Nombre de pages émettrices à partir duquel la requalification s'applique quelle que soit la part (défaut 200)")
    p.add_argument("--no-boilerplate-inference", action="store_true",
                   help="Désactiver la requalification en gabarit des liens vers les cibles citées par la quasi-totalité des pages")
    p.add_argument("--uncrawled", default="leak", choices=["leak", "nodes"],
                   help="Cibles découvertes mais absentes de la table d'URL (budget de crawl) : leak = fuite (défaut), "
                        "nodes = nœud sans sortie supposé en 200 (ancien comportement)")
    p.add_argument("--utility-regex", help="Regex des URL utilitaires (mentions légales, panier...)")
    p.add_argument("--labels", help="Classes business : règles nom<TAB>regex (money, acquisition, legal_support, neutre) ou CSV url,label")
    p.add_argument("--no-actions", action="store_true", help="Ne pas calculer le plan d'action simulé")
    p.add_argument("--max-sims", type=int, default=120, help="Nombre maximal de simulations pour le plan d'action (défaut 120)")
    p.add_argument("--money-share", type=float, default=0.80)
    p.add_argument("--starving-percentile", type=float, default=40.0)
    p.add_argument("--overfed-percentile", type=float, default=80.0)
    p.add_argument("--max-nodes", type=int, default=3000, help="Nœuds détaillés exportés dans le visualiseur")
    p.add_argument("--site-name", help="Nom affiché dans les rapports")
    p.add_argument("--out", required=True, help="Dossier de sortie")
    p.add_argument("--excel", action="store_true", help="(sans effet depuis 0.5.0 : le classeur report.xlsx est toujours produit)")
    p.add_argument("--no-excel", action="store_true", help="Ne pas produire le classeur report.xlsx")
    p.add_argument("--cdn", action="store_true",
                   help="Charger les bibliothèques du rapport depuis le CDN au lieu de les embarquer (fichier plus léger, "
                        "mais rapport inutilisable hors ligne)")
    p.add_argument("--links-source", choices=["crawl", "export"],
                   help="Origine des liens, rappelée dans la diapositive Méthode : crawl (HTML servi, sans rendu JavaScript) ou "
                        "export d'un autre outil ; déduit pour un export Screaming Frog, posé par prgraph crawl et serve")
    p.add_argument("--brand-name", help="Nom affiché sur le mode Présentation (consultant, agence) ; rien par défaut")
    p.add_argument("--brand-logo", help="Logo du mode Présentation : fichier PNG, JPEG ou SVG (embarqué dans le rapport)")
    p.add_argument("--brand-color", help="Couleur d'accent du mode Présentation, ex. #1640ef")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="prgraph", description="PageRank interne pondéré, alignement business et graphe.")
    ap.add_argument("--version", action="version", version=f"pagerank-graph {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compute", help="Calculer le PageRank et produire rapports + visualiseur")
    add_common_inputs(c)

    s = sub.add_parser("simulate", help="What-if : modifier le graphe par règles et comparer avant / après")
    add_common_inputs(s)
    s.add_argument("--remove-position", action="append", default=[], help="Position à retirer (répétable) : footer, nav, header, sidebar")
    s.add_argument("--remove-position-target", help="Regex : ne retirer ces positions que vers les cibles correspondantes")
    s.add_argument("--remove-target-regex")
    s.add_argument("--remove-source-regex")
    s.add_argument("--remove-edges", help="CSV source,target à retirer")
    s.add_argument("--add-edges", help="CSV source,target[,position] à ajouter")
    s.add_argument("--cap-position", help='"nav:30" : garder au plus 30 liens nav par page')

    d = sub.add_parser("demo", help="Générer un site synthétique et, avec --run, le calculer")
    d.add_argument("--out", required=True)
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--products", type=int, default=1200)
    d.add_argument("--run", action="store_true", help="Enchaîner compute sur le site généré")
    d.add_argument("--with-data", action="store_true",
                   help="Avec --run : utiliser aussi transitions.csv (blend 0.7) et embeddings.csv (decay 0.5) générés")

    cl = sub.add_parser("crawl", help="Crawl live d'un site → edges.csv + urls.csv (robots.txt respecté)")
    cl.add_argument("--url", required=True)
    cl.add_argument("--out", required=True)
    cl.add_argument("--max-pages", type=int, default=500)
    cl.add_argument("--delay", type=float, default=0.3)
    cl.add_argument("--workers", type=int, default=4)
    cl.add_argument("--no-robots", action="store_true")
    cl.add_argument("--subdomains", action="store_true")
    cl.add_argument("--run", action="store_true", help="Enchaîner compute dans le même dossier")

    sv = sub.add_parser("serve", help="Serveur local : page de lancement, crawl + calcul, rapports")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--runs", default="runs", help="Dossier des analyses")
    sv.add_argument("--open", action="store_true", help="Ouvrir le navigateur")

    cs = sub.add_parser("crawl-static", help="Dossier HTML statique → edges.csv + urls.csv")
    cs.add_argument("--dir", required=True)
    cs.add_argument("--base-url", required=True)
    cs.add_argument("--out", required=True)
    cs.add_argument("--no-trailing-slash", action="store_true")

    def add_fetch_common(fp: argparse.ArgumentParser) -> None:
        fp.add_argument("--days", type=int, default=90, help="Fenêtre en jours, terminée 3 jours avant aujourd'hui (défaut 90)")
        fp.add_argument("--start", help="Date de début AAAA-MM-JJ (remplace --days)")
        fp.add_argument("--end", help="Date de fin AAAA-MM-JJ (défaut : aujourd'hui moins 3 jours)")
        fp.add_argument("--credentials", help="Fichier JSON Google (défaut : variable GOOGLE_APPLICATION_CREDENTIALS)")
        fp.add_argument("--merge", help="CSV business existant à fusionner par URL avec les données récupérées")
        fp.add_argument("--out", required=True, help="CSV business à écrire (url + métriques)")

    fg = sub.add_parser("fetch-gsc", help="Search Console → CSV business (url, clicks, impressions, ctr, position)")
    fg.add_argument("--site", required=True, help="Propriété : sc-domain:exemple.fr ou https://www.exemple.fr/")
    fg.add_argument("--search-type", default="web", choices=["web", "image", "video", "news", "discover", "googleNews"])
    add_fetch_common(fg)

    fa = sub.add_parser("fetch-ga4", help="GA4 Data API → CSV business (url, sessions, conversions, revenue)")
    fa.add_argument("--property", required=True, help="Identifiant de propriété GA4 : 123456 ou properties/123456")
    fa.add_argument("--base-url", required=True, help="Origine du site pour préfixer les chemins : https://exemple.fr")
    fa.add_argument("--dimension", default="landingPagePlusQueryString", choices=["landingPagePlusQueryString", "landingPage"])
    add_fetch_common(fa)

    df = sub.add_parser("diff", help="Comparer deux analyses : pages protégées qui reculent, liens disparus vers les cibles")
    df.add_argument("--before", required=True, help="Dossier de l'analyse de référence")
    df.add_argument("--after", required=True, help="Dossier de la nouvelle analyse")
    df.add_argument("--protected", help="CSV url des cibles à protéger (en plus des money / stratégiques / famine de la référence)")
    df.add_argument("--drop-threshold", type=float, default=5.0, help="Recul de score (points) qui déclenche l'alerte")
    df.add_argument("--out", help="Dossier de sortie (défaut : dossier de la nouvelle analyse)")

    ls = sub.add_parser("gsc-sites", help="Lister les propriétés Search Console accessibles avec ces identifiants")
    ls.add_argument("--credentials")
    return ap


def read_brand(a) -> dict | None:
    """Marque du mode Présentation (nom, logo embarqué en data URI, couleur), rien par défaut : l'outil est neutre."""
    import base64
    import re as _re
    name = getattr(a, "brand_name", None)
    logo_path = getattr(a, "brand_logo", None)
    color = getattr(a, "brand_color", None)
    if not (name or logo_path or color):
        return None
    brand = {"name": (name or "").strip()[:80] or None, "logo": None, "color": None}
    if color:
        if not _re.fullmatch(r"#[0-9a-fA-F]{6}", color.strip()):
            raise ValueError(f"--brand-color : couleur hexadécimale attendue (#1640ef), reçu « {color} »")
        brand["color"] = color.strip().lower()
    if logo_path:
        ext = os.path.splitext(logo_path)[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}.get(ext)
        if mime is None:
            raise ValueError(f"--brand-logo : PNG, JPEG ou SVG attendu, reçu « {logo_path} »")
        with open(logo_path, "rb") as fh:
            raw = fh.read()
        if len(raw) > 512 * 1024:
            raise ValueError("--brand-logo : fichier de plus de 512 Ko, réduire le logo")
        if mime == "image/svg+xml" and _re.search(rb"<script|on\w+\s*=|javascript:", raw, _re.I):
            raise ValueError("--brand-logo : SVG contenant du script refusé")
        brand["logo"] = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return brand


# ── Pipeline ──────────────────────────────────────────────────────────────────

class Pipeline:
    def __init__(self, args):
        self.args = args
        self.weights = parse_weights(args.weights_profile, args.weights)

    def load_and_compute(self):
        a = self.args
        t0 = time.time()
        table = {}
        if a.urls:
            for info in read_url_table(a.urls):
                table[normalize_url(info.url, a.strip_query)] = info
            log(f"table d'URL : {len(table)} lignes")
        edges_iter, fmt = read_edges(a.edges)
        log(f"arêtes : format {fmt}")
        self.edges_format = fmt
        # Cibles jamais requalifiées en gabarit : pages stratégiques et pages money (une page de conversion liée
        # dans chaque article est un choix éditorial, pas un bloc répété à atténuer).
        self.strategic = read_url_weight_list(a.strategic) if a.strategic else None
        strategic_set = {normalize_url(u, a.strip_query) for u in (self.strategic or {})}
        label_rules = None
        if a.labels:
            lrules, _ = read_segment_rules(a.labels)
            label_rules = compile_class_rules([(name, rx.pattern) for name, rx in lrules]) if lrules else None

        def protect(url: str) -> bool:
            if url in strategic_set:
                return True
            if label_rules:
                path = urlsplit(url).path or "/"
                return any(name == "money" and rx.search(path) for name, rx in label_rules)
            return class_of(url) == "money"

        site_host = urlsplit(a.home).netloc.lower() if a.home else None
        self.cg = clean(edges_iter, table, self.weights, strip_query=a.strip_query,
                        boilerplate_share=0.0 if a.no_boilerplate_inference else a.boilerplate_share,
                        boilerplate_min_links=a.boilerplate_min_links, protect=protect, site_host=site_host,
                        uncrawled=a.uncrawled)
        st = self.cg.stats
        log(f"nettoyage : {st['nodes']} nœuds, {st['edges_out']} arêtes uniques ({st['edges_in']} lignes lues, "
            f"{st['duplicates_collapsed']} doublons, {st['lost_404']} vers 4xx/5xx, {st.get('lost_external', 0)} vers un autre hôte, "
            f"{st.get('lost_uncrawled', 0)} vers des URL non crawlées, {st['nofollow']} nofollow, "
            f"{st['redirect_or_canonical_rewrites']} réécritures, {st.get('boilerplate_reclassified', 0)} liens requalifiés en gabarit "
            f"vers {st.get('boilerplate_targets', 0)} URL ({st.get('boilerplate_protected', 0)} cibles money / stratégiques épargnées), "
            f"{st.get('uncrawled_nodes', 0)} URL non crawlées gardées en nœuds) en {time.time() - t0:.1f}s")
        self.g = Graph.from_clean(self.cg)
        if self.g.n == 0:
            raise SystemExit("aucun nœud exploitable : vérifier les fichiers d'entrée")

        # Pondération au-delà de la position : clics observés puis composante sémantique.
        self.weighting: dict | None = None
        self.w_ref = None
        if a.transitions:
            from .weights import apply_transitions, read_transitions
            obs = read_transitions(a.transitions)
            try:
                self.g, tst = apply_transitions(self.g, obs, blend=a.data_blend, strip_query=a.strip_query)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            self.weighting = {"transitions": tst}
            log(f"clics observés : {tst['pairs_matched']}/{tst['pairs_read']} couples retrouvés, "
                f"{tst['edges_covered_share']:.1%} des arêtes et {tst['mass_covered_share']:.1%} de la masse heuristique couverts, "
                f"{tst['sources_with_data']} pages sources avec donnée, mélange {tst['blend']:.0%} donnée / {1 - tst['blend']:.0%} position")
        if a.embeddings:
            from .weights import apply_semantic, read_embeddings
            emb = read_embeddings(a.embeddings)
            self.w_ref = self.g.w.copy()
            try:
                self.g, sst, _ = apply_semantic(self.g, emb, decay=a.semantic_decay, strip_query=a.strip_query)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            self.weighting = {**(self.weighting or {}), "embeddings": sst}
            log(f"composante sémantique : {sst['nodes_embedded']}/{sst['nodes_total']} pages avec vecteur (dimension {sst['dimension']}), "
                f"{sst['edges_scored_share']:.1%} des arêtes pénalisées, facteur moyen {sst['factor_mean']:.2f}, "
                f"{sst['edges_zeroed']} arêtes à zéro, decay {sst['decay']}")

        self.home = None
        if a.home:
            h = normalize_url(a.home, a.strip_query)
            self.home = self.g.urls.index(h) if h in self.g.urls else None
            if self.home is None:
                log(f"avertissement : accueil {a.home} absent du graphe, accueil deviné")
        if self.home is None:
            self.home = guess_home(self.g.urls)

        teleport = None
        if a.restart:
            vec = read_url_weight_list(a.restart)
            idx = {u: i for i, u in enumerate(self.g.urls)}
            teleport = np.zeros(self.g.n)
            hit = 0
            for u, w in vec.items():
                k = normalize_url(u, a.strip_query)
                if k in idx:
                    teleport[idx[k]] += w
                    hit += 1
            log(f"téléportation observée : {hit}/{len(vec)} URL retrouvées")
            if teleport.sum() <= 0:
                teleport = None
        self.teleport = teleport      # réutilisé par simulate, le plan d'action et le visualiseur (meta.teleport)

        t1 = time.time()
        self.pr_res = power_iteration(self.g, alpha=a.alpha, teleport=teleport, dangling=a.dangling,
                                      home=self.home, tol=a.tol, max_iter=a.max_iter)
        self.ch_res = cheirank(self.g, alpha=a.alpha, tol=a.tol, max_iter=a.max_iter)
        self.pr, self.chei = self.pr_res.pr, self.ch_res.pr
        self.score, self.cscore = log_score(self.pr), log_score(self.chei)
        log(f"PageRank : {self.pr_res.iterations} itérations, résidu {self.pr_res.residual:.1e}, "
            f"masse culs-de-sac/fuites {self.pr_res.dangling_mass:.1%}, {time.time() - t1:.2f}s")

        rules, mapping = ([], {})
        if a.segments:
            rules, mapping = read_segment_rules(a.segments)
            mapping = {normalize_url(k, a.strip_query): v for k, v in mapping.items()}
        self.segments = assign_segments(self.g.urls, self.cg.given_segments, rules, mapping)

        self.business, cols = (None, [])
        if a.business:
            self.business, cols = read_business(a.business, getattr(a, "business_base_url", None))
            log(f"business : {len(self.business)} URL, métriques {cols}")
        self.value_col = choose_value_column(cols, a.value_col) if cols else None
        self.business_cols = list(cols)
        # Lecture « demande » : impressions quand elles ne sont pas la colonne de valeur retenue.
        self.demand_col = DEMAND_COLUMN if (DEMAND_COLUMN in cols and self.value_col and self.value_col != DEMAND_COLUMN) else None
        self.groups = read_groups(a.cannibalisation) if a.cannibalisation else None
        self.cfg = AnalysisConfig(alpha=a.alpha, money_share=a.money_share, starving_percentile=a.starving_percentile,
                                  overfed_percentile=a.overfed_percentile, strip_query=a.strip_query)
        if a.utility_regex:
            self.cfg.utility_regex = a.utility_regex
        if a.labels:
            lrules, lmap = read_segment_rules(a.labels)
            self.cfg.class_rules = [(name, rx.pattern) for name, rx in lrules] or None
            self.cfg.class_mapping = {normalize_url(k, a.strip_query): v for k, v in lmap.items()} or None
        self.an = analyse(self.g, self.pr, self.score, self.chei, self.cscore, self.segments, self.business,
                          self.value_col, self.strategic, self.groups, self.home, self.cfg, w_ref=self.w_ref,
                          demand_col=self.demand_col)
        self.an.uncrawled = self.cg.uncrawled
        if not a.no_actions:
            from .actions import build_plan
            t2 = time.time()
            self.an.actions = build_plan(self.g, self.pr, self.score, self.an, self.weights, self.cg.stats,
                                         alpha=a.alpha, dangling=a.dangling, max_sims=a.max_sims, titles=self.cg.titles,
                                         teleport=self.teleport)
            log(f"plan d'action : {len(self.an.actions)} actions simulées en {time.time() - t2:.1f}s, "
                f"alignement business {self.an.alignment.get('score', 0):.0f}/100")
            from .actions import build_scenario
            self.an.scenario = build_scenario(self.g, self.pr, self.an, self.weights, alpha=a.alpha, dangling=a.dangling,
                                              teleport=self.teleport, actions=self.an.actions)
            if self.an.scenario:
                sc = self.an.scenario
                log(f"scénario gabarit : alignement {sc['before']['alignment']:.0f} → {sc['after']['alignment']:.0f}/100, "
                    f"money + acquisition {sc['before']['productive_share']:.1%} → {sc['after']['productive_share']:.1%}")
        if self.business:
            with_value = int((self.an.value > 0).sum())
            log(f"jointure business : {with_value} pages du graphe avec une valeur ({self.value_col}), "
                f"couverture {with_value / max(self.an.n, 1):.1%} des {self.an.n} pages"
                + (f" ; demande ({self.demand_col}) : {int((self.an.demand > 0).sum())} pages" if self.demand_col else ""))
        self.meta = {
            "site": a.site_name or ((urlsplit(self.g.urls[self.home]).netloc if self.home is not None else "") or "site"),
            "alpha": a.alpha, "dangling": a.dangling, "weights": self.weights, "weights_profile": a.weights_profile,
            "starving_percentile": a.starving_percentile, "overfed_percentile": a.overfed_percentile,
            "stats": self.cg.stats, "iterations": self.pr_res.iterations, "converged": self.pr_res.converged,
            "weighting": self.weighting, "business_cols": self.business_cols, "demand_col": self.demand_col,
            "brand": read_brand(a),
            "links_source": getattr(a, "links_source", None) or ("export" if getattr(self, "edges_format", "") == "screamingfrog" else None),
            "value_period": (getattr(a, "business_period", None) or (read_business_period(a.business) if a.business else None)),
            # Une valeur de trafic (clics, impressions, sessions) ne mesure pas ce qui rapporte : les pages de
            # conversion en captent peu. Le rapport le dit à côté de chaque chiffre de valeur.
            "value_kind": ("trafic" if (self.value_col or "") in ("clicks", "impressions", "sessions") else
                           ("business" if self.value_col else None)),
        }
        return self

    def export(self, extra_cols=None):
        from . import export as ex
        a = self.args
        os.makedirs(a.out, exist_ok=True)
        ex.write_pages_csv(os.path.join(a.out, "pages.csv"), self.g, self.pr, self.score, self.chei, self.cscore,
                           self.an, self.cg.titles, extra=extra_cols)
        ex.write_segments_csv(os.path.join(a.out, "segments.csv"), self.an)
        ex.write_flows_csv(os.path.join(a.out, "flows_segments.csv"), self.an)
        ex.write_findings_md(os.path.join(a.out, "findings.md"), self.g, self.pr, self.score, self.chei, self.cscore,
                             self.an, self.meta, self.cg.stats,
                             {"iterations": self.pr_res.iterations, "converged": self.pr_res.converged})
        data = ex.build_graph_json(self.g, self.pr, self.score, self.chei, self.cscore, self.an, self.cg.titles,
                                   self.meta, max_nodes=a.max_nodes, teleport=self.teleport)
        ex.write_json(os.path.join(a.out, "graph.json"), data)
        ex.write_report_html(os.path.join(a.out, "report.html"), data, cdn=getattr(a, "cdn", False))
        if self.an.actions:
            from .actions import write_dev_brief
            ex.write_actions_csv(os.path.join(a.out, "actions.csv"), self.an.actions)
            write_dev_brief(os.path.join(a.out, "brief_dev.md"), self.meta.get("site", "site"), self.an, self.an.actions, self.g)
        excel = not getattr(a, "no_excel", False)
        if excel:
            try:
                ex.write_excel(os.path.join(a.out, "report.xlsx"), a.out)
            except PermissionError:
                # Classeur ouvert dans Excel pendant le calcul : le reste des sorties est écrit, on le dit.
                excel = False
                log("avertissement : report.xlsx est ouvert dans un tableur, non réécrit (le fermer puis relancer)")
        log(f"sorties écrites dans {a.out} : pages.csv, segments.csv, flows_segments.csv, findings.md, graph.json, report.html"
            + (", actions.csv, brief_dev.md" if self.an.actions else "") + (", report.xlsx" if excel else ""))
        return data

    def summary(self) -> str:
        an = self.an
        L = [f"Nœuds {an.n}, arêtes {self.g.m}, culs-de-sac {int(an.dangling.sum())}, orphelines (plancher) {len(an.orphans)}"]
        total = sum(an.zone_flows.values())
        boiler = sum(an.zone_flows.get(p, 0.0) for p in ("header", "nav", "footer", "sidebar", "boilerplate")) / total
        L.append(f"Jus circulant par le gabarit : {boiler:.1%}")
        if an.value_col:
            L.append(f"Pages en famine ({an.value_col}) : {int(an.starving.sum())} ; gouffres utilitaires : {int(an.overfed.sum())}")
            worst = sorted(an.seg_table, key=lambda r: -r["gap"])
            if worst:
                r = worst[0]
                L.append(f"Segment le plus sous-alimenté : {r['segment']} ({r['value_share']:.0%} de la valeur, {r['pr_share']:.0%} du PR)")
        return "\n".join(L)


def _position_or_fail(raw: str, option: str) -> str:
    """Une position inconnue est une faute de frappe, pas un « unknown » silencieux (qui retirerait d'autres liens)."""
    from .loaders import POSITIONS, normalize_position
    p = normalize_position(raw)
    if p == "unknown" and str(raw).strip().lower() not in ("unknown", ""):
        raise ValueError(f"{option} : position « {raw} » inconnue (choix : {', '.join(POSITIONS)})")
    return p


def parse_rules(a) -> Rules:
    import re as _re
    from .loaders import read_table, pick, normalize_position
    rules = Rules(remove_positions=[_position_or_fail(p, "--remove-position") for p in a.remove_position],
                  remove_positions_target=a.remove_position_target, remove_target_regex=a.remove_target_regex,
                  remove_source_regex=a.remove_source_regex)
    if a.remove_edges:
        rules.remove_edges = [(pick(r, "source"), pick(r, "target")) for r in read_table(a.remove_edges)]
    if a.add_edges:
        rules.add_edges = [(pick(r, "source"), pick(r, "target"), normalize_position(pick(r, "position", default="content")))
                           for r in read_table(a.add_edges)]
    if a.cap_position:
        m = _re.fullmatch(r"\s*([A-Za-z-]+)\s*:\s*(\d+)\s*", a.cap_position)
        if not m:
            raise ValueError(f'--cap-position : format attendu "position:N" (ex. nav:30), reçu « {a.cap_position} »')
        rules.cap_position = (_position_or_fail(m.group(1), "--cap-position"), int(m.group(2)))
    return rules


def cmd_compute(a) -> int:
    p = Pipeline(a).load_and_compute()
    p.export()
    print(p.summary())
    return 0


def cmd_simulate(a) -> int:
    p = Pipeline(a).load_and_compute()
    rules = parse_rules(a)
    g2, st = apply_rules(p.g, rules, p.weights)
    log(f"simulation : {' ; '.join(rules.describe())} → {st['removed']} arêtes retirées, {st['added']} ajoutées, "
        f"{st.get('moved', 0)} déplacées"
        + (f", {st['added_unknown_url']} ajouts ignorés (URL hors graphe)" if st["added_unknown_url"] else ""))
    # Même modèle que le calcul de référence : vecteur de téléportation (--restart) conservé.
    pr2 = power_iteration(g2, alpha=a.alpha, teleport=p.teleport, dangling=a.dangling, home=p.home, tol=a.tol,
                          max_iter=a.max_iter).pr
    score2 = log_score(pr2)
    d = diff_table(p.g.urls, p.pr, pr2, p.score, score2)
    p.export(extra_cols={"sim_pagerank": pr2, "sim_score": score2, "delta_score": d["score_delta"], "delta_rel_pr": d["rel"]})
    os.makedirs(a.out, exist_ok=True)
    L = [f"Outil : prgraph {__version__} · {time.strftime('%Y-%m-%d')}\n",
         f"# Simulation : {' ; '.join(rules.describe())}\n",
         f"Arêtes retirées : {st['removed']} ; ajoutées : {st['added']}. Scores log recalculés sur le nouveau graphe.\n"]
    an = p.an
    strat = np.flatnonzero(an.strategic | an.money)
    if strat.size:
        gain = float(pr2[strat].sum() - p.pr[strat].sum())
        L.append(f"**Pages money / stratégiques** : part de PR {p.pr[strat].sum():.2%} → {pr2[strat].sum():.2%} ({gain:+.2%} pt)\n")
    util = np.flatnonzero(an.utility)
    if util.size:
        L.append(f"**Pages utilitaires** : part de PR {p.pr[util].sum():.2%} → {pr2[util].sum():.2%}\n")
    for title, lst in (("Plus fortes hausses", d["gainers"]), ("Plus fortes baisses", d["losers"])):
        L.append(f"## {title}\n")
        L.append("| URL | Score avant | Score après | Δ score | Δ PR relatif |\n|---|---|---|---|---|")
        for i in lst[:30]:
            L.append(f"| {p.g.urls[i]} | {p.score[i]:.0f} | {score2[i]:.0f} | {d['score_delta'][i]:+.1f} | {d['rel'][i]:+.0%} |")
        L.append("")
    with io.open(os.path.join(a.out, "simulation.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("\n".join(L[:6]))
    return 0


def cmd_demo(a) -> int:
    from .demo import generate
    info = generate(a.out, seed=a.seed, n_products=a.products)
    log(f"site de démo : {info['pages']} pages, {info['edges']} liens, {info['heroes']} produits héros, "
        f"{info['transitions']} transitions observées, {info['embedded']} vecteurs → {a.out}")
    if a.run:
        out = os.path.join(a.out, "out-data" if a.with_data else "out")
        argv = ["compute", "--edges", os.path.join(a.out, "edges.csv"), "--urls", os.path.join(a.out, "urls.csv"),
                "--business", os.path.join(a.out, "business.csv"), "--segments", os.path.join(a.out, "segments.tsv"),
                "--strategic", os.path.join(a.out, "strategic.csv"),
                "--cannibalisation", os.path.join(a.out, "cannibalisation.csv"),
                "--site-name", "Démo boutique outdoor", "--out", out]
        if a.with_data:
            argv += ["--transitions", os.path.join(a.out, "transitions.csv"), "--data-blend", "0.7",
                     "--embeddings", os.path.join(a.out, "embeddings.csv"), "--semantic-decay", "0.5"]
        return main(argv)
    return 0


def cmd_crawl(a) -> int:
    from .crawl_live import CrawlConfig, crawl
    url = a.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    if not urlsplit(url).netloc:
        raise ValueError(f"--url : adresse invalide « {a.url} » (attendu https://exemple.fr/)")
    cfg = CrawlConfig(start_url=url, out_dir=a.out, max_pages=a.max_pages, delay=a.delay, workers=a.workers,
                      respect_robots=not a.no_robots, include_subdomains=a.subdomains)
    stats = crawl(cfg, progress=lambda m, **kw: log(m))
    fetched = int(stats.get("fetched", stats.get("pages", 0)))
    if fetched == 0:
        why = " (robots.txt indisponible)" if stats.get("robots_unavailable") else (
            f" ({stats.get('blocked', 0)} URL interdites par robots.txt)" if stats.get("blocked") else "")
        log(f"crawl : aucune page récupérée{why} ; pas de calcul")
        return 2
    if a.run:
        return main(["compute", "--edges", os.path.join(a.out, "edges.csv"), "--urls", os.path.join(a.out, "urls.csv"),
                     "--home", url, "--strip-query", "--links-source", "crawl", "--out", a.out])
    return 0


def cmd_serve(a) -> int:
    from .server import serve
    serve(port=a.port, runs_dir=a.runs, open_browser=a.open)
    return 0


def cmd_diff(a) -> int:
    from .diff import DiffInputError, diff_runs, exit_code, write_diff_csv, write_diff_md
    protected = [u for u in read_url_weight_list(a.protected)] if a.protected else None
    try:
        d = diff_runs(a.before, a.after, protected, drop_threshold=a.drop_threshold)
    except DiffInputError as e:
        print(f"Comparaison impossible : {e}", file=sys.stderr)
        return 2
    out = a.out or a.after
    os.makedirs(out, exist_ok=True)
    write_diff_md(os.path.join(out, "diff.md"), d)
    write_diff_csv(os.path.join(out, "diff.csv"), d)
    print(f"Pages protégées suivies : {len(d['protected'])} ; reculs ≥ {a.drop_threshold:.0f} pt : {len(d['regressions'])} ; "
          f"liens disparus vers cibles protégées : {len(d['lost_to_protected'])} ; liens disparus / nouveaux : "
          f"{len(d['lost_edges'])} / {len(d['new_edges'])} ; pages protégées absentes de la nouvelle analyse : "
          f"{len(d['protected_missing'])}")
    if d["edges_missing"]:
        print(f"Comparaison incomplète : edges.csv absent dans {', '.join(d['edges_missing'])}, liens disparus non contrôlés")
    print(f"Rapport : {os.path.join(out, 'diff.md')}")
    return exit_code(d)


def cmd_crawl_static(a) -> int:
    from .crawl_static import crawl
    info = crawl(a.dir, a.base_url, a.out, trailing_slash=not a.no_trailing_slash)
    log(f"crawl statique : {info['pages']} pages, {info['edges']} liens, {info['missing_targets']} cibles sans fichier → {a.out}")
    return 0


# ── Connecteurs Search Console / GA4 ──────────────────────────────────────────

def _fetch_period(a) -> tuple[str, str]:
    from . import connectors
    start, end = connectors.period_from_days(a.days)
    return (a.start or start), (a.end or end)


def _write_fetched(a, rows: list[dict], source: str) -> int:
    from . import connectors
    table = connectors.rows_to_table(rows)
    n_fetched = len(table)
    if a.merge:
        other, other_cols = read_business(a.merge)
        table = connectors.merge_business(other, table)
        log(f"fusion avec {a.merge} : {len(other)} URL ({', '.join(other_cols)}) + {n_fetched} URL {source} → {len(table)} URL")
    out_dir = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(out_dir, exist_ok=True)
    start, end = _fetch_period(a)
    header = connectors.write_business_csv(a.out, table, period=f"{start} au {end}")
    log(f"{a.out} : {len(table)} URL, colonnes {', '.join(header[1:])}")
    print(f"{len(table)} URL écrites dans {a.out} ({', '.join(header[1:])})")
    return 0


def cmd_fetch_gsc(a) -> int:
    from . import connectors
    start, end = _fetch_period(a)
    try:
        creds = connectors.load_credentials(connectors.GSC_SCOPES, a.credentials)
        log(f"Search Console : {a.site}, du {start} au {end}, type {a.search_type}")
        rows = connectors.fetch_gsc_pages(a.site, start, end, creds, search_type=a.search_type)
    except (connectors.MissingDependency, connectors.CredentialsError) as exc:
        raise SystemExit(str(exc)) from exc
    log(f"Search Console : {len(rows)} URL récupérées")
    return _write_fetched(a, rows, "Search Console")


def cmd_fetch_ga4(a) -> int:
    from . import connectors
    start, end = _fetch_period(a)
    try:
        creds = connectors.load_credentials(connectors.GA4_SCOPES, a.credentials)
        log(f"GA4 : propriété {a.property}, du {start} au {end}, dimension {a.dimension}, base {a.base_url}")
        rows = connectors.fetch_ga4_landing_pages(a.property, start, end, creds, base_url=a.base_url, dimension=a.dimension)
    except (connectors.MissingDependency, connectors.CredentialsError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    log(f"GA4 : {len(rows)} pages d'atterrissage récupérées")
    return _write_fetched(a, rows, "GA4")


def cmd_gsc_sites(a) -> int:
    from . import connectors
    try:
        creds = connectors.load_credentials(connectors.GSC_SCOPES, a.credentials)
        sites = connectors.list_gsc_sites(creds)
    except (connectors.MissingDependency, connectors.CredentialsError) as exc:
        raise SystemExit(str(exc)) from exc
    for s in sites:
        print(f"{s['site_url']}\t{s['permission']}")
    if not sites:
        print("aucune propriété accessible avec ces identifiants", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    cmd = {"compute": cmd_compute, "diff": cmd_diff, "simulate": cmd_simulate, "demo": cmd_demo, "crawl-static": cmd_crawl_static,
           "crawl": cmd_crawl, "serve": cmd_serve, "fetch-gsc": cmd_fetch_gsc, "fetch-ga4": cmd_fetch_ga4,
           "gsc-sites": cmd_gsc_sites}[args.cmd]
    try:
        return cmd(args)
    except ValueError as exc:
        # Erreur d'entrée (fichier, option, format) : un message, pas une trace de pile.
        raise SystemExit(f"erreur d'entrée : {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
