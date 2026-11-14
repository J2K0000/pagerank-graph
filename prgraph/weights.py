# -*- coding: utf-8 -*-
"""Pondération des arêtes au-delà de la position : clics observés et composante sémantique.

Deux ajustements optionnels, appliqués après le nettoyage et avant le PageRank, dans cet
ordre : clics observés puis sémantique.

1. Clics observés (`--transitions FILE.csv`, `--data-blend`). Pour chaque page source v qui
   possède au moins une transition observée, le poids de l'arête (v, u) devient

       S(v) × [ blend × p_obs(v, u) + (1 − blend) × p_pos(v, u) ]

   où p_pos est la part heuristique (poids de position / somme des poids sortants de v),
   p_obs la part observée (transitions v→u / transitions sortantes de v) et S(v) la somme
   heuristique des poids sortants, conservée pour que la part de fuite de v (nofollow, 404)
   ne bouge pas. Une arête sans clic sur une page qui en a garde (1 − blend) × son poids
   heuristique : c'est le plancher qui permet de conclure « mal placé » plutôt que
   « inexistant ». Les pages sans aucune donnée gardent l'heuristique seule.
   blend = 1.0 est refusé : un lien jamais cliqué disparaîtrait du modèle et ne pourrait
   plus jamais être diagnostiqué (piège de circularité).

2. Composante sémantique (`--embeddings FILE.csv`, `--semantic-decay`). Le poids de chaque
   arête est multiplié par max(0, cos(source, cible)) ** decay, puis les poids sont
   renormalisés par source : la masse émise par une page ne change pas, sa répartition oui.
   Un lien hors sujet transmet donc moins et ses voisins thématiques récupèrent la
   différence. Une arête dont l'une des extrémités n'a pas de vecteur garde un facteur 1
   (pas d'information, pas de pénalité). Si toutes les arêtes d'une source tombent à 0, ses
   poids d'origine sont conservés. decay = 0 désactive la pénalité.
"""
from __future__ import annotations

import json

import numpy as np

from .clean import normalize_url
from .graph import Graph
from .loaders import _to_float, pick, read_header, read_table

TRANSITION_COLUMNS = ("transitions", "count", "clicks", "clics", "sessions", "passages", "n", "weight", "poids")


# ── Lecture ───────────────────────────────────────────────────────────────────

def read_transitions(path: str) -> dict[tuple[str, str], float]:
    """CSV source,target,transitions|count|clicks|sessions → {(source, cible): total}."""
    out: dict[tuple[str, str], float] = {}
    for row in read_table(path):
        s = pick(row, "source", "source_url", "from", "referrer", "page_referrer")
        t = pick(row, "target", "target_url", "to", "destination", "page", "page_location")
        if not s or not t:
            continue
        raw = pick(row, *TRANSITION_COLUMNS, default="1")
        v = _to_float(raw)
        if v <= 0:
            continue
        key = (s, t)
        out[key] = out.get(key, 0.0) + v
    return out


def read_embeddings(path: str) -> dict[str, np.ndarray]:
    """CSV `url` puis colonnes numériques, ou `url,vector` avec un vecteur JSON par ligne."""
    header = read_header(path)
    if not header:
        return {}
    url_col = next((h for h in ("url", "page", "address") if h in header), None)
    if url_col is None:
        raise ValueError("fichier d'embeddings : colonne url attendue en tête")
    vec_col = next((h for h in ("vector", "embedding", "vecteur") if h in header), None)
    out: dict[str, np.ndarray] = {}
    dim: int | None = None
    num_cols = [h for h in header if h != url_col] if vec_col is None else []
    for row in read_table(path):
        u = row.get(url_col, "")
        if not u:
            continue
        if vec_col is not None:
            raw = row.get(vec_col, "").strip()
            if not raw:
                continue
            try:
                vec = np.asarray(json.loads(raw), dtype=np.float64).ravel()
            except (ValueError, TypeError) as exc:
                raise ValueError(f"vecteur JSON invalide pour {u} : {exc}") from exc
        else:
            vec = np.asarray([_to_float(row.get(c, "")) for c in num_cols], dtype=np.float64)
        if dim is None:
            dim = int(vec.size)
        elif vec.size != dim:
            raise ValueError(f"dimension incohérente pour {u} : {vec.size} au lieu de {dim}")
        out[u] = vec
    return out


# ── Clics observés ────────────────────────────────────────────────────────────

def apply_transitions(graph: Graph, observed: dict[tuple[str, str], float], blend: float = 0.7,
                      strip_query: bool = False) -> tuple[Graph, dict]:
    """Mélange part observée / part heuristique, normalisé par page source."""
    if not (0.0 <= blend < 1.0):
        raise ValueError(
            f"--data-blend {blend} refusé : la part de donnée doit être dans [0, 1[. À 1.0 un lien jamais cliqué "
            "tombe à zéro et disparaît du modèle (piège de circularité) ; garder un plancher heuristique, 0.7 par défaut.")
    n, m = graph.n, graph.m
    idx = {u: i for i, u in enumerate(graph.urls)}
    edge_index: dict[int, int] = {}
    if m:
        keys = graph.src.astype(np.int64) * n + graph.dst.astype(np.int64)
        edge_index = {int(k): j for j, k in enumerate(keys)}
    obs = np.zeros(m, dtype=np.float64)
    matched = unmatched = 0
    total_obs = matched_obs = 0.0
    for (s, t), v in observed.items():
        total_obs += v
        si, ti = idx.get(normalize_url(s, strip_query)), idx.get(normalize_url(t, strip_query))
        j = edge_index.get(si * n + ti) if si is not None and ti is not None else None
        if j is None:
            unmatched += 1
            continue
        matched += 1
        matched_obs += v
        obs[j] += v

    w = graph.w.astype(np.float64)
    s_edges = np.bincount(graph.src, weights=w, minlength=n)
    s_obs = np.bincount(graph.src, weights=obs, minlength=n)
    has_data = s_obs[graph.src] > 0
    new_w = w.copy()
    if has_data.any():
        src_h = graph.src[has_data]
        p_pos = w[has_data] / s_edges[src_h]
        p_obs = obs[has_data] / s_obs[src_h]
        new_w[has_data] = s_edges[src_h] * (blend * p_obs + (1.0 - blend) * p_pos)
    covered = obs > 0
    total_w = float(w.sum())
    sources_total = int((np.bincount(graph.src, minlength=n) > 0).sum())
    stats = {
        "blend": blend,
        "edges_total": int(m),
        "edges_covered": int(covered.sum()),
        "edges_covered_share": float(covered.sum() / m) if m else 0.0,
        "mass_covered_share": float(w[covered].sum() / total_w) if total_w > 0 else 0.0,
        "sources_total": sources_total,
        "sources_with_data": int((s_obs > 0).sum()),
        "sources_with_data_share": float((s_obs > 0).sum() / sources_total) if sources_total else 0.0,
        "edges_floored": int((has_data & ~covered).sum()),
        "pairs_read": len(observed),
        "pairs_matched": matched,
        "pairs_unmatched": unmatched,
        "transitions_total": total_obs,
        "transitions_matched_share": float(matched_obs / total_obs) if total_obs > 0 else 0.0,
    }
    return graph.with_edges(graph.src, graph.dst, new_w, graph.pos), stats


# ── Composante sémantique ─────────────────────────────────────────────────────

def apply_semantic(graph: Graph, embeddings: dict[str, np.ndarray], decay: float = 0.5,
                   strip_query: bool = False) -> tuple[Graph, dict, np.ndarray]:
    """Facteur max(0, cos) ** decay par arête, puis renormalisation par source.
    Retourne (graphe, statistiques, facteur par arête)."""
    if decay < 0:
        raise ValueError(f"--semantic-decay {decay} refusé : l'exposant doit être positif ou nul (0 = pas de pénalité).")
    n, m = graph.n, graph.m
    idx = {u: i for i, u in enumerate(graph.urls)}
    dim = next((int(v.size) for v in embeddings.values()), 0)
    E = np.zeros((n, max(dim, 1)), dtype=np.float64)
    has_vec = np.zeros(n, dtype=bool)
    for u, vec in embeddings.items():
        i = idx.get(normalize_url(u, strip_query))
        if i is None or vec.size != dim:
            continue
        E[i] = vec
        has_vec[i] = True
    norms = np.linalg.norm(E, axis=1)
    nz = norms > 0
    E[nz] /= norms[nz][:, None]
    has_vec &= nz

    factor = np.ones(m, dtype=np.float64)
    scored = np.zeros(m, dtype=bool)
    if m:
        scored = has_vec[graph.src] & has_vec[graph.dst]
        cos = np.einsum("ij,ij->i", E[graph.src[scored]], E[graph.dst[scored]])
        cos = np.clip(cos, 0.0, 1.0)
        factor[scored] = 1.0 if decay == 0 else np.power(cos, decay)

    w = graph.w.astype(np.float64)
    penalised = w * factor
    s_old = np.bincount(graph.src, weights=w, minlength=n)
    s_new = np.bincount(graph.src, weights=penalised, minlength=n)
    all_zero = (s_new <= 0) & (s_old > 0)
    scale = np.ones(n, dtype=np.float64)
    ok = s_new > 0
    scale[ok] = s_old[ok] / s_new[ok]
    new_w = penalised * scale[graph.src]
    keep = all_zero[graph.src]
    new_w[keep] = w[keep]
    factor[keep] = 1.0

    stats = {
        "decay": decay,
        "dimension": dim,
        "nodes_total": int(n),
        "nodes_embedded": int(has_vec.sum()),
        "nodes_embedded_share": float(has_vec.sum() / n) if n else 0.0,
        "edges_total": int(m),
        "edges_scored": int(scored.sum()),
        "edges_scored_share": float(scored.sum() / m) if m else 0.0,
        "edges_zeroed": int((scored & (factor <= 0)).sum() + (scored & keep).sum()),
        "factor_mean": float(factor[scored].mean()) if scored.any() else 1.0,
        "sources_all_zero": int(all_zero.sum()),
        "vectors_unmatched": int(len(embeddings) - has_vec.sum()) if len(embeddings) >= has_vec.sum() else 0,
    }
    return graph.with_edges(graph.src, graph.dst, new_w, graph.pos), stats, factor
