# -*- coding: utf-8 -*-
"""Comparaison de deux analyses du même site : pages qui reculent, liens disparus vers les cibles
protégées, nouveaux liens. C'est le garde-fou contre le sculptage en boucle : une décision de
maillage prise pour une cible ne doit pas être défaite plus tard pour en pousser une autre."""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime

from . import __version__
from .clean import normalize_url
from .loaders import read_table, pick


class DiffInputError(ValueError):
    """Dossier qui n'est pas une analyse (pages.csv absent) : la comparaison ne peut pas avoir lieu."""


def _read_pages(d: str) -> dict[str, dict]:
    p = os.path.join(d, "pages.csv")
    if not os.path.exists(p):
        raise DiffInputError(f"pages.csv absent dans {d} : ce dossier n'est pas une analyse. Lancer "
                             f"« prgraph compute --edges {os.path.join(d, 'edges.csv')} ... --out {d} » "
                             "(ou « prgraph crawl ... --run ») avant de comparer.")
    out = {}
    for r in read_table(p):
        u = pick(r, "url")
        if u:
            out[normalize_url(u)] = r
    return out


def _read_edges(d: str) -> dict[tuple[str, str], str] | None:
    """None si edges.csv manque : les liens disparus ne sont alors pas contrôlés (jamais « 0 » en silence)."""
    p = os.path.join(d, "edges.csv")
    out = {}
    if not os.path.exists(p):
        return None
    for r in read_table(p):
        s, t = pick(r, "source"), pick(r, "target")
        if s and t:
            out[(normalize_url(s), normalize_url(t))] = pick(r, "position", default="unknown")
    return out


def _f(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def diff_runs(before: str, after: str, protected: list[str] | None = None, drop_threshold: float = 5.0) -> dict:
    pb, pa = _read_pages(before), _read_pages(after)
    eb, ea = _read_edges(before), _read_edges(after)
    edges_missing = [d for d, e in ((before, eb), (after, ea)) if e is None]
    eb, ea = eb or {}, ea or {}
    prot = {normalize_url(u) for u in (protected or [])}
    # Cibles protégées par défaut : pages money / stratégiques / famine de l'analyse de référence
    for u, r in pb.items():
        flags = pick(r, "flags")
        if any(f in flags for f in ("strategique", "money", "famine")) or pick(r, "classe") == "money":
            prot.add(u)
    common = [u for u in pb if u in pa]
    # Le score log 0-100 dépend du minimum et du maximum du site : une orpheline en plus le déplace pour toutes
    # les pages. La comparaison porte donc aussi sur la part d'équité (colonne pagerank, somme 1) et sur le rang.
    rank_b = {u: r for r, u in enumerate(sorted(pb, key=lambda x: -_f(pb[x].get("pagerank"))), 1)}
    rank_a = {u: r for r, u in enumerate(sorted(pa, key=lambda x: -_f(pa[x].get("pagerank"))), 1)}
    movers = []
    for u in common:
        d = _f(pa[u].get("score")) - _f(pb[u].get("score"))
        share_b, share_a = _f(pb[u].get("pagerank")), _f(pa[u].get("pagerank"))
        rel = (share_a - share_b) / share_b if share_b > 0 else 0.0
        movers.append((u, d, _f(pb[u].get("score")), _f(pa[u].get("score")), int(_f(pb[u].get("in_links"))), int(_f(pa[u].get("in_links"))),
                       rel, rank_b.get(u, 0), rank_a.get(u, 0)))
    movers.sort(key=lambda m: (m[6] if m[6] else m[1]))
    # Régression : recul de score au-delà du seuil, OU part d'équité en baisse d'au moins un quart, OU rang qui
    # recule d'au moins 10 % des pages. Le seuil de score seul manquerait une page qui perd la moitié de son jus
    # pendant que le minimum du site bouge.
    n_pages = max(len(pa), 1)
    regressions = [m for m in movers if m[0] in prot and (m[1] <= -drop_threshold or m[6] <= -0.25
                                                           or (m[8] - m[7]) >= max(5, 0.10 * n_pages))]
    # Un côté sans edges.csv : comparer les liens donnerait tout « disparu » ou tout « nouveau ». Non contrôlé.
    lost_edges = [] if edges_missing else [(s, t, p) for (s, t), p in eb.items() if (s, t) not in ea]
    new_edges = [] if edges_missing else [(s, t, p) for (s, t), p in ea.items() if (s, t) not in eb]
    lost_to_protected = [e for e in lost_edges if e[1] in prot]
    pages_lost = [u for u in pb if u not in pa]
    pages_new = [u for u in pa if u not in pb]
    # Une page protégée absente de la nouvelle analyse n'est pas « stable » : URL changée (refonte), autre
    # hôte (recette), page supprimée ou non crawlée. diff ne compare que des URL identiques.
    protected_missing = sorted(u for u in prot if u not in pa)
    return {"before": before, "after": after, "n_before": len(pb), "n_after": len(pa), "protected": sorted(prot),
            "regressions": regressions, "losers": movers[:30], "gainers": list(reversed(movers[-30:])),
            "lost_edges": lost_edges, "new_edges": new_edges, "lost_to_protected": lost_to_protected,
            "pages_lost": pages_lost, "pages_new": pages_new, "drop_threshold": drop_threshold,
            "protected_missing": protected_missing, "edges_missing": edges_missing, "n_common": len(common)}


def has_alarm(d: dict) -> bool:
    return bool(d["regressions"] or d["lost_to_protected"] or d["protected_missing"])


def exit_code(d: dict) -> int:
    """1 = régression détectée, 2 = comparaison incomplète (edges.csv absent), 0 = rien à signaler."""
    return 1 if has_alarm(d) else (2 if d["edges_missing"] else 0)


def write_diff_md(path: str, d: dict) -> None:
    L = [f"Outil : prgraph {__version__} · {datetime.now():%Y-%m-%d}\n",
         f"# Comparaison de deux analyses\n",
         f"Référence : `{d['before']}` ({d['n_before']} pages) · Nouvelle : `{d['after']}` ({d['n_after']} pages) · "
         f"{d['n_common']} URL communes · généré le {datetime.now():%Y-%m-%d %H:%M}.\n",
         "## Verdict\n"]
    if has_alarm(d):
        L.append(f"🚨 **{len(d['regressions'])} page(s) protégée(s) reculent (score de {d['drop_threshold']:.0f} points ou plus, "
                 f"part d'équité d'un quart ou plus, ou rang de 10 % des pages ou plus), "
                 f"{len(d['lost_to_protected'])} lien(s) vers des cibles protégées ont disparu et "
                 f"{len(d['protected_missing'])} page(s) protégée(s) sont absentes de la nouvelle analyse.** "
                 "Vérifier avant d'aller plus loin : une décision de maillage antérieure a pu être défaite.\n")
    elif not d["edges_missing"]:
        L.append("✅ Aucune page protégée ne recule au-delà du seuil et aucun lien vers une cible protégée n'a disparu.\n")
    if d["edges_missing"]:
        L.append(f"⚠️ **Comparaison incomplète** : edges.csv absent dans {', '.join(f'`{x}`' for x in d['edges_missing'])}. "
                 "Les liens disparus n'ont pas été contrôlés. diff lit pages.csv et edges.csv dans le même dossier : "
                 "lancer compute avec `--out` sur le dossier du crawl.\n")
    if d["protected_missing"]:
        L.append("## Pages protégées absentes de la nouvelle analyse\n")
        L.append("diff ne compare que des URL identiques et ne suit pas les redirections : une URL changée par la refonte, "
                 "une recette sur un autre hôte ou une page non crawlée apparaît ici. Contrôler chacune par le plan de "
                 "redirection avant de conclure.\n")
        for u in d["protected_missing"][:100]:
            L.append(f"- {u}")
        L.append("")
    L.append("| Indicateur | Valeur |\n|---|---|")
    L.append(f"| Pages protégées suivies (money, stratégiques, famine de la référence, liste fournie) | {len(d['protected'])} |")
    L.append(f"| Pages disparues / nouvelles | {len(d['pages_lost'])} / {len(d['pages_new'])} |")
    L.append(f"| Liens disparus / nouveaux (paires source → cible) | {len(d['lost_edges'])} / {len(d['new_edges'])} |")
    L.append(f"| Liens disparus vers des cibles protégées | {len(d['lost_to_protected'])} |\n")
    if d["regressions"]:
        L.append("## Pages protégées qui reculent\n")
        L.append("| URL | Score avant | Score après | Δ score | Δ part d'équité | Rang avant → après | Liens entrants avant → après |\n|---|---|---|---|---|---|---|")
        for u, dd, sb, sa, ib, ia, rel, rb, ra in d["regressions"]:
            L.append(f"| {u} | {sb:.0f} | {sa:.0f} | {dd:+.1f} | {rel:+.0%} | {rb} → {ra} | {ib} → {ia} |")
        L.append("")
    if d["lost_to_protected"]:
        L.append("## Liens disparus vers des cibles protégées\n")
        L.append("| Source | Cible | Position |\n|---|---|---|")
        for s, t, p in d["lost_to_protected"][:100]:
            L.append(f"| {s} | {t} | {p} |")
        L.append("")
    L.append("## Plus fortes baisses de part d'équité\n")
    L.append("| URL | Score avant | Score après | Δ score | Δ part d'équité | Rang avant → après |\n|---|---|---|---|---|---|")
    for u, dd, sb, sa, ib, ia, rel, rb, ra in d["losers"][:20]:
        if rel < 0 or dd < 0:
            L.append(f"| {u} | {sb:.0f} | {sa:.0f} | {dd:+.1f} | {rel:+.0%} | {rb} → {ra} |")
    L.append("\n## Plus fortes hausses de part d'équité\n")
    L.append("| URL | Score avant | Score après | Δ score | Δ part d'équité | Rang avant → après |\n|---|---|---|---|---|---|")
    for u, dd, sb, sa, ib, ia, rel, rb, ra in d["gainers"][:20]:
        if rel > 0 or dd > 0:
            L.append(f"| {u} | {sb:.0f} | {sa:.0f} | {dd:+.1f} | {rel:+.0%} | {rb} → {ra} |")
    L.append("")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def write_diff_csv(path: str, d: dict) -> None:
    from .export import neutralise_cell
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        raw = csv.writer(fh)

        class _W:
            @staticmethod
            def writerow(row):
                raw.writerow([neutralise_cell(c) if isinstance(c, str) else c for c in row])
        w = _W()
        w.writerow(["type", "url_ou_source", "cible", "position", "score_avant", "score_apres", "delta_score",
                    "delta_part_equite", "rang_avant", "rang_apres", "protegee"])
        prot = set(d["protected"])
        seen = set()
        for u, dd, sb, sa, ib, ia, rel, rb, ra in d["losers"] + d["gainers"]:
            if u in seen:
                continue            # sur un petit site, les 30 premiers et les 30 derniers se recouvrent
            seen.add(u)
            w.writerow(["page", u, "", "", f"{sb:.1f}", f"{sa:.1f}", f"{dd:+.1f}", f"{rel:+.3f}", rb, ra, int(u in prot)])
        for s, t, p in d["lost_edges"]:
            w.writerow(["lien_disparu", s, t, p, "", "", "", "", "", "", int(t in prot)])
        for s, t, p in d["new_edges"]:
            w.writerow(["lien_nouveau", s, t, p, "", "", "", "", "", "", int(t in prot)])
