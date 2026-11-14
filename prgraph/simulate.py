# -*- coding: utf-8 -*-
"""Jumeau numérique : modifier le graphe par règles et comparer la distribution avant / après."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .clean import normalize_url
from .graph import Graph
from .loaders import POSITIONS


@dataclass
class Rules:
    remove_positions: list[str] = field(default_factory=list)      # ex. ["footer", "nav"]
    remove_positions_target: str | None = None                      # regex : ne retirer que vers ces cibles
    remove_target_regex: str | None = None
    remove_source_regex: str | None = None
    remove_edges: list[tuple[str, str]] = field(default_factory=list)
    add_edges: list[tuple[str, str, str]] = field(default_factory=list)   # (source, target, position)
    cap_position: tuple[str, int] | None = None                     # ("nav", 30) : garder au plus N liens nav par page
    move_positions: list[str] = field(default_factory=list)        # ex. ["header", "nav", "boilerplate"] : déplacer...
    move_positions_target: str | None = None                        # ...les liens vers ces cibles (regex)...
    move_to: str = "footer"                                         # ...vers cette position (le lien reste, son poids change)

    def describe(self) -> list[str]:
        out = []
        if self.remove_positions:
            s = f"retrait des liens en position {', '.join(self.remove_positions)}"
            if self.remove_positions_target:
                s += f" vers les cibles /{self.remove_positions_target}/"
            out.append(s)
        if self.move_positions:
            s = f"déplacement des liens en position {', '.join(self.move_positions)} vers la position {self.move_to}"
            if self.move_positions_target:
                s += f" (cibles /{self.move_positions_target}/)"
            out.append(s)
        if self.remove_target_regex:
            out.append(f"retrait des liens vers /{self.remove_target_regex}/")
        if self.remove_source_regex:
            out.append(f"retrait des liens émis par /{self.remove_source_regex}/")
        if self.remove_edges:
            out.append(f"retrait de {len(self.remove_edges)} lien(s) nommé(s)")
        if self.add_edges:
            out.append(f"ajout de {len(self.add_edges)} lien(s)")
        if self.cap_position:
            out.append(f"plafond de {self.cap_position[1]} liens {self.cap_position[0]} par page")
        return out or ["aucune modification"]


def apply_rules(graph: Graph, rules: Rules, weights: dict[str, float]) -> tuple[Graph, dict]:
    n = graph.n
    urls = graph.urls
    idx = {u: i for i, u in enumerate(urls)}
    pos_index = {p: i for i, p in enumerate(POSITIONS)}
    keep = np.ones(graph.m, dtype=bool)
    stats = {"removed": 0, "added": 0, "added_unknown_url": 0, "upgraded": 0, "moved": 0}
    w_all = graph.w.astype(np.float64).copy()
    pos_all = graph.pos.copy()

    if rules.move_positions and rules.move_to in pos_index:
        # Le lien reste (une mention légale doit rester accessible) ; seule sa position, donc son poids, change.
        pos_ids = [pos_index[p] for p in rules.move_positions if p in pos_index]
        mask = np.isin(graph.pos, pos_ids)
        if rules.move_positions_target:
            rx = re.compile(rules.move_positions_target, re.I)
            tgt_match = np.array([bool(rx.search(u)) for u in urls])
            mask &= tgt_match[graph.dst]
        new_pid = pos_index[rules.move_to]
        new_w = float(weights.get(rules.move_to, weights["unknown"]))
        mask &= (pos_all != new_pid) | (w_all != new_w)
        pos_all[mask] = new_pid
        w_all[mask] = new_w
        stats["moved"] = int(mask.sum())

    if rules.remove_positions:
        pos_ids = [pos_index[p] for p in rules.remove_positions if p in pos_index]
        mask = np.isin(graph.pos, pos_ids)
        if rules.remove_positions_target:
            rx = re.compile(rules.remove_positions_target, re.I)
            tgt_match = np.array([bool(rx.search(u)) for u in urls])
            mask &= tgt_match[graph.dst]
        keep &= ~mask
    if rules.remove_target_regex:
        rx = re.compile(rules.remove_target_regex, re.I)
        m = np.array([bool(rx.search(u)) for u in urls])
        keep &= ~m[graph.dst]
    if rules.remove_source_regex:
        rx = re.compile(rules.remove_source_regex, re.I)
        m = np.array([bool(rx.search(u)) for u in urls])
        keep &= ~m[graph.src]
    if rules.remove_edges:
        pairs = set()
        for s, t in rules.remove_edges:
            si, ti = idx.get(normalize_url(s)), idx.get(normalize_url(t))
            if si is not None and ti is not None:
                pairs.add(si * n + ti)
        if pairs:
            key = graph.src.astype(np.int64) * n + graph.dst
            keep &= ~np.isin(key, np.fromiter(pairs, dtype=np.int64))
    if rules.cap_position:
        pname, cap = rules.cap_position
        pid = pos_index.get(pname)
        if pid is not None and cap >= 0:
            is_pos = graph.pos == pid
            order = np.lexsort((np.arange(graph.m), graph.src))
            rank = np.zeros(graph.m, dtype=np.int64)
            # rang de chaque arête de cette position parmi celles de la même source
            src_sorted = graph.src[order]
            pos_sorted = is_pos[order]
            counter = {}
            for k, e in enumerate(order):
                if not pos_sorted[k]:
                    continue
                s = int(src_sorted[k])
                counter[s] = counter.get(s, 0) + 1
                rank[e] = counter[s]
            keep &= ~(is_pos & (rank > cap))

    stats["removed"] = int((~keep).sum())
    src, dst, w, pos = graph.src[keep], graph.dst[keep], w_all[keep], pos_all[keep]

    if rules.add_edges:
        S, D, W_, P = [], [], [], []
        for s, t, p in rules.add_edges:
            si, ti = idx.get(normalize_url(s)), idx.get(normalize_url(t))
            if si is None or ti is None or si == ti:
                stats["added_unknown_url"] += 1
                continue
            S.append(si); D.append(ti); W_.append(weights.get(p, weights["unknown"])); P.append(pos_index.get(p, pos_index["unknown"]))
        if S:
            src = np.concatenate([src, np.asarray(S, dtype=np.int32)])
            dst = np.concatenate([dst, np.asarray(D, dtype=np.int32)])
            w = np.concatenate([w, np.asarray(W_)])
            pos = np.concatenate([pos, np.asarray(P, dtype=np.int8)])
            # Fusion : une seule arête par paire, poids max
            key = src.astype(np.int64) * n + dst
            order = np.lexsort((-w, key))
            ks = key[order]
            first = np.ones(len(ks), dtype=bool)
            first[1:] = ks[1:] != ks[:-1]
            sel = order[first]
            stats["added"] = int(len(sel) - keep.sum())
            # Liens déjà présents dont le poids augmente (ex. lien de menu doublé d'un lien dans le texte)
            n_before = int(keep.sum())
            key_before = src[:n_before].astype(np.int64) * n + dst[:n_before]
            w_before = dict(zip(key_before.tolist(), w[:n_before].tolist()))
            upgraded = 0
            for kk, ww in zip(key[sel].tolist(), w[sel].tolist()):
                if kk in w_before and ww > w_before[kk] + 1e-12:
                    upgraded += 1
            stats["upgraded"] = upgraded
            src, dst, w, pos = src[sel], dst[sel], w[sel], pos[sel]

    return graph.with_edges(src, dst, w, pos), stats


def diff_table(urls: list[str], before: np.ndarray, after: np.ndarray, score_b: np.ndarray,
               score_a: np.ndarray, top: int = 30) -> dict:
    delta = after - before
    rel = np.where(before > 0, delta / before, 0.0)
    gainers = [int(i) for i in np.argsort(-rel)[:top] if rel[i] > 0]
    losers = [int(i) for i in np.argsort(rel)[:top] if rel[i] < 0]
    return {"delta": delta, "rel": rel, "gainers": gainers, "losers": losers,
            "score_delta": score_a - score_b}
