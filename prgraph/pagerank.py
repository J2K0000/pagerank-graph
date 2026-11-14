# -*- coding: utf-8 -*-
"""Puissance itérée sur matrice creuse, culs-de-sac, téléportation biaisée, CheiRank, score log."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .graph import Graph


@dataclass
class PRResult:
    pr: np.ndarray
    iterations: int
    converged: bool
    residual: float
    dangling_mass: float      # masse réinjectée à la dernière itération (culs-de-sac + fuites)


def power_iteration(graph: Graph, alpha: float = 0.85, teleport: np.ndarray | None = None,
                    dangling: str = "uniform", home: int | None = None,
                    tol: float = 1e-9, max_iter: int = 200) -> PRResult:
    n = graph.n
    if n == 0:
        return PRResult(np.zeros(0), 0, True, 0.0, 0.0)
    if teleport is None:
        t = np.full(n, 1.0 / n)
    else:
        t = np.asarray(teleport, dtype=np.float64)
        if t.sum() <= 0:
            t = np.full(n, 1.0 / n)
        t = t / t.sum()
    if dangling == "uniform":
        r = np.full(n, 1.0 / n)
    elif dangling == "teleport":
        r = t
    elif dangling == "home":
        if home is None:
            raise ValueError("dangling='home' exige l'index de la page d'accueil")
        r = np.zeros(n)
        r[home] = 1.0
    else:
        raise ValueError(f"politique de culs-de-sac inconnue : {dangling}")

    mt = graph.transition_transposed()
    leak = graph.leak_fraction()
    x = np.full(n, 1.0 / n)
    converged, it, resid, m = False, 0, 0.0, 0.0
    for it in range(1, max_iter + 1):
        m = float(np.dot(x, leak))
        x_new = alpha * (mt @ x + m * r) + (1.0 - alpha) * t
        s = x_new.sum()
        if s > 0:
            x_new /= s
        resid = float(np.abs(x_new - x).sum())
        x = x_new
        if resid < tol:
            converged = True
            break
    return PRResult(pr=x, iterations=it, converged=converged, residual=resid, dangling_mass=alpha * m)


def cheirank(graph: Graph, alpha: float = 0.85, tol: float = 1e-9, max_iter: int = 200) -> PRResult:
    """PageRank du graphe inversé : capacité d'une page à irriguer le reste du site."""
    return power_iteration(graph.reversed(), alpha=alpha, dangling="uniform", tol=tol, max_iter=max_iter)


def log_score(pr: np.ndarray) -> np.ndarray:
    """Échelle 0-100 logarithmique : (ln PR - ln PRmin) / (ln PRmax - ln PRmin) * 100."""
    pr = np.asarray(pr, dtype=np.float64)
    if pr.size == 0:
        return pr
    positive = pr[pr > 0]
    if positive.size == 0:
        return np.zeros_like(pr)
    lo, hi = float(positive.min()), float(positive.max())
    out = np.zeros_like(pr)
    if hi <= lo:
        out[pr > 0] = 100.0
        return out
    mask = pr > 0
    out[mask] = (np.log(pr[mask]) - np.log(lo)) / (np.log(hi) - np.log(lo)) * 100.0
    return np.clip(out, 0.0, 100.0)
