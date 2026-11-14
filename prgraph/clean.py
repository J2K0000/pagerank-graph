# -*- coding: utf-8 -*-
"""Nettoyage du graphe avant tout calcul.

1. Normalisation des URL (fragment, casse du schéma et de l'hôte, port par défaut).
2. Résolution des chaînes de redirection puis des canoniques : le PageRank ne se calcule
   que sur les URL canoniques finales en 200.
3. Fuites : lien vers une 4xx/5xx = équité perdue ; lien nofollow = jus jeté. Dans les deux
   cas le poids reste au dénominateur de la page source mais ne transmet rien.
4. Fusion des multi-arêtes : une seule connexion A→B, pondérée par l'emplacement le plus
   visible (poids maximal), ancre du premier lien rencontré.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import numpy as np

from .loaders import POSITIONS, UrlInfo

MAX_HOPS = 20


def normalize_url(url: str, strip_query: bool = False) -> str:
    u = url.strip()
    if not u:
        return u
    try:
        parts = urlsplit(u)
    except ValueError:
        return u
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    path = parts.path or ("/" if netloc else "")
    query = "" if strip_query else parts.query
    return urlunsplit((scheme, netloc, path, query, ""))


class Resolver:
    """Suit 3xx puis canonical jusqu'au point fixe. `final(u)` renvoie l'URL finale ou None
    si l'équité est perdue (4xx, 5xx, boucle, redirection sans cible)."""

    def __init__(self, table: dict[str, UrlInfo], strip_query: bool = False):
        self.table = table
        self.strip_query = strip_query
        self._cache: dict[str, str | None] = {}
        self.chains = 0          # nombre d'URL dont la résolution a traversé au moins un saut
        self.loops = 0

    def _step(self, u: str) -> tuple[str | None, bool]:
        """Un saut. Retourne (url_suivante, terminal)."""
        info = self.table.get(u)
        if info is None:
            return u, True                       # inconnu : supposé 200 (pas de table ou hors crawl)
        st = info.status
        if st is not None and 300 <= st < 400:
            if info.redirect:
                return normalize_url(info.redirect, self.strip_query), False
            return None, True                    # redirection sans cible : perdu
        if st is not None and (st >= 400 or st < 200):
            return None, True
        if info.canonical:
            c = normalize_url(info.canonical, self.strip_query)
            if c and c != u:
                cinfo = self.table.get(c)
                if cinfo is None or cinfo.status is None or 200 <= cinfo.status < 400:
                    return c, False
        return u, True

    def final(self, url: str) -> str | None:
        u = normalize_url(url, self.strip_query)
        if u in self._cache:
            return self._cache[u]
        seen = [u]
        cur = u
        res: str | None = None
        for _ in range(MAX_HOPS):
            nxt, terminal = self._step(cur)
            if terminal:
                res = nxt
                break
            if nxt in seen:
                self.loops += 1
                res = None
                break
            seen.append(nxt)
            cur = nxt
        if len(seen) > 1:
            self.chains += 1
        for s in seen:
            self._cache[s] = res
        return res

    def is_ok(self, final_url: str) -> bool:
        info = self.table.get(final_url)
        return info is None or info.status is None or 200 <= info.status < 300


@dataclass
class CleanGraph:
    urls: list[str]
    src: np.ndarray                       # int32, id source
    dst: np.ndarray                       # int32, id cible
    w: np.ndarray                         # float64, poids de l'arête fusionnée
    pos: np.ndarray                       # int8, index dans POSITIONS
    anchors: list[str]                    # ancre du premier lien rencontré, par arête
    leaked_out: np.ndarray                # float64 par nœud : poids des liens nofollow / 404
    titles: list[str]
    given_segments: list[str | None]
    stats: dict = field(default_factory=dict)
    uncrawled: np.ndarray | None = None   # bool : URL découverte mais absente de la table (non crawlée)

    @property
    def n(self) -> int:
        return len(self.urls)


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def same_site(host: str, site_host: str) -> bool:
    """Même site : hôte identique, ou sous-domaine de l'autre (www.exemple.fr, blog.exemple.fr, exemple.fr).
    Un hôte vide (URL relative) est considéré interne."""
    if not host or not site_host:
        return True
    a, b = host.split(":")[0], site_host.split(":")[0]
    a = a[4:] if a.startswith("www.") else a
    b = b[4:] if b.startswith("www.") else b
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def clean(edges, table: dict[str, UrlInfo], weights: dict[str, float],
          strip_query: bool = False, include_table_nodes: bool = True,
          boilerplate_share: float = 0.6, boilerplate_min_sources: int = 20,
          boilerplate_min_links: int = 200, protect=None, site_host: str | None = None,
          uncrawled: str = "leak") -> CleanGraph:
    """`boilerplate_share` : une cible liée en position content/unknown depuis au moins cette part
    des pages émettrices (et au moins `boilerplate_min_sources` pages) est requalifiée en lien de
    gabarit (logo, bandeau de clients, méga-menu en <div>). Une cible liée en content/unknown depuis
    au moins `boilerplate_min_links` pages est requalifiée quelle que soit la part (menus par langue).
    0 désactive l'inférence. La requalification est graduelle : le poids devient
    max(poids gabarit, poids d'origine × (1 − part des pages émettrices)), et les cibles pour lesquelles
    `protect(url)` est vrai (pages stratégiques, pages money) ne sont jamais requalifiées. La liste des
    cibles touchées est dans `stats["boilerplate_target_list"]`.

    `site_host` : hôte du site ; un lien dont la cible finale (après 301 / canonical) est sur un autre
    hôte est une fuite (`stats["lost_external"]`), jamais un nœud. Déduit de la première source si absent.

    `uncrawled` : sort des cibles absentes de la table d'URL quand une table existe : "leak" (défaut),
    l'équité part vers une page dont on ne sait rien ; "nodes", l'ancien comportement (nœud sans sortie
    supposé en 200, marqué `uncrawled`)."""
    if uncrawled not in ("leak", "nodes"):
        raise ValueError(f"uncrawled : « {uncrawled} » inconnu (leak ou nodes)")
    resolver = Resolver(table, strip_query)
    url_id: dict[str, int] = {}
    urls: list[str] = []
    site_host = (site_host or "").lower() or None

    def nid(u: str) -> int:
        i = url_id.get(u)
        if i is None:
            i = len(urls)
            url_id[u] = i
            urls.append(u)
        return i

    # Inventaire : toute URL canonique finale en 200 de la table est un nœud, même sans lien entrant.
    if include_table_nodes:
        for u in list(table):
            f = resolver.final(u)
            if f is not None and f == normalize_url(u, strip_query) and resolver.is_ok(f):
                nid(f)

    pos_index = {p: i for i, p in enumerate(POSITIONS)}
    S: list[int] = []
    D: list[int] = []
    W: list[float] = []
    P: list[int] = []
    A: list[str] = []
    leaked: dict[int, float] = {}
    st = {"edges_in": 0, "dropped_source_not_canonical": 0, "lost_404": 0, "nofollow": 0,
          "self_loops": 0, "redirect_or_canonical_rewrites": 0, "external_or_empty": 0,
          "duplicates_collapsed": 0, "lost_external": 0, "lost_uncrawled": 0, "uncrawled_policy": uncrawled}
    has_table = bool(table)

    for e in edges:
        st["edges_in"] += 1
        if not e.source or not e.target:
            st["external_or_empty"] += 1
            continue
        s_norm = normalize_url(e.source, strip_query)
        if site_host is None:
            site_host = _host(s_norm) or None
        s_final = resolver.final(s_norm)
        if s_final is None or s_final != s_norm or not resolver.is_ok(s_final):
            # Une URL redirigée ou non canonique n'émet pas de liens propres.
            st["dropped_source_not_canonical"] += 1
            continue
        sid = nid(s_final)
        w = e.weight if e.weight is not None else weights.get(e.position, weights["unknown"])
        if w <= 0:
            continue
        t_norm = normalize_url(e.target, strip_query)
        if e.target_status is not None and t_norm not in table:
            table[t_norm] = UrlInfo(url=t_norm, status=e.target_status)
        t_final = resolver.final(t_norm)
        if t_final is None or not resolver.is_ok(t_final):
            st["lost_404"] += 1
            leaked[sid] = leaked.get(sid, 0.0) + w
            continue
        if site_host and not same_site(_host(t_final), site_host):
            # La cible finale (après 301 / canonical) est sur un autre hôte : l'équité quitte le site.
            st["lost_external"] += 1
            leaked[sid] = leaked.get(sid, 0.0) + w
            continue
        if has_table and uncrawled == "leak" and t_final not in table:
            # Cible découverte mais jamais récupérée (budget de pages) : on ne sait rien d'elle, fuite.
            # Décidé sur la table seule, jamais sur l'ordre des lignes du fichier.
            st["lost_uncrawled"] += 1
            leaked[sid] = leaked.get(sid, 0.0) + w
            continue
        if t_final != t_norm:
            st["redirect_or_canonical_rewrites"] += 1
        if e.nofollow:
            st["nofollow"] += 1
            leaked[sid] = leaked.get(sid, 0.0) + w
            continue
        tid = nid(t_final)
        if tid == sid:
            st["self_loops"] += 1
            continue
        S.append(sid)
        D.append(tid)
        W.append(w)
        P.append(pos_index.get(e.position, pos_index["unknown"]))
        A.append(e.anchor)

    n = len(urls)
    src = np.asarray(S, dtype=np.int64)
    dst = np.asarray(D, dtype=np.int64)
    w_arr = np.asarray(W, dtype=np.float64)
    p_arr = np.asarray(P, dtype=np.int8)

    # Fusion des multi-arêtes : clé (src, dst), poids max, position du poids max, première ancre.
    if len(src):
        key = src * n + dst
        order = np.lexsort((-w_arr, key))      # tri par clé, puis poids décroissant
        key_sorted = key[order]
        first_of_group = np.ones(len(key_sorted), dtype=bool)
        first_of_group[1:] = key_sorted[1:] != key_sorted[:-1]
        keep = order[first_of_group]           # arête de poids max par paire, triée par clé
        # Première ancre rencontrée dans le fichier par paire (même ordre de clés)
        order_app = np.lexsort((np.arange(len(key)), key))
        ks = key[order_app]
        fg = np.ones(len(ks), dtype=bool)
        fg[1:] = ks[1:] != ks[:-1]
        first_app = order_app[fg]
        st["duplicates_collapsed"] = int(len(src) - len(keep))
        src_c, dst_c, w_c, p_c = src[keep], dst[keep], w_arr[keep], p_arr[keep]
        anchors = [A[i] for i in first_app]
    else:
        src_c, dst_c, w_c, p_c, anchors = src, dst, w_arr, p_arr, []

    # Inférence du gabarit : cibles liées depuis la quasi-totalité des pages émettrices
    st["boilerplate_reclassified"] = 0
    st["boilerplate_targets"] = 0
    st["boilerplate_protected"] = 0
    st["boilerplate_target_list"] = []
    st["boilerplate_threshold"] = 0
    if boilerplate_share and len(src_c):
        n_sources = int(len(np.unique(src_c)))
        threshold = max(boilerplate_min_sources, int(np.ceil(boilerplate_share * n_sources)))
        if boilerplate_min_links:
            threshold = min(threshold, max(boilerplate_min_sources, boilerplate_min_links))
        st["boilerplate_threshold"] = int(threshold)
        # Seuls les liens déclarés content / unknown comptent : une page de menu liée partout en header et
        # trois fois dans un texte garde ses trois liens éditoriaux à plein poids.
        editorial = np.isin(p_c, [pos_index["content"], pos_index["unknown"]])
        per_target = np.bincount(dst_c[editorial], minlength=n)
        hot = per_target >= threshold
        if hot.any() and protect is not None:
            prot = np.array([bool(protect(urls[i])) for i in range(n)], dtype=bool)
            st["boilerplate_protected"] = int((hot & prot).sum())
            hot &= ~prot
        if hot.any():
            mask = hot[dst_c] & editorial
            if mask.any():
                p_c = p_c.copy()
                w_c = w_c.copy()
                w_boiler = float(weights.get("boilerplate", weights["unknown"]))
                # Graduel : plus la cible est citée par une grande part des pages, plus elle ressemble à un
                # bloc répété ; à 100 % des pages elle vaut le gabarit, à la part seuil elle en est encore loin.
                share = per_target[dst_c[mask]] / max(n_sources, 1)
                w_c[mask] = np.maximum(w_boiler, w_c[mask] * (1.0 - share))
                p_c[mask] = pos_index["boilerplate"]
                st["boilerplate_reclassified"] = int(mask.sum())
                touched = np.unique(dst_c[mask])
                st["boilerplate_targets"] = int(touched.size)
                counts = np.bincount(dst_c[mask], minlength=n)
                order = sorted((int(i) for i in touched), key=lambda i: -int(counts[i]))
                st["boilerplate_target_list"] = [(urls[i], int(counts[i]), round(float(counts[i] / max(n_sources, 1)), 3))
                                                 for i in order[:50]]

    leaked_out = np.zeros(n, dtype=np.float64)
    for i, v in leaked.items():
        leaked_out[i] = v

    titles = [table[u].title if u in table else "" for u in urls]
    uncrawled_mask = np.array([bool(table) and u not in table for u in urls], dtype=bool)
    given_segments = [table[u].segment if u in table else None for u in urls]
    st.update({"nodes": n, "edges_out": int(len(src_c)), "redirect_chains_resolved": resolver.chains,
               "redirect_loops": resolver.loops, "uncrawled_nodes": int(uncrawled_mask.sum()), "site_host": site_host})
    return CleanGraph(urls=urls, src=src_c.astype(np.int32), dst=dst_c.astype(np.int32), w=w_c,
                      pos=p_c, anchors=anchors, leaked_out=leaked_out, titles=titles,
                      given_segments=given_segments, stats=st, uncrawled=uncrawled_mask)
