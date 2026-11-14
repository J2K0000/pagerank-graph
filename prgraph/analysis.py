# -*- coding: utf-8 -*-
"""Lecture du PageRank : segments, profondeur réelle, quadrants PR × CheiRank, alignement
business, fuites entre silos, trous noirs, pages utilitaires sur-alimentées."""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import numpy as np
from scipy import sparse
from scipy.stats import spearmanr

from .graph import Graph
from .loaders import POSITIONS

# ── Classes business ─────────────────────────────────────────────────────────
# Étiquette business de chaque URL, décidée sur les segments de chemin et non sur des sous-chaînes :
# « /blog/legal-tech » est un article, « /legal » une page légale. Règle d'évaluation :
#   1. premier segment = rubrique de contenu (blog, glossaire...)  → acquisition, quoi que contienne la suite ;
#   2. un segment COMMENCE par un mot légal / support (legal, privacy, cart...) → legal_support ;
#   3. un segment commence par un mot de confiance (about, team, press, faq)  → confiance ;
#   4. un segment commence par un mot money (pricing, demo, product...)       → money ;
#   5. un segment commence par un mot d'acquisition (guide, ebook, webinar...) → acquisition ;
#   6. sinon neutre.
# Surchargeable par --labels (règles nom<TAB>regex, à l'ancienne, ou CSV url,label).
CLASS_NAMES = ["money", "acquisition", "confiance", "legal_support", "neutre", "accueil"]
CLASS_LABELS = {"money": "Money / conversion", "acquisition": "Acquisition / piliers", "confiance": "Confiance (à propos, presse, FAQ)",
                "legal_support": "Légal / support", "neutre": "Neutre", "accueil": "Accueil"}
HUB_SEGMENTS = {"blog", "blogs", "article", "articles", "actualites", "actualite", "news", "magazine", "glossary", "glossaire",
                "lexique", "definitions", "definition", "guide", "guides", "ebook", "ebooks", "e-book", "e-books", "livre-blanc",
                "livres-blancs", "whitepaper", "whitepapers", "webinar", "webinars", "webinars-podcasts", "podcast", "podcasts",
                "ressources", "ressource", "resources", "resource", "tools-templates", "templates", "modeles", "wiki", "academy",
                "academie", "tutoriels", "tutorials", "conseils", "customer", "customers", "client", "clients", "cas-clients",
                "case-studies", "case-study", "temoignages", "etudes-de-cas", "product-tour", "demo-produit", "demos-produit"}
LEGAL_WORDS = {"mentions-legales", "mentions", "legal", "legals", "legal-center", "legal-notice", "cgv", "cgu", "cgs", "conditions",
               "conditions-generales", "terms", "terms-of-use", "terms-and-conditions", "confidentialite", "privacy", "privacy-policy",
               "cookies", "cookie", "rgpd", "gdpr", "login", "connexion", "logout", "register", "mon-compte", "account", "panier",
               "cart", "wishlist", "favoris", "plan-du-site", "sitemap", "recrutement", "careers", "career", "jobs", "job",
               "search", "recherche", "accessibilite", "accessibility", "credits", "copyright", "data-processing",
               "trial-agreement", "supplementary-terms", "newsletter", "informations-legales", "politique-de-confidentialite"}
TRUST_WORDS = {"a-propos", "about", "about-us", "qui-sommes-nous", "equipe", "team", "presse", "press", "faq", "faqs",
               "notre-histoire", "our-story", "valeurs", "values", "engagements"}
MONEY_WORDS = {"demo", "demander-une-demo", "request-a-demo", "demander", "devis", "quote", "pricing", "tarifs", "tarif", "prix",
               "contact", "contact-us", "contactez-nous", "essai", "essai-gratuit", "trial", "free-trial", "signup", "sign-up",
               "inscription", "checkout", "commande", "order", "produit", "produits", "product", "products", "offre", "offres",
               "offer", "offers", "solution", "solutions", "platform", "plateforme", "service", "services", "boutique", "shop",
               "integration", "integrations", "feature", "features", "fonctionnalites", "fonctionnalite", "comparatif",
               "comparatifs", "versus", "vs", "alternative", "alternatives", "logiciel", "logiciels", "software",
               "categorie", "categories", "category", "collection", "collections", "prestation", "prestations", "reserver",
               "booking", "rendez-vous", "appointment"}
MONEY_EXACT = {"p", "c"}          # /p/<produit>, /c/<categorie> : segment entier seulement, jamais « vitamin-c »
LEGAL_SUFFIXES = ("-policy", "-notice", "-terms", "-conditions", "-legales", "-legal", "-cookies", "-rgpd", "-gdpr")
ACQ_WORDS = HUB_SEGMENTS | {"calculateur", "calculator", "calculatrice", "outil", "outils", "tool", "tools"}
# Vocabulaire déduit du slug, sans frontière de mot : « marque-vs-concurrent » ou « logiciel-de-paie » restent money.
MONEY_INFIX = re.compile(r"(^|-)(vs|versus|alternative|alternatives)(-|$)", re.I)
DEFAULT_CLASS_RULES = None   # conservé pour compatibilité : None = règles par segments ci-dessus
# Cible par défaut quand aucune valeur business n'est fournie (part du PageRank souhaitée par classe).
DEFAULT_TARGET_SHARES = {"money": 0.38, "acquisition": 0.38, "confiance": 0.04, "accueil": 0.08, "neutre": 0.12, "legal_support": 0.0}
# Pages utilitaires (gouffres) : légal / support et mécanique du compte, jamais une page de confiance.
UTILITY_WORDS = LEGAL_WORDS | {"livraison", "retours", "retour", "returns", "shipping"}
DEFAULT_UTILITY_REGEX = None   # conservé pour compatibilité : None = UTILITY_WORDS par segment


def _segments(path: str) -> list[str]:
    segs = [s.lower() for s in path.split("/") if s]
    return [re.sub(r"\.(html?|php|aspx?)$", "", s) for s in segs]


LANG_SEG = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")


def _starts_with_word(seg: str, words: set[str]) -> bool:
    """Le segment est le mot, commence par le mot (« pricing-2026 ») ou se termine par lui
    (« sales-compensation-platform », « shopping-cart ») ; jamais une sous-chaîne au milieu d'un autre mot."""
    if seg in words:
        return True
    parts = seg.split("-")
    for k in (1, 2, 3):
        if "-".join(parts[:k]) in words or "-".join(parts[-k:]) in words:
            return True
    return False


def _is_legal_segment(seg: str, words: set[str]) -> bool:
    """Les pages légales ont des slugs courts et stéréotypés : le segment entier est dans la liste, ou il se
    termine par un suffixe légal (« cookie-policy », « conditions-generales-de-vente » non, « shipping-terms » oui).
    Un mot légal en tête d'un slug plus long (« job-board-software », « privacy-first-analytics ») n'en fait pas
    une page légale."""
    return seg in words or seg.endswith(LEGAL_SUFFIXES)


def _body_segments(path: str) -> list[str] | None:
    """Segments hors préfixe de langue ; None pour l'accueil (racine, ou racine d'une langue : /fr/)."""
    segs = _segments(path or "/")
    if not segs:
        return None
    if LANG_SEG.match(segs[0]):
        segs = segs[1:]
        if not segs:
            return None
    return segs


def class_of(url: str) -> str:
    """Classe business d'une URL par ses segments de chemin (voir le commentaire de CLASS_NAMES)."""
    body = _body_segments(urlsplit(url).path)
    if body is None:
        return "accueil"
    if body[0] in HUB_SEGMENTS:
        return "acquisition"
    if any(_is_legal_segment(s, LEGAL_WORDS) for s in body):
        return "legal_support"
    if any(s in TRUST_WORDS for s in body):
        return "confiance"
    if any(s in MONEY_EXACT or _starts_with_word(s, MONEY_WORDS) or MONEY_INFIX.search(s) for s in body):
        return "money"
    if any(_starts_with_word(s, ACQ_WORDS) for s in body):
        return "acquisition"
    return "neutre"


def is_utility(url: str, extra_regex: str | None = None) -> bool:
    """Page utilitaire (candidate gouffre) : segment légal / support entier, hors rubrique de contenu."""
    if extra_regex:
        return bool(re.compile(extra_regex, re.I).search(urlsplit(url).path))
    body = _body_segments(urlsplit(url).path)
    if body is None or body[0] in HUB_SEGMENTS:
        return False
    return any(_is_legal_segment(s, UTILITY_WORDS) for s in body)


def compile_class_rules(rules: list[tuple[str, str]] | None = None) -> list[tuple[str, re.Pattern]] | None:
    """Règles regex fournies par --labels ; None = classification par segments (class_of)."""
    return [(name, re.compile(rx, re.I)) for name, rx in rules] if rules else None


def classify(urls: list[str], home: int | None, rules: list[tuple[str, re.Pattern]] | None,
             mapping: dict[str, str] | None = None, strategic: np.ndarray | None = None) -> list[str]:
    out = []
    for i, u in enumerate(urls):
        if home is not None and i == home:
            out.append("accueil")
            continue
        if mapping and u in mapping:
            out.append(mapping[u])
            continue
        if strategic is not None and strategic[i]:
            out.append("money")
            continue
        if rules:
            path = urlsplit(u).path or "/"
            for name, rx in rules:
                if rx.search(path):
                    out.append(name)
                    break
            else:
                out.append("neutre")
            continue
        out.append(class_of(u))
    return out


def alignment_score(pr: np.ndarray, classes: list[str], value_share: np.ndarray | None,
                    exclude_home: bool = True) -> dict:
    """Score 0-100 = 100 × (1 − distance en variation totale entre la répartition du PageRank par
    classe et la répartition cible). Cible = répartition de la valeur business par classe si elle
    existe, sinon DEFAULT_TARGET_SHARES. 100 = le jus atterrit exactement là où la valeur est.
    L'accueil est exclu de la distance (ses clics sont des recherches de marque, pas une valeur que le
    maillage doit aligner) ; sa ligne reste affichée à titre d'information."""
    cls = np.array(classes)
    pr_by = {c: float(pr[cls == c].sum()) for c in CLASS_NAMES}
    has_value = value_share is not None and float(value_share.sum()) > 0
    if has_value:
        target = {c: float(value_share[cls == c].sum()) for c in CLASS_NAMES}
        basis = "valeur business"
    else:
        target = dict(DEFAULT_TARGET_SHARES)
        basis = "cible par défaut"
    scored = [c for c in CLASS_NAMES if not (exclude_home and c == "accueil")]
    pr_tot = sum(pr_by[c] for c in scored) or 1.0
    tg_tot = sum(target[c] for c in scored) or 1.0
    tvd = 0.5 * sum(abs(pr_by[c] / pr_tot - target[c] / tg_tot) for c in scored)
    counts = {c: int((cls == c).sum()) for c in CLASS_NAMES}
    return {"score": round(100.0 * (1.0 - tvd), 1), "basis": basis, "pr_share": pr_by, "target_share": target,
            "counts": counts, "waste_share": pr_by["legal_support"],
            "productive_share": pr_by["money"] + pr_by["acquisition"], "home_excluded": bool(exclude_home),
            "scale": "sous 70 : désaligné ; 70 à 85 : à corriger ; au-dessus de 85 : aligné par classe, vérifier les segments"}


@dataclass
class AnalysisConfig:
    alpha: float = 0.85
    utility_regex: str | None = DEFAULT_UTILITY_REGEX   # None = mots utilitaires par segment (is_utility)
    money_share: float = 0.80          # part cumulée de valeur qui définit les pages « money »
    starving_percentile: float = 40.0  # score en dessous duquel une money page est en famine
    overfed_percentile: float = 80.0   # score au-dessus duquel une page utilitaire est un gouffre
    top_n: int = 30
    class_rules: list | None = None    # [(nom, regex)] ; None = DEFAULT_CLASS_RULES
    class_mapping: dict | None = None  # {url: classe} explicite
    strip_query: bool = False          # même normalisation que le graphe pour joindre business, cibles et groupes


@dataclass
class Analysis:
    n: int
    segments: list[str]                 # nom du segment par nœud
    segment_names: list[str]
    segment_index: np.ndarray
    depth: np.ndarray                   # clics depuis l'accueil, -1 = inatteignable
    home: int | None
    in_deg: np.ndarray
    out_deg: np.ndarray
    in_weight: np.ndarray
    dangling: np.ndarray                # bool : aucune arête sortante
    leak_frac: np.ndarray
    utility: np.ndarray                 # bool
    strategic: np.ndarray               # bool
    value: np.ndarray                   # valeur business par nœud (0 si absente)
    value_col: str | None
    value_share: np.ndarray
    money: np.ndarray                   # bool : fait partie des pages qui cumulent money_share de la valeur
    starving: np.ndarray                # bool : money ou stratégique ET score sous le seuil
    overfed: np.ndarray                 # bool : utilitaire ET score au-dessus du seuil
    quadrant: np.ndarray                # 0 puits, 1 hub, 2 passoire, 3 noeud mort
    seg_table: list[dict]
    seg_flows: np.ndarray               # matrice segment × segment (masse de PR transportée)
    zone_flows: dict[str, float]        # masse transportée par position + fuites + culs-de-sac
    black_holes: list[int]              # culs-de-sac les mieux alimentés
    overlinked: list[int]               # pages utilitaires les plus puissantes
    starving_list: list[int]
    orphans: list[int]
    unreachable: list[int]
    cannibal_groups: list[dict]
    spearman: float | None
    thresholds: dict = field(default_factory=dict)
    position_counts: dict = field(default_factory=dict)
    uncrawled: np.ndarray | None = None
    thermal: dict | None = None         # déperdition thermique (composante sémantique), None sans w_ref
    demand: np.ndarray | None = None    # « demande » par nœud (impressions) quand la valeur retenue est autre
    demand_col: str | None = None
    demand_list: list[int] = field(default_factory=list)   # forte demande, PageRank faible, hors famine
    classes: list[str] = field(default_factory=list)       # classe business par nœud
    alignment: dict = field(default_factory=dict)          # score d'alignement et répartitions par classe
    actions: list[dict] = field(default_factory=list)      # plan d'action (rempli par actions.py)
    scenario: dict | None = None                           # avant / après des chantiers de gabarit (actions.build_scenario)


QUADRANT_NAMES = ["puits", "hub", "passoire", "noeud_mort"]
FILE_RX = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|csv|xml|json|txt|jpe?g|png|gif|webp|svg|mp4|mp3|ics)$", re.I)


def guess_home(urls: list[str]) -> int | None:
    best, best_len = None, 10**9
    for i, u in enumerate(urls):
        p = urlsplit(u)
        path = p.path or "/"
        if path in ("/", "/index.html", "/index.php", "/fr/", "/fr") and not p.query:
            if len(u) < best_len:
                best, best_len = i, len(u)
    if best is None and urls:
        best = int(np.argmin([len(u) for u in urls]))
    return best


LANG_RX = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.I)
MIN_SEGMENT_PAGES = 3


def default_segment(url: str) -> str:
    """Premier segment de chemin ; sous un préfixe de langue (fr, uk, en-us), le second segment
    est ajouté (fr/blog). Les segments trop petits sont regroupés ensuite par `compact_segments`."""
    p = urlsplit(url)
    parts = [s for s in p.path.split("/") if s]
    if not parts:
        return "accueil"
    seg = parts[0]
    if len(parts) == 1 and "." in seg and not seg.endswith((".html", ".htm", ".php")):
        return "fichiers"
    if LANG_RX.match(seg) and len(parts) > 1:
        return f"{seg.lower()}/{parts[1]}"
    return re.sub(r"\.(html?|php)$", "", seg) if len(parts) == 1 else seg


def compact_segments(urls: list[str], segments: list[str], min_pages: int = MIN_SEGMENT_PAGES) -> list[str]:
    """Regroupe les segments de moins de `min_pages` pages : pages de premier niveau → « racine »,
    sous un préfixe de langue → « <langue>/racine », sinon « autres »."""
    counts: dict[str, int] = {}
    for s in segments:
        counts[s] = counts.get(s, 0) + 1
    out = []
    for u, s in zip(urls, segments):
        if counts[s] >= min_pages or s in ("accueil", "fichiers"):
            out.append(s)
            continue
        parts = [x for x in urlsplit(u).path.split("/") if x]
        if "/" in s:
            lang = s.split("/")[0]
            out.append(f"{lang}/racine" if len(parts) <= 2 else f"{lang}/autres")
        else:
            out.append("racine" if len(parts) <= 1 else "autres")
    return out


def assign_segments(urls, given, rules, mapping) -> list[str]:
    out = []
    for u, g in zip(urls, given):
        if u in mapping:
            out.append(mapping[u]); continue
        if g:
            out.append(g); continue
        if rules:
            for name, rx in rules:
                if rx.search(u):
                    out.append(name); break
            else:
                out.append("autre")
            continue
        out.append(default_segment(u))
    if not rules and not mapping and len(urls) >= 200:
        out = compact_segments(urls, out, min_pages=max(3, round(0.002 * len(urls))))
    return out


def click_depth(graph: Graph, home: int | None) -> np.ndarray:
    depth = np.full(graph.n, -1, dtype=np.int32)
    if home is None or graph.n == 0:
        return depth
    adj = graph.adjacency_csr()
    depth[home] = 0
    frontier = np.array([home])
    d = 0
    while frontier.size:
        d += 1
        nxt = np.unique(adj[frontier].indices)
        nxt = nxt[depth[nxt] < 0]
        depth[nxt] = d
        frontier = nxt
    return depth


def analyse(graph: Graph, pr: np.ndarray, score: np.ndarray, chei: np.ndarray, cscore: np.ndarray,
            segments: list[str], business: dict[str, dict[str, float]] | None, value_col: str | None,
            strategic: dict[str, float] | None, groups: dict[str, str] | None, home: int | None,
            cfg: AnalysisConfig, w_ref: np.ndarray | None = None, demand_col: str | None = None) -> Analysis:
    """`w_ref` : poids des arêtes avant la pénalité sémantique. S'il est fourni, la déperdition
    thermique est mesurée : masse qui aurait circulé vers chaque segment sans la pénalité, contre
    masse effective, à PageRank stationnaire identique.
    `demand_col` : métrique business lue comme « demande » (impressions Search Console) quand la valeur
    retenue est une autre colonne (clics) : liste secondaire des pages à forte demande et PageRank faible."""
    n = graph.n
    urls = graph.urls
    seg_names = sorted(set(segments), key=lambda s: (s != "accueil", s))
    seg_idx_map = {s: i for i, s in enumerate(seg_names)}
    seg_index = np.array([seg_idx_map[s] for s in segments], dtype=np.int32)

    in_deg, out_deg, in_w = graph.in_degree(), graph.out_degree(), graph.in_weight()
    dangling = graph.dangling_mask()
    leak_frac = graph.leak_fraction()
    depth = click_depth(graph, home)

    utility = np.array([is_utility(u, cfg.utility_regex) for u in urls], dtype=bool)
    if home is not None:
        utility[home] = False

    strategic_mask = np.zeros(n, dtype=bool)
    if strategic:
        idx = {u: i for i, u in enumerate(urls)}
        for u in strategic:
            from .clean import normalize_url
            k = normalize_url(u, cfg.strip_query)
            if k in idx:
                strategic_mask[idx[k]] = True

    value = np.zeros(n, dtype=np.float64)
    demand = np.zeros(n, dtype=np.float64) if demand_col else None
    if business and value_col:
        idx = {u: i for i, u in enumerate(urls)}
        from .clean import normalize_url
        for u, metrics in business.items():
            k = normalize_url(u, cfg.strip_query)
            if k in idx:
                value[idx[k]] += float(metrics.get(value_col, 0.0))
                if demand is not None:
                    demand[idx[k]] += float(metrics.get(demand_col, 0.0))
    total_value = float(value.sum())
    value_share = value / total_value if total_value > 0 else np.zeros(n)

    money = np.zeros(n, dtype=bool)
    if total_value > 0:
        order = np.argsort(-value)
        cum = np.cumsum(value[order]) / total_value
        k = int(np.searchsorted(cum, cfg.money_share)) + 1
        money[order[:k]] = value[order[:k]] > 0

    starving_thr = float(np.percentile(score, cfg.starving_percentile)) if n else 0.0
    overfed_thr = float(np.percentile(score, cfg.overfed_percentile)) if n else 0.0
    starving = (money | strategic_mask) & (score < starving_thr)
    overfed = utility & (score >= overfed_thr)

    # Demande non servie : pages qui cumulent money_share de la demande, sous le seuil de famine,
    # et qui ne sont pas déjà en famine sur la valeur (sinon elles y figurent déjà).
    demand_list: list[int] = []
    if demand is not None and demand.sum() > 0:
        order_d = np.argsort(-demand)
        cum_d = np.cumsum(demand[order_d]) / demand.sum()
        k_d = int(np.searchsorted(cum_d, cfg.money_share)) + 1
        high_demand = np.zeros(n, dtype=bool)
        high_demand[order_d[:k_d]] = demand[order_d[:k_d]] > 0
        cand = high_demand & (score < starving_thr) & ~starving
        demand_list = sorted([int(i) for i in np.flatnonzero(cand)], key=lambda i: (-demand[i], score[i]))[: cfg.top_n * 2]

    med_pr, med_ch = float(np.median(score)), float(np.median(cscore))
    hi_pr, hi_ch = score >= med_pr, cscore >= med_ch
    quadrant = np.where(hi_pr & ~hi_ch, 0, np.where(hi_pr & hi_ch, 1, np.where(~hi_pr & hi_ch, 2, 3))).astype(np.int8)

    # Flux de PageRank par arête, agrégés par zone et par paire de segments
    flow = graph.edge_flow(pr, cfg.alpha)
    zone_flows = {p: 0.0 for p in POSITIONS}
    if graph.m:
        by_pos = np.bincount(graph.pos.astype(np.int64), weights=flow, minlength=len(POSITIONS))
        for i, p in enumerate(POSITIONS):
            zone_flows[p] = float(by_pos[i])
    zone_flows["fuites_et_culs_de_sac"] = float(cfg.alpha * np.dot(pr, leak_frac))
    zone_flows["teleportation"] = float(1.0 - cfg.alpha)
    position_counts = {p: int(c) for p, c in zip(POSITIONS, np.bincount(graph.pos.astype(np.int64), minlength=len(POSITIONS)))} if graph.m else {}

    S = len(seg_names)
    seg_flows = np.zeros((S, S))
    if graph.m:
        np.add.at(seg_flows, (seg_index[graph.src], seg_index[graph.dst]), flow)

    seg_table = []
    for s_i, name in enumerate(seg_names):
        mask = seg_index == s_i
        out_total = seg_flows[s_i].sum()
        internal = seg_flows[s_i, s_i]
        seg_table.append({
            "segment": name,
            "pages": int(mask.sum()),
            "pr_share": float(pr[mask].sum()),
            "value_share": float(value_share[mask].sum()),
            "score_mean": float(score[mask].mean()) if mask.any() else 0.0,
            "score_median": float(np.median(score[mask])) if mask.any() else 0.0,
            "leak_out_share": float(1 - internal / out_total) if out_total > 0 else 0.0,
            "inflow_external": float(seg_flows[:, s_i].sum() - internal),
            "starving": int((starving & mask).sum()),
            "overfed": int((overfed & mask).sum()),
            "dangling": int((dangling & mask).sum()),
        })
    for row in seg_table:
        row["gap"] = row["value_share"] - row["pr_share"]

    # Déperdition thermique : flux de référence (poids avant pénalité sémantique) contre flux effectif.
    thermal = None
    if w_ref is not None and graph.m:
        w_ref = np.asarray(w_ref, dtype=np.float64)
        s_ref = np.bincount(graph.src, weights=w_ref, minlength=n) + graph.leaked_out
        flow_ref = cfg.alpha * pr[graph.src] * w_ref / s_ref[graph.src]
        in_ref = np.bincount(seg_index[graph.dst], weights=flow_ref, minlength=S)
        in_eff = np.bincount(seg_index[graph.dst], weights=flow, minlength=S)
        cross = seg_index[graph.src] != seg_index[graph.dst]
        for s_i, row in enumerate(seg_table):
            row["thermal_in_ref"] = float(in_ref[s_i])
            row["thermal_in_eff"] = float(in_eff[s_i])
            row["thermal_loss"] = float(in_ref[s_i] - in_eff[s_i])
            row["thermal_loss_share"] = float((in_ref[s_i] - in_eff[s_i]) / in_ref[s_i]) if in_ref[s_i] > 0 else 0.0
        thermal = {
            "rerouted_mass": float(np.maximum(flow_ref - flow, 0.0).sum()),
            "rerouted_share": float(np.maximum(flow_ref - flow, 0.0).sum() / flow_ref.sum()) if flow_ref.sum() > 0 else 0.0,
            "cross_segment_ref": float(flow_ref[cross].sum()),
            "cross_segment_eff": float(flow[cross].sum()),
            "edges_penalised": int((graph.w < w_ref - 1e-12).sum()),
        }

    # Trous noirs : culs-de-sac réellement alimentés (au moins un lien entrant et un PageRank au-dessus de la
    # médiane) et qui sont des pages, pas des fichiers (un PDF ne peut pas recevoir de bloc de liens).
    is_file = np.array([bool(FILE_RX.search(urlsplit(u).path)) for u in urls], dtype=bool)
    med_pr_raw = float(np.median(pr)) if n else 0.0
    black_holes = [int(i) for i in np.argsort(-pr)
                   if dangling[i] and in_deg[i] > 0 and pr[i] >= med_pr_raw and not is_file[i]][: cfg.top_n]
    overlinked = [int(i) for i in np.argsort(-score) if utility[i]][: cfg.top_n]
    starving_list = sorted([int(i) for i in np.flatnonzero(starving)], key=lambda i: (-value[i], score[i]))[: cfg.top_n * 2]
    orphans = [int(i) for i in np.flatnonzero((in_deg == 0) & (np.arange(n) != (home if home is not None else -1)))]
    unreachable = [int(i) for i in np.flatnonzero(depth < 0)]

    cannibal_groups = []
    if groups:
        from .clean import normalize_url
        idx = {u: i for i, u in enumerate(urls)}
        by_group: dict[str, list[int]] = {}
        for u, g in groups.items():
            k = normalize_url(u, cfg.strip_query)
            if k in idx:
                by_group.setdefault(g, []).append(idx[k])
        for g, members in by_group.items():
            if len(members) < 2:
                continue
            members.sort(key=lambda i: -pr[i])
            lead, second = members[0], members[1]
            ratio = float(pr[lead] / pr[second]) if pr[second] > 0 else float("inf")
            cannibal_groups.append({"group": g, "pages": members, "leader": lead, "ratio": ratio,
                                    "decided": ratio >= 5.0})

    sp = None
    both = (value > 0)
    if both.sum() >= 5:
        r = spearmanr(value[both], score[both]).correlation
        sp = None if r is None or np.isnan(r) else float(r)

    classes = classify(urls, home, compile_class_rules(cfg.class_rules), cfg.class_mapping, strategic_mask)
    alignment = alignment_score(pr, classes, value_share if total_value > 0 else None)
    # Le démenti du score : le segment le plus sous-alimenté et la corrélation de rang, toujours affichés à côté.
    worst = max((r for r in seg_table if r["segment"] != "accueil"), key=lambda r: r["gap"], default=None)
    if worst is not None and total_value > 0:
        alignment["worst_segment"] = {"segment": worst["segment"], "pr_share": worst["pr_share"],
                                      "value_share": worst["value_share"], "gap": worst["gap"]}
    alignment["spearman"] = sp
    # Part de l'autorité reçue par les pages qui portent la valeur (celles qui cumulent `money_share` de la valeur
    # mesurée). C'est la seule mesure qui mérite le libellé « pages qui rapportent » : la part des classes money +
    # acquisition (`productive_share`) décrit des types d'adresses, pas la valeur.
    alignment["value_pages_pr_share"] = float(pr[money].sum()) if total_value > 0 else None
    alignment["value_pages_count"] = int(money.sum()) if total_value > 0 else 0
    alignment["value_pages_value_share"] = float(cfg.money_share) if total_value > 0 else None

    return Analysis(
        n=n, segments=segments, segment_names=seg_names, segment_index=seg_index, depth=depth, home=home,
        in_deg=in_deg, out_deg=out_deg, in_weight=in_w, dangling=dangling, leak_frac=leak_frac,
        utility=utility, strategic=strategic_mask, value=value, value_col=value_col, value_share=value_share,
        money=money, starving=starving, overfed=overfed, quadrant=quadrant, seg_table=seg_table,
        seg_flows=seg_flows, zone_flows=zone_flows, black_holes=black_holes, overlinked=overlinked,
        starving_list=starving_list, orphans=orphans, unreachable=unreachable, cannibal_groups=cannibal_groups,
        spearman=sp,
        thresholds={"starving_score": starving_thr, "overfed_score": overfed_thr, "median_score": med_pr,
                    "median_cscore": med_ch, "money_share": cfg.money_share},
        position_counts=position_counts,
        thermal=thermal,
        demand=demand, demand_col=demand_col if demand is not None else None, demand_list=demand_list,
        classes=classes, alignment=alignment,
    )
