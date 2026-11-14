# -*- coding: utf-8 -*-
"""Représentation creuse du graphe : matrice CSR et opérations vectorisées."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy import sparse

from .clean import CleanGraph


@dataclass
class Graph:
    urls: list[str]
    src: np.ndarray
    dst: np.ndarray
    w: np.ndarray
    pos: np.ndarray
    leaked_out: np.ndarray

    @classmethod
    def from_clean(cls, cg: CleanGraph) -> "Graph":
        return cls(urls=cg.urls, src=cg.src, dst=cg.dst, w=cg.w, pos=cg.pos, leaked_out=cg.leaked_out)

    @property
    def n(self) -> int:
        return len(self.urls)

    @property
    def m(self) -> int:
        return int(len(self.src))

    def out_sum(self) -> np.ndarray:
        """Somme des poids sortants, fuites comprises (dénominateur du surfeur raisonnable)."""
        s = np.bincount(self.src, weights=self.w, minlength=self.n).astype(np.float64)
        return s + self.leaked_out

    def leak_fraction(self) -> np.ndarray:
        """Part de la masse de chaque nœud qui ne suit aucune arête : 1 pour un cul-de-sac."""
        s = self.out_sum()
        frac = np.ones(self.n, dtype=np.float64)
        nz = s > 0
        frac[nz] = self.leaked_out[nz] / s[nz]
        return frac

    def transition_transposed(self) -> sparse.csr_matrix:
        """M^T avec M[v,u] = w(v,u)/S(v) : x_{k+1} = M^T x_k."""
        s = self.out_sum()
        data = self.w / np.where(s > 0, s, 1.0)[self.src]      # arête de poids nul : ne transmet rien
        mt = sparse.csr_matrix((data, (self.dst, self.src)), shape=(self.n, self.n), dtype=np.float64)
        mt.sum_duplicates()
        return mt

    def reversed(self) -> "Graph":
        """Graphe inversé pour le CheiRank : mêmes poids, flèches retournées, sans fuites."""
        return Graph(urls=self.urls, src=self.dst.copy(), dst=self.src.copy(), w=self.w.copy(),
                     pos=self.pos.copy(), leaked_out=np.zeros(self.n))

    def in_degree(self) -> np.ndarray:
        return np.bincount(self.dst, minlength=self.n)

    def out_degree(self) -> np.ndarray:
        return np.bincount(self.src, minlength=self.n)

    def in_weight(self) -> np.ndarray:
        return np.bincount(self.dst, weights=self.w, minlength=self.n)

    def edge_flow(self, pr: np.ndarray, alpha: float = 0.85) -> np.ndarray:
        """Masse de PageRank transportée par chaque arête à l'état stationnaire."""
        s = self.out_sum()
        return alpha * pr[self.src] * self.w / np.where(s > 0, s, 1.0)[self.src]

    def dangling_mask(self) -> np.ndarray:
        return np.bincount(self.src, minlength=self.n) == 0

    def adjacency_csr(self) -> sparse.csr_matrix:
        return sparse.csr_matrix((np.ones(self.m, dtype=np.int8), (self.src, self.dst)), shape=(self.n, self.n))

    def with_edges(self, src, dst, w, pos) -> "Graph":
        return replace(self, src=np.asarray(src, dtype=np.int32), dst=np.asarray(dst, dtype=np.int32),
                       w=np.asarray(w, dtype=np.float64), pos=np.asarray(pos, dtype=np.int8))
