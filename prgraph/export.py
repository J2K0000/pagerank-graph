# -*- coding: utf-8 -*-
"""Sorties : pages.csv, segments.csv, flows_segments.csv, findings.md, graph.json, report.html."""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime
from urllib.parse import urlsplit

import numpy as np

from . import __version__
from .analysis import CLASS_LABELS, CLASS_NAMES, QUADRANT_NAMES, Analysis
from .graph import Graph
from .loaders import POSITIONS

FLAG_HOME, FLAG_STRATEGIC, FLAG_MONEY, FLAG_STARVING, FLAG_OVERFED, FLAG_UTILITY, FLAG_DANGLING, \
    FLAG_ORPHAN, FLAG_UNREACHABLE, FLAG_UNCRAWLED = 1, 2, 4, 8, 16, 32, 64, 128, 256, 512

VIEWER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer")
VIEWER_TEMPLATE = os.path.join(VIEWER_DIR, "template.html")
PRESENTATION_TEMPLATE = os.path.join(VIEWER_DIR, "present.html")
GRAPH_DATA_MARKER = "/*__GRAPH_DATA__*/null"
PRESENTATION_MARKER = "<!--__PRESENTATION__-->"
# Bibliothèques du visualiseur livrées avec le paquet (licences MIT dans viewer/vendor/) : le rapport les
# embarque pour s'ouvrir hors ligne, sans appel à un tiers. `cdn=True` garde les balises <script src> du CDN.
VENDOR_LIBS = {
    "https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js": "graphology.umd.min.js",
    "https://cdn.jsdelivr.net/npm/graphology-library@0.8.0/dist/graphology-library.min.js": "graphology-library.min.js",
    "https://cdn.jsdelivr.net/npm/sigma@3.0.1/dist/sigma.min.js": "sigma.min.js",
}
FAMILY_SLOTS = 7          # familles colorées (palette catégorielle validée) ; les suivantes forment « Autres »
_LANG_PREFIX = re.compile(r"^[a-z]{2}(-[a-z]{2})?/", re.I)

# Barème de lecture du score d'alignement, affiché sous le KPI (findings.md § 0 et rapport HTML)
ALIGNMENT_SCALE = "sous 70 : désaligné ; 70 à 85 : à corriger ; au-dessus de 85 : aligné par classe, vérifier les segments"
SILO_MATRIX_MAX = 10   # segments affichés dans la matrice des silos de findings.md, le reste en « autres »

# ── Neutralisation des cellules texte (injection de formule tableur) ─────────
# Un tableur (Excel, LibreOffice, Sheets) interprète une cellule qui commence par =, +, -, @, tabulation
# ou retour chariot comme une formule : un titre crawlé « =HYPERLINK(...) » devient un lien de hameçonnage
# dans le classeur remis au client. Les nombres formatés (« -0.0123 », « +1.5 ») restent intacts.
_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")
_NUMERIC = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?\s*%?$")


def neutralise_cell(v):
    """Préfixe d'une apostrophe toute chaîne qui serait lue comme une formule par un tableur.

    Les valeurs non textuelles et les chaînes numériques sont renvoyées telles quelles ; la fonction est
    idempotente (une chaîne déjà préfixée commence par une apostrophe, qui n'est pas un déclencheur)."""
    if not isinstance(v, str) or not v:
        return v
    if v[0] in _FORMULA_PREFIX and not _NUMERIC.match(v):
        return "'" + v
    return v


def _row(cells) -> list:
    return [neutralise_cell(c) for c in cells]


def node_flags(an: Analysis) -> np.ndarray:
    n = an.n
    flags = np.zeros(n, dtype=np.int32)
    if an.home is not None:
        flags[an.home] |= FLAG_HOME
    flags[an.strategic] |= FLAG_STRATEGIC
    flags[an.money] |= FLAG_MONEY
    flags[an.starving] |= FLAG_STARVING
    flags[an.overfed] |= FLAG_OVERFED
    flags[an.utility] |= FLAG_UTILITY
    flags[an.dangling] |= FLAG_DANGLING
    if an.orphans:
        flags[np.asarray(an.orphans)] |= FLAG_ORPHAN
    if an.unreachable:
        flags[np.asarray(an.unreachable)] |= FLAG_UNREACHABLE
    if an.uncrawled is not None and an.uncrawled.any():
        flags[an.uncrawled] |= FLAG_UNCRAWLED
    return flags


def flag_labels(f: int) -> str:
    names = []
    for bit, name in ((FLAG_HOME, "accueil"), (FLAG_STRATEGIC, "strategique"), (FLAG_MONEY, "money"),
                      (FLAG_STARVING, "famine"), (FLAG_OVERFED, "gouffre"), (FLAG_UTILITY, "utilitaire"),
                      (FLAG_DANGLING, "cul-de-sac"), (FLAG_ORPHAN, "orpheline"), (FLAG_UNREACHABLE, "inatteignable"),
                      (FLAG_UNCRAWLED, "non-crawlee")):
        if f & bit:
            names.append(name)
    return "|".join(names)


# ── CSV ───────────────────────────────────────────────────────────────────────

def write_pages_csv(path: str, g: Graph, pr, score, chei, cscore, an: Analysis, titles: list[str],
                    extra: dict[str, np.ndarray] | None = None) -> None:
    flags = node_flags(an)
    order = np.argsort(-score)
    # pr_share_pct = part d'équité (PageRank × 100) : comparable d'une analyse à l'autre, contrairement au
    # score log 0-100 qui est une position relative dans ce rapport (son minimum change à chaque page ajoutée).
    cols = ["url", "segment", "classe", "score", "pr_share_pct", "pagerank", "cheirank_score", "cheirank", "quadrant",
            "in_links", "out_links", "click_depth", "value", "value_share", "flags", "leak_fraction", "title"]
    if an.demand_col:
        cols.append(f"demand_{an.demand_col}")
    if extra:
        cols += list(extra)
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_row(cols))
        for i in order:
            row = [g.urls[i], an.segments[i], an.classes[i] if an.classes else "", f"{score[i]:.2f}",
                   f"{100 * pr[i]:.4f}", f"{pr[i]:.10g}", f"{cscore[i]:.2f}",
                   f"{chei[i]:.10g}", QUADRANT_NAMES[an.quadrant[i]], int(an.in_deg[i]), int(an.out_deg[i]),
                   int(an.depth[i]), f"{an.value[i]:.4g}", f"{an.value_share[i]:.6f}", flag_labels(int(flags[i])),
                   f"{an.leak_frac[i]:.3f}", titles[i] if i < len(titles) else ""]
            if an.demand_col:
                row.append(f"{an.demand[i]:.4g}")
            if extra:
                row += [f"{arr[i]:.6g}" for arr in extra.values()]
            w.writerow(_row(row))


def write_segments_csv(path: str, an: Analysis) -> None:
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["segment", "pages", "pr_share", "value_share", "gap_value_minus_pr", "score_mean",
                    "score_median", "leak_out_share", "starving", "overfed", "dangling"])
        for r in sorted(an.seg_table, key=lambda r: -r["pr_share"]):
            w.writerow(_row([r["segment"], r["pages"], f"{r['pr_share']:.4f}", f"{r['value_share']:.4f}",
                             f"{r['gap']:+.4f}", f"{r['score_mean']:.1f}", f"{r['score_median']:.1f}",
                             f"{r['leak_out_share']:.3f}", r["starving"], r["overfed"], r["dangling"]]))


def write_flows_csv(path: str, an: Analysis) -> None:
    S = len(an.segment_names)
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["from_segment", "to_segment", "pagerank_mass", "share_of_source_outflow"])
        for i in range(S):
            tot = an.seg_flows[i].sum()
            for j in range(S):
                m = an.seg_flows[i, j]
                if m > 0:
                    w.writerow(_row([an.segment_names[i], an.segment_names[j], f"{m:.8f}", f"{m / tot:.4f}" if tot else "0"]))


# ── graph.json ────────────────────────────────────────────────────────────────

def select_nodes(an: Analysis, score: np.ndarray, max_nodes: int, rng_seed: int = 7) -> np.ndarray:
    n = an.n
    if n <= max_nodes:
        return np.arange(n)
    keep = np.zeros(n, dtype=bool)
    if an.home is not None:
        keep[an.home] = True
    keep |= an.strategic | an.starving | an.overfed | an.money
    for lst in (an.black_holes, an.overlinked):
        if lst:
            keep[np.asarray(lst)] = True
    for grp in an.cannibal_groups:
        keep[np.asarray(grp["pages"])] = True
    budget = max_nodes - int(keep.sum())
    if budget > 0:
        top = np.argsort(-score)
        top = top[~keep[top]][: budget // 2]
        keep[top] = True
        budget = max_nodes - int(keep.sum())
    if budget > 0:
        rng = np.random.default_rng(rng_seed)
        rest = np.flatnonzero(~keep)
        # Échantillon stratifié par segment, proportionnel à la taille du segment
        seg = an.segment_index[rest]
        counts = np.bincount(seg, minlength=len(an.segment_names))
        for s_i, c in enumerate(counts):
            if c == 0:
                continue
            k = max(1, int(round(budget * c / len(rest))))
            cand = rest[seg == s_i]
            keep[rng.choice(cand, size=min(k, len(cand)), replace=False)] = True
    return np.flatnonzero(keep)


def worst_segment(an: Analysis) -> dict | None:
    """Segment au plus grand écart positif (part de la valeur moins part du PageRank) : le principal démenti
    du score d'alignement par classe. Lit `an.alignment["worst_segment"]` s'il existe, sinon le calcule
    depuis `an.seg_table`. None sans colonne de valeur ou sans écart positif."""
    ws = (an.alignment or {}).get("worst_segment")
    if ws and ws.get("segment") is not None:
        return {"segment": str(ws["segment"]), "pr_share": float(ws.get("pr_share", 0.0)),
                "value_share": float(ws.get("value_share", 0.0))}
    if not an.value_col or not an.seg_table:
        return None
    r = max(an.seg_table, key=lambda r: r.get("gap", 0.0))
    if r.get("gap", 0.0) <= 0:
        return None
    return {"segment": str(r["segment"]), "pr_share": float(r["pr_share"]), "value_share": float(r["value_share"])}


def worst_family(an: Analysis) -> dict | None:
    """Rubrique (famille de segments, toutes langues) au plus grand écart positif entre part de la valeur et part
    du PageRank : le chiffre de tête du mode Présentation, écrit aussi dans findings.md et le classeur pour qu'il
    ait sa pièce. None sans colonne de valeur ou sans écart positif."""
    if not an.value_col or not an.seg_table:
        return None
    fams, _ = segment_families(an)
    best = None
    for f in fams:
        if f["name"] in ("autres", "accueil"):
            continue
        if f["gap"] > 0 and (best is None or f["gap"] > best["gap"]):
            best = f
    if best is None:
        return None
    return {"family": best["name"], "segments": list(best["segments"]), "langs": list(best["langs"]),
            "pr_share": float(best["pr_share"]), "value_share": float(best["value_share"]), "gap": float(best["gap"])}


def alignment_reading(score: float, ws: dict | None, spearman: float | None) -> str:
    """Phrase de lecture du KPI : le score par classe, son principal démenti par segment et la corrélation de rang."""
    txt = f"{score:.0f} / 100 par classe"
    if ws:
        txt += (f", mais le segment {ws['segment']} reçoit {_pct(ws['pr_share'])} du PageRank pour "
                f"{_pct(ws['value_share'])} de la valeur")
    if spearman is not None:
        txt += " ; corrélation de rang valeur ↔ score : " + f"{spearman:+.2f}".replace("-", "−")
    return txt + "."


def teleport_vector(teleport, sel: np.ndarray, n: int) -> list[float] | None:
    """Vecteur de téléportation (`--restart`) restreint aux nœuds exportés, renormalisé, dans l'ordre des nœuds.
    None si aucun vecteur n'est fourni ou s'il est uniforme (le JavaScript utilise alors 1/n)."""
    if teleport is None:
        return None
    t = np.asarray(teleport, dtype=np.float64).ravel()
    if t.shape[0] != n or not np.isfinite(t).all() or t.sum() <= 0:
        return None
    t = t / t.sum()
    if np.allclose(t, 1.0 / n, rtol=0.0, atol=1e-12):
        return None
    sub = t[sel]
    if sub.sum() <= 0:
        return None
    sub = sub / sub.sum()
    return [float(f"{v:.6g}") for v in sub]


def segment_families(an: Analysis) -> tuple[list[dict], list[int]]:
    """Regroupe les segments par rubrique, sans le préfixe de langue (« fr/blog », « uk/blog » et « blog »
    forment la famille « blog »). Les familles sont triées par part de PageRank ; les FAMILY_SLOTS premières
    reçoivent un emplacement de la palette catégorielle (dans cet ordre, jamais recyclé), les suivantes sont
    regroupées en « Autres ». Retourne (familles, famille de chaque segment)."""
    names = []
    for r in an.seg_table:
        s = str(r["segment"])
        base = _LANG_PREFIX.sub("", s) if "/" in s else s
        names.append("accueil" if s == "accueil" else base)
    fam: dict[str, dict] = {}
    for r, f in zip(an.seg_table, names):
        d = fam.setdefault(f, {"name": f, "segments": [], "pages": 0, "pr_share": 0.0, "value_share": 0.0, "langs": []})
        d["segments"].append(str(r["segment"]))
        d["pages"] += int(r["pages"])
        d["pr_share"] += float(r["pr_share"])
        d["value_share"] += float(r["value_share"])
        s = str(r["segment"])
        if "/" in s and _LANG_PREFIX.match(s):
            d["langs"].append(s.split("/")[0])
    ordered = sorted(fam.values(), key=lambda d: -d["pr_share"])
    out, index = [], {}
    others = {"name": "autres", "segments": [], "pages": 0, "pr_share": 0.0, "value_share": 0.0, "langs": [], "slot": None,
              "members": []}
    for k, d in enumerate(ordered):
        if k < FAMILY_SLOTS or len(ordered) <= FAMILY_SLOTS + 1:
            d["slot"] = k if k < FAMILY_SLOTS else None
            index[d["name"]] = len(out)
            out.append(d)
        else:
            others["segments"] += d["segments"]
            others["members"].append(d["name"])
            for key in ("pages", "pr_share", "value_share"):
                others[key] += d[key]
    if others["segments"]:
        index.update({m: len(out) for m in others["members"]})
        out.append(others)
    for d in out:
        d["pr_share"] = float(f"{d['pr_share']:.6g}")
        d["value_share"] = float(f"{d['value_share']:.6g}")
        d["gap"] = float(f"{d['value_share'] - d['pr_share']:.6g}")
        d["langs"] = sorted(set(d["langs"]))
    return out, [index[f] for f in names]


def build_graph_json(g: Graph, pr, score, chei, cscore, an: Analysis, titles, meta: dict,
                     max_nodes: int = 3000, teleport=None) -> dict:
    """`teleport` : vecteur de téléportation du pipeline (`getattr(pipeline, "teleport", None)`, tableau numpy
    ou None) ; accepté aussi par la clé `meta["teleport"]`. Exporté en `meta.teleport` pour le what-if JS."""
    sel = select_nodes(an, score, max_nodes)
    if teleport is None:
        teleport = meta.get("teleport")
    meta = {k: v for k, v in meta.items() if k != "teleport"}
    truncated = len(sel) < an.n
    remap = -np.ones(an.n, dtype=np.int64)
    remap[sel] = np.arange(len(sel))
    flags = node_flags(an)
    emask = (remap[g.src] >= 0) & (remap[g.dst] >= 0)
    es, ed = remap[g.src[emask]], remap[g.dst[emask]]
    ew, ep = g.w[emask], g.pos[emask]

    nodes = []
    demand = an.demand if an.demand is not None else None
    cls_index = {c: k for k, c in enumerate(CLASS_NAMES)}
    cls_default = cls_index.get("neutre", 0)
    for i in sel:
        nodes.append([g.urls[i], int(an.segment_index[i]), float(f"{pr[i]:.6g}"), round(float(score[i]), 2),
                      float(f"{chei[i]:.6g}"), round(float(cscore[i]), 2), int(an.in_deg[i]), int(an.out_deg[i]),
                      int(an.depth[i]), float(f"{an.value[i]:.6g}"), int(flags[i]), int(an.quadrant[i]),
                      (titles[i] if i < len(titles) else "")[:120], float(f"{g.leaked_out[i]:.6g}"),
                      float(f"{demand[i]:.6g}") if demand is not None else 0.0,
                      cls_index.get(an.classes[i], cls_default) if an.classes else cls_default])
    edges = [[int(a), int(b), float(f"{w:.4g}"), int(p)] for a, b, w, p in zip(es, ed, ew, ep)]

    def ids(lst):
        return [int(remap[i]) for i in lst if remap[i] >= 0]

    actions = []
    for a in an.actions:
        b = {k: v for k, v in a.items() if k not in ("targets", "donors")}
        b["targets"] = ids(a.get("targets", []))
        b["donors"] = ids(a.get("donors", []))
        if b.get("prescription"):
            p_ = dict(b["prescription"])
            p_["target"] = int(remap[p_["target"]]) if remap[p_["target"]] >= 0 else None
            p_["steps"] = [{**st, "donor": int(remap[st["donor"]]) if remap[st["donor"]] >= 0 else None} for st in p_["steps"]]
            b["prescription"] = p_
        actions.append(b)

    families, seg_family = segment_families(an)
    return {
        "meta": {**meta, "tool_version": __version__, "generated": datetime.now().isoformat(timespec="seconds"),
                 "n_nodes": an.n, "n_edges": g.m, "exported_nodes": len(sel), "exported_edges": len(edges),
                 "truncated": truncated, "home": int(remap[an.home]) if an.home is not None and remap[an.home] >= 0 else None,
                 "positions": POSITIONS, "quadrants": QUADRANT_NAMES, "thresholds": an.thresholds,
                 "value_col": an.value_col, "demand_col": an.demand_col, "spearman": an.spearman,
                 "position_counts": an.position_counts,
                 "teleport": teleport_vector(teleport, sel, an.n),
                 "alignment_scale": (an.alignment or {}).get("scale") or ALIGNMENT_SCALE,
                 "node_fields": ["url", "seg", "pr", "score", "chei", "cscore", "indeg", "outdeg", "depth",
                                 "value", "flags", "quadrant", "title", "leak", "demand", "cls"],
                 "classes": CLASS_NAMES, "class_labels": CLASS_LABELS},
        "alignment": ({**an.alignment, "worst_segment": worst_segment(an), "worst_family": worst_family(an)}
                      if an.alignment else an.alignment),
        "actions": actions,
        "segments": [{"id": i, "name": r["segment"], "family": seg_family[i], **{k: v for k, v in r.items() if k != "segment"}}
                     for i, r in enumerate(an.seg_table)],
        "families": families,
        "scenario": getattr(an, "scenario", None),
        "seg_flows": [[float(f"{v:.6g}") for v in row] for row in an.seg_flows],
        "zones": {k: float(f"{v:.6g}") for k, v in an.zone_flows.items()},
        "thermal": an.thermal,
        "nodes": nodes,
        "edges": edges,
        "top": {"black_holes": ids(an.black_holes), "overlinked": ids(an.overlinked), "starving": ids(an.starving_list),
                "demand_starving": ids(an.demand_list),
                "orphans_count": len(an.orphans), "unreachable_count": len(an.unreachable),
                "cannibal": [{"group": c["group"], "pages": ids(c["pages"]), "leader": int(remap[c["leader"]]),
                              "ratio": None if c["ratio"] == float("inf") else round(c["ratio"], 2), "decided": c["decided"]}
                             for c in an.cannibal_groups]},
    }


def write_json(path: str, data: dict) -> None:
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))


def json_for_script(data: dict) -> str:
    """JSON sûr à l'intérieur d'un élément <script> HTML.

    Le tokeniseur HTML ne connaît pas JSON : la séquence « <!--<script » dans une chaîne fait passer le
    parseur en état « script double-échappé » et la balise de fermeture du bloc de données n'est plus
    reconnue (le rapport s'ouvre vide). Tous les « < », « > » et « & » sont donc écrits en échappements
    JSON (\\u003c, \\u003e, \\u0026), ainsi que U+2028 et U+2029, qui sont des fins de ligne pour
    JavaScript mais pas pour JSON. Le résultat reste du JSON strictement équivalent."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for ch, rep in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                    (" ", "\\u2028"), (" ", "\\u2029")):
        payload = payload.replace(ch, rep)
    return payload


def _inline_script(js: str) -> str | None:
    """Contenu JS sûr dans un <script> en ligne, ou None s'il ne peut pas être embarqué tel quel. Les versions
    livrées ne contiennent ni « </script » ni « <!-- » (vérifié à l'ajout) ; une version qui en contiendrait
    garde sa balise CDN plutôt que d'être réécrite à l'aveugle (« <!-- » peut être du code valide, x<!--y)."""
    if "</script" in js.lower() or "<!--" in js:
        return None
    return js


def embed_vendor_libs(html: str) -> str:
    """Remplace les balises <script src> des bibliothèques connues par leur contenu livré avec le paquet."""
    for url, fname in VENDOR_LIBS.items():
        p = os.path.join(VIEWER_DIR, "vendor", fname)
        if not os.path.exists(p):
            # Le rapport appellerait un tiers à l'ouverture : on le dit au lieu de le faire en silence.
            import sys
            print(f"avertissement : bibliothèque embarquée absente ({p}) : report.html la chargera depuis {url}",
                  file=sys.stderr)
            continue
        tag_rx = re.compile(r'<script[^>]*src="' + re.escape(url) + r'"[^>]*>\s*</script>')
        if not tag_rx.search(html):
            continue
        with io.open(p, encoding="utf-8") as fh:
            js = _inline_script(fh.read())
        if js is None:
            continue
        html = tag_rx.sub(lambda _m: f"<script>/* {fname} (MIT, voir viewer/vendor) */\n{js}\n</script>", html, count=1)
    return html


def write_report_html(path: str, data: dict, template_path: str = VIEWER_TEMPLATE, cdn: bool = False,
                      presentation_path: str = PRESENTATION_TEMPLATE) -> None:
    """report.html autonome : données injectées, mode Présentation inséré (si le gabarit porte le marqueur et que
    viewer/present.html existe), bibliothèques embarquées sauf `cdn=True`."""
    with io.open(template_path, encoding="utf-8") as fh:
        tpl = fh.read()
    count = tpl.count(GRAPH_DATA_MARKER)
    if count != 1:
        raise ValueError(f"gabarit {template_path} : le marqueur {GRAPH_DATA_MARKER} doit apparaître exactement "
                         f"une fois (trouvé {count} fois) ; le rapport ne peut pas recevoir ses données")
    if PRESENTATION_MARKER in tpl and presentation_path and os.path.exists(presentation_path):
        with io.open(presentation_path, encoding="utf-8") as fh:
            tpl = tpl.replace(PRESENTATION_MARKER, fh.read(), 1)
    if not cdn:
        tpl = embed_vendor_libs(tpl)
    # Les données en dernier : leur contenu (titres crawlés) ne doit jamais être relu comme un marqueur.
    html = tpl.replace(GRAPH_DATA_MARKER, json_for_script(data), 1)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


# ── findings.md ───────────────────────────────────────────────────────────────

def _pct(x: float) -> str:
    return f"{100 * x:.1f} %"


def _num(x: float) -> str:
    """Nombre lisible : entier sans exposant, sinon 4 chiffres significatifs."""
    x = float(x)
    if x.is_integer() and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.4g}"


def _depth(d) -> str:
    """Profondeur de clic lisible : -1 (aucun chemin depuis l'accueil) s'écrit « inatteignable »."""
    d = int(d)
    return "inatteignable" if d < 0 else str(d)


def _main_host(g: Graph, an: Analysis) -> str:
    """Hôte de référence des tableaux : celui de l'accueil, sinon l'hôte majoritaire des URL."""
    if an.home is not None and an.home < len(g.urls):
        h = urlsplit(g.urls[an.home]).netloc
        if h:
            return h
    counts: dict[str, int] = {}
    for u in g.urls:
        h = urlsplit(u).netloc
        if h:
            counts[h] = counts.get(h, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def _short(url: str, host: str) -> str:
    """URL affichée en chemin (plus requête) quand elle est sur l'hôte de référence, en entier sinon."""
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    if p.netloc and p.netloc == host:
        return (p.path or "/") + (f"?{p.query}" if p.query else "")
    return url


def write_findings_md(path: str, g: Graph, pr, score, chei, cscore, an: Analysis, meta: dict,
                      clean_stats: dict, pr_info: dict) -> None:
    host = _main_host(g, an)
    urls = [_short(u, host) for u in g.urls]
    L: list[str] = []
    L.append(f"Outil : prgraph {__version__} · {datetime.now():%Y-%m-%d}\n")
    L.append(f"# PageRank interne pondéré : {meta.get('site', 'site')}\n")
    L.append(f"Généré le {datetime.now():%Y-%m-%d %H:%M} par pagerank-graph {__version__}. "
             "Les scores sont un **ordre relatif** de pages à renforcer, jamais une prédiction de positionnement "
             "(poids par position = heuristique [Consensus], brevet du surfeur raisonnable US 7,716,225 [Confirmé]).\n")
    if host:
        L.append(f"Hôte de référence : `{host}`. Les tableaux affichent les URL de cet hôte en chemin relatif ; "
                 "les URL complètes sont dans `pages.csv`.\n")

    al = an.alignment or {}
    if al:
        L.append("## 0. Verdict : alignement business et plan d'action\n")
        total_zone = sum(an.zone_flows.values()) or 1.0
        boiler0 = sum(an.zone_flows.get(p, 0.0) for p in ("header", "nav", "footer", "sidebar", "boilerplate")) / total_zone
        L.append(f"**Score d'alignement business : {al['score']:.0f} / 100** (100 × (1 − écart en variation totale entre la "
                 f"répartition du PageRank par classe et la répartition cible ; cible = {al['basis']}).\n")
        ws = worst_segment(an)
        L.append(f"Barème : {al.get('scale') or ALIGNMENT_SCALE}.\n")
        if al.get("home_excluded"):
            L.append("L'accueil est exclu de la distance : ses clics sont des recherches de marque, pas une valeur à "
                     "aligner par le maillage.\n")
        L.append("| Indicateur | Valeur |\n|---|---|")
        L.append(f"| Score d'alignement par classe | {al['score']:.0f} / 100 |")
        if ws:
            L.append(f"| Pire écart de segment | {ws['segment']} : {_pct(ws['pr_share'])} du PageRank pour "
                     f"{_pct(ws['value_share'])} de la valeur ({100 * (ws['value_share'] - ws['pr_share']):+.1f} pt) |")
        else:
            L.append("| Pire écart de segment | n/a (aucun segment ne reçoit moins que sa part de valeur"
                     + ("" if an.value_col else ", pas de colonne de valeur") + ") |")
        wf = worst_family(an)
        if wf and len(wf["segments"]) > 1:
            L.append(f"| Pire écart de rubrique, toutes langues (chiffre du mode Présentation) | {wf['family']} "
                     f"({', '.join(wf['segments'])}) : {_pct(wf['pr_share'])} du PageRank pour {_pct(wf['value_share'])} "
                     f"de la valeur ({100 * wf['gap']:+.1f} pt) |")
        vp = al.get("value_pages_pr_share")
        if vp is not None:
            L.append(f"| Pages qui portent {100 * (al.get('value_pages_value_share') or 0.8):.0f} % de la valeur "
                     f"({al.get('value_pages_count', 0)} pages) | {_pct(vp)} du PageRank |")
        L.append(f"| Corrélation de rang valeur ↔ score (Spearman) | "
                 f"{f'{an.spearman:+.2f}'.replace('-', '−') if an.spearman is not None else 'n/a'} |")
        L.append("")
        L.append(f"Lecture : {alignment_reading(al['score'], ws, an.spearman)} Le score par classe ne voit pas un "
                 "transfert à l'intérieur d'une classe : le § 3 donne l'écart segment par segment.\n")
        L.append(f"{_pct(boiler0)} de l'énergie du site circule par le gabarit (menus, pieds de page, bandeaux répétés). "
                 f"Les pages légales et support captent {_pct(al['waste_share'])} du PageRank ; les pages money et acquisition "
                 f"en captent {_pct(al['productive_share'])}.\n")
        if an.value_col in ("clicks", "impressions", "sessions"):
            L.append(f"La cible est la répartition des {an.value_col} : les pages de conversion captent peu de trafic "
                     "organique, la cible les sous-pondère. Avec des conversions ou un revenu par page (GA4), la cible "
                     "reflète la valeur réelle ; les classes peuvent aussi être forcées avec `--labels`.\n")
        L.append("| Classe | Pages | Part du PageRank | Cible | Écart |\n|---|---|---|---|---|")
        for c in CLASS_NAMES:
            gap = al["pr_share"][c] - al["target_share"][c]
            L.append(f"| {CLASS_LABELS[c]} | {al['counts'][c]} | {_pct(al['pr_share'][c])} | {_pct(al['target_share'][c])} | {100 * gap:+.1f} pt |")
        L.append("")
        if an.actions:
            L.append("Plan d'action classé par gain simulé sur le graphe (détail et consignes dans `brief_dev.md`) :\n")
            L.append("| Priorité | Ticket | Action | Cible | Problème | Gain estimé |\n|---|---|---|---|---|---|")
            icons = {"critique": "🚨 Critique", "moyen": "⚠️ Moyen", "optimisation": "💡 Optimisation"}
            for a in an.actions[:15]:
                tgt = _short(a["target_urls"][0], host) if a["target_urls"] else "site entier"
                if len(a["target_urls"]) > 1:
                    tgt += f" (+{len(a['target_urls']) - 1})"
                L.append(f"| {icons[a['priority']]} | {a['id']} | {a['title']} | {tgt} | {a['problem']} | {a['gain_text']} |")
            L.append("")
    L.append("## 1. Résumé\n")
    L.append("| Indicateur | Valeur |\n|---|---|")
    L.append(f"| Pages canoniques en 200 (nœuds) | {an.n} |")
    L.append(f"| Liens uniques après fusion (arêtes) | {g.m} |")
    L.append(f"| Lignes de liens lues | {clean_stats.get('edges_in', 0)} |")
    L.append(f"| Doublons A→B fusionnés | {clean_stats.get('duplicates_collapsed', 0)} |")
    L.append(f"| Liens réécrits (301 / canonical traversés) | {clean_stats.get('redirect_or_canonical_rewrites', 0)} |")
    L.append(f"| Liens vers 4xx/5xx (équité perdue) | {clean_stats.get('lost_404', 0)} |")
    L.append(f"| Liens vers un autre hôte (fuite hors du site) | {clean_stats.get('lost_external', 0)} |")
    L.append(f"| Liens vers des URL non crawlées (fuite) | {clean_stats.get('lost_uncrawled', 0)} |")
    L.append(f"| Liens nofollow internes (jus jeté) | {clean_stats.get('nofollow', 0)} |")
    L.append(f"| Culs-de-sac (aucun lien sortant) | {int(an.dangling.sum())} |")
    if clean_stats.get("uncrawled_nodes"):
        L.append(f"| URL découvertes mais non crawlées (budget), traitées comme culs-de-sac | {clean_stats['uncrawled_nodes']} |")
    if clean_stats.get("boilerplate_reclassified"):
        L.append(f"| Liens requalifiés en gabarit (cible présente sur la quasi-totalité des pages) | {clean_stats['boilerplate_reclassified']} vers {clean_stats['boilerplate_targets']} URL |")
    L.append(f"| Orphelines dans le graphe (plancher, voir § 9) | {len(an.orphans)} |")
    L.append(f"| Inatteignables depuis l'accueil | {len(an.unreachable)} |")
    L.append(f"| Itérations / convergence | {pr_info.get('iterations')} / {'oui' if pr_info.get('converged') else 'non'} |")
    L.append(f"| Modèle | d = {meta.get('alpha')}, culs-de-sac → {meta.get('dangling')}, poids {meta.get('weights_profile')} |")
    wg = meta.get("weighting") or {}
    if wg.get("transitions"):
        t = wg["transitions"]
        L.append(f"| Clics observés | mélange {_pct(t['blend'])} donnée / {_pct(1 - t['blend'])} position, "
                 f"{_pct(t['edges_covered_share'])} des arêtes couvertes |")
    if wg.get("embeddings"):
        e = wg["embeddings"]
        L.append(f"| Composante sémantique | decay {e['decay']}, {_pct(e['nodes_embedded_share'])} des pages avec vecteur |")
    if an.value_col:
        L.append(f"| Colonne de valeur business | {an.value_col} |")
        if an.spearman is not None:
            L.append(f"| Corrélation de rang valeur ↔ score (Spearman, pages avec valeur) | {an.spearman:+.2f} |")
    L.append("")

    # § 1 bis : l'inférence du gabarit déplace plus de PageRank que le choix du profil de poids ; le lecteur
    # doit voir le seuil, les cibles touchées et les cibles épargnées pour juger si elle a eu raison.
    if clean_stats.get("boilerplate_threshold") or clean_stats.get("boilerplate_reclassified"):
        L.append("## 1 bis. Inférence du gabarit\n")
        L.append(f"Une cible liée en `content` ou `unknown` depuis au moins **{clean_stats.get('boilerplate_threshold', 0)} pages** "
                 "émettrices est requalifiée en `boilerplate` (bloc répété : logo, bandeau, méga-menu en `<div>`), avec un poids "
                 "qui baisse à mesure que la part des pages émettrices monte. "
                 f"Résultat : **{clean_stats.get('boilerplate_reclassified', 0)} liens** requalifiés vers "
                 f"**{clean_stats.get('boilerplate_targets', 0)} cibles** ; {clean_stats.get('boilerplate_protected', 0)} cible(s) money "
                 "ou stratégique(s) épargnée(s). L'inférence a parfois raison (bandeau de logos), parfois tort (page pilier citée "
                 "dans le texte) : vérifier les cibles ci-dessous et désactiver avec `--no-boilerplate-inference` si elle se trompe.\n")
        tl = clean_stats.get("boilerplate_target_list") or []
        if tl:
            L.append("| Cible requalifiée | Liens requalifiés | Part des pages émettrices |\n|---|---|---|")
            for item in tl[:15]:
                u, cnt, share = item[0], item[1], item[2]
                L.append(f"| {_short(str(u), host)} | {int(cnt)} | {_pct(float(share))} |")
            if len(tl) > 15:
                L.append(f"\n{len(tl) - 15} autre(s) cible(s) dans la liste complète (50 premières) de `graph.json`, `meta.stats.boilerplate_target_list`.")
        L.append("")

    L.append("## 2. Où part l'équité, par zone de page\n")
    L.append("Masse de PageRank transportée à chaque itération stationnaire, par emplacement des liens.\n")
    L.append("| Zone | Part de la masse transportée | Nb de liens | Poids unitaire |\n|---|---|---|---|")
    total = sum(an.zone_flows.values())
    for p in POSITIONS:
        if an.position_counts.get(p, 0) == 0:
            continue
        L.append(f"| {p} | {_pct(an.zone_flows[p] / total)} | {an.position_counts.get(p, 0)} | {meta['weights'].get(p, 0):.2f} |")
    L.append(f"| fuites et culs-de-sac (réinjectés) | {_pct(an.zone_flows['fuites_et_culs_de_sac'] / total)} | n/a | n/a |")
    L.append(f"| téléportation (1 - d) | {_pct(an.zone_flows['teleportation'] / total)} | n/a | n/a |")
    boiler = sum(an.zone_flows.get(p, 0.0) for p in ("header", "nav", "footer", "sidebar", "boilerplate"))
    L.append(f"\nPart du jus qui circule par le gabarit (header, nav, footer, sidebar, boilerplate) : **{_pct(boiler / total)}**. "
             "Ce qui est répété sur toutes les pages est le premier levier : le déplacer avant d'ajouter.\n")

    if wg:
        L.append("## 2 bis. Pondération des arêtes au-delà de la position\n")
        if wg.get("transitions"):
            t = wg["transitions"]
            L.append("### Clics observés\n")
            L.append(f"Poids d'arête = {_pct(t['blend'])} × part observée des transitions sortantes de la page source + "
                     f"{_pct(1 - t['blend'])} × part heuristique par position, masse sortante de chaque page conservée. "
                     "Une arête sans clic sur une page qui en a garde "
                     f"{_pct(1 - t['blend'])} de son poids heuristique : le plancher qui permet de conclure « mal placé » "
                     "plutôt que « inexistant » (piège de circularité). Les pages sans donnée gardent l'heuristique seule.\n")
            L.append("| Couverture | Valeur |\n|---|---|")
            L.append(f"| Couples source → cible lus / retrouvés dans le graphe | {t['pairs_read']} / {t['pairs_matched']} "
                     f"({t['pairs_unmatched']} non retrouvés) |")
            L.append(f"| Transitions retrouvées (part du volume) | {_pct(t['transitions_matched_share'])} |")
            L.append(f"| Arêtes couvertes par au moins un clic | {t['edges_covered']} / {t['edges_total']} ({_pct(t['edges_covered_share'])}) |")
            L.append(f"| Masse heuristique couverte | {_pct(t['mass_covered_share'])} |")
            L.append(f"| Pages sources avec donnée | {t['sources_with_data']} / {t['sources_total']} ({_pct(t['sources_with_data_share'])}) |")
            L.append(f"| Arêtes au plancher (page avec donnée, lien jamais cliqué) | {t['edges_floored']} |")
            L.append("\nLes transitions observées décrivent le design en place, pas une cible : un lien jamais cliqué est "
                     "un lien mal placé, pas un lien à supprimer.\n")
        if wg.get("embeddings"):
            e = wg["embeddings"]
            L.append("### Composante sémantique\n")
            L.append(f"Poids d'arête multiplié par max(0, cos(source, cible)) ** {e['decay']}, puis renormalisé par page source : "
                     "la masse émise ne change pas, un lien hors sujet en transmet moins et ses voisins thématiques "
                     "récupèrent la différence. Arête sans vecteur à l'une des extrémités : facteur 1.\n")
            L.append("| Couverture | Valeur |\n|---|---|")
            L.append(f"| Pages avec vecteur (dimension {e['dimension']}) | {e['nodes_embedded']} / {e['nodes_total']} ({_pct(e['nodes_embedded_share'])}) |")
            L.append(f"| Arêtes pénalisables (deux extrémités avec vecteur) | {e['edges_scored']} / {e['edges_total']} ({_pct(e['edges_scored_share'])}) |")
            L.append(f"| Facteur moyen sur ces arêtes | {e['factor_mean']:.2f} |")
            L.append(f"| Arêtes tombées à zéro (cos ≤ 0) | {e['edges_zeroed']} |")
            if e.get("sources_all_zero"):
                L.append(f"| Pages dont toutes les arêtes tombaient à zéro (poids d'origine conservés) | {e['sources_all_zero']} |")
            if an.thermal:
                th = an.thermal
                L.append("\n**Déperdition thermique.** Masse de PageRank qui aurait circulé vers chaque segment sans la pénalité "
                         "(poids avant composante sémantique) contre masse effective, au même PageRank stationnaire. "
                         "Un écart positif = le segment recevait du jus par des liens hors sujet ; négatif = il en récupère.\n")
                L.append(f"Masse réacheminée : **{_pct(th['rerouted_mass'])}** de la masse totale de PageRank, soit "
                         f"{_pct(th['rerouted_share'])} du flux transporté par les liens ({th['edges_penalised']} arêtes "
                         f"affaiblies). Flux entre segments différents : {_pct(th['cross_segment_ref'])} sans pénalité, "
                         f"{_pct(th['cross_segment_eff'])} avec.\n")
                L.append("| Segment | Masse entrante sans pénalité | Masse entrante effective | Déperdition | Part |\n|---|---|---|---|---|")
                for r in sorted(an.seg_table, key=lambda r: -abs(r.get("thermal_loss", 0.0))):
                    L.append(f"| {r['segment']} | {_pct(r['thermal_in_ref'])} | {_pct(r['thermal_in_eff'])} | "
                             f"{100 * r['thermal_loss']:+.2f} pt | {100 * r['thermal_loss_share']:+.1f} % |")
                L.append("")

    L.append("## 3. Alignement business par segment\n")
    if an.value_col:
        L.append("Part de la valeur business contre part du PageRank. Un écart négatif = segment qui rapporte plus qu'il ne reçoit.\n")
    L.append("| Segment | Pages | Part du PR | Part de la valeur | Écart | Score médian | Fuite sortante | Famine | Gouffres |\n|---|---|---|---|---|---|---|---|---|")
    for r in sorted(an.seg_table, key=lambda r: -r["gap"]):
        L.append(f"| {r['segment']} | {r['pages']} | {_pct(r['pr_share'])} | {_pct(r['value_share']) if an.value_col else 'n/a'} | "
                 f"{('%+.1f pt' % (100 * r['gap'])) if an.value_col else 'n/a'} | {r['score_median']:.0f} | {_pct(r['leak_out_share'])} | "
                 f"{r['starving']} | {r['overfed']} |")
    L.append("")

    L.append("## 4. Famine : pages rentables ou stratégiques sous-alimentées\n")
    L.append(f"Critère : page dans les {int(an.thresholds['money_share'] * 100)} % cumulés de la valeur (ou déclarée stratégique) "
             f"ET score < {an.thresholds['starving_score']:.0f} (percentile {meta.get('starving_percentile', 40)}).\n")
    bcols = meta.get("business_cols") or []
    if an.value_col:
        L.append(f"Colonne de valeur retenue : {an.value_col}"
                 + (f" (colonnes disponibles : {', '.join(bcols)} ; priorité revenue > conversions > clicks > sessions, "
                    f"`--value-col` pour forcer)." if bcols else "."))
        if an.demand_col:
            L.append(f"Seules des données de recherche sont disponibles : lire **{an.demand_col} = demande** (ce que Google "
                     f"montre) et **clics = valeur réalisée** (ce que la page capte). La famine ci-dessous est calculée "
                     f"sur les clics ; le § 4 bis liste les pages à forte demande et PageRank faible, candidates à un "
                     f"renforcement même si elles captent peu aujourd'hui.")
        L.append("")
    if an.starving_list:
        L.append("| URL | Score | Part d'équité | Valeur | Liens entrants | Profondeur | Segment |\n|---|---|---|---|---|---|---|")
        for i in an.starving_list[:40]:
            L.append(f"| {urls[i]} | {score[i]:.0f} | {_pct(pr[i])} | {_num(an.value[i])} | {an.in_deg[i]} | {_depth(an.depth[i])} | {an.segments[i]} |")
    else:
        L.append("Aucune page en famine selon ce critère" + (" (pas de données business fournies : seules les pages stratégiques sont évaluées)." if not an.value_col else "."))
    L.append("")

    if an.demand_col and an.demand is not None:
        L.append(f"## 4 bis. Demande non servie : {an.demand_col} fortes, PageRank faible\n")
        L.append(f"Pages dans les {int(an.thresholds['money_share'] * 100)} % cumulés des {an.demand_col}, score < "
                 f"{an.thresholds['starving_score']:.0f}, hors pages déjà en famine. Google les montre, le site ne les "
                 f"soutient pas : un gain de maillage y est le plus directement mesurable (clics, position).\n")
        if an.demand_list:
            L.append(f"| URL | Score | {an.demand_col} | {an.value_col} | Liens entrants | Profondeur | Segment |\n|---|---|---|---|---|---|---|")
            for i in an.demand_list[:40]:
                L.append(f"| {urls[i]} | {score[i]:.0f} | {_num(an.demand[i])} | {_num(an.value[i])} | {an.in_deg[i]} | "
                         f"{_depth(an.depth[i])} | {an.segments[i]} |")
        else:
            L.append("Aucune page ne cumule une forte demande et un PageRank faible en dehors de la famine.")
        L.append("")

    L.append("## 5. Gouffres : pages utilitaires sur-alimentées\n")
    L.append(f"Critère : URL utilitaire (regex) ET score ≥ {an.thresholds['overfed_score']:.0f} (percentile {meta.get('overfed_percentile', 80)}).\n")
    if an.overlinked:
        L.append("| URL | Score | Part d'équité | Liens entrants | Valeur |\n|---|---|---|---|---|")
        for i in an.overlinked[:25]:
            mark = " 🔴" if an.overfed[i] else ""
            L.append(f"| {urls[i]}{mark} | {score[i]:.0f} | {_pct(pr[i])} | {an.in_deg[i]} | {an.value[i]:.4g} |")
    else:
        L.append("Aucune URL ne correspond au motif utilitaire.")
    L.append("")

    L.append("## 6. Trous noirs : culs-de-sac les mieux alimentés\n")
    L.append("Pages sans lien sortant qui absorbent du PageRank. Leur masse est réinjectée selon la politique choisie, "
             "mais le jus cesse d'irriguer leur voisinage éditorial.\n")
    if an.black_holes:
        L.append("| URL | Score | Liens entrants | Segment |\n|---|---|---|---|")
        for i in an.black_holes[:25]:
            L.append(f"| {urls[i]} | {score[i]:.0f} | {an.in_deg[i]} | {an.segments[i]} |")
    else:
        L.append("Aucun cul-de-sac.")
    L.append("")

    L.append("## 7. Étanchéité des silos\n")
    L.append("Part du flux sortant de chaque segment qui part vers un autre segment (liens transversaux).\n")
    S = len(an.segment_names)
    # Matrice limitée aux SILO_MATRIX_MAX segments de plus grand PageRank, le reste regroupé en « autres » :
    # une matrice de 28 × 28 ne se lit dans aucun lecteur Markdown. Le détail complet est dans flows_segments.csv.
    order_seg = sorted(range(S), key=lambda s: -an.seg_table[s]["pr_share"]) if an.seg_table else list(range(S))
    shown = order_seg[:SILO_MATRIX_MAX]
    others = order_seg[SILO_MATRIX_MAX:]
    groups = [[s] for s in shown] + ([others] if others else [])
    labels = [an.segment_names[s] for s in shown] + (["autres"] if others else [])
    if others:
        L.append(f"Matrice limitée aux {len(shown)} segments de plus grand PageRank ; les {len(others)} autres sont "
                 "regroupés dans « autres ». Détail complet segment par segment dans `flows_segments.csv`.\n")
    L.append("| Depuis \\ vers | " + " | ".join(labels) + " |")
    L.append("|---|" + "---|" * len(labels))
    for gi, grp in enumerate(groups):
        rows = an.seg_flows[np.asarray(grp)].sum(axis=0) if S else np.zeros(0)
        tot = rows.sum()
        cells = [(_pct(rows[np.asarray(gj)].sum() / tot) if tot > 0 else "n/a") for gj in groups]
        L.append(f"| **{labels[gi]}** | " + " | ".join(cells) + " |")
    L.append("")

    L.append("## 8. Quadrants PageRank × CheiRank\n")
    counts = np.bincount(an.quadrant.astype(np.int64), minlength=4)
    L.append("Coupe à la médiane des deux scores : par construction, la moitié du site est « faible » sur chaque axe. "
             "Un quadrant décrit une position dans le graphe, jamais une décision.\n")
    L.append("| Quadrant | Lecture | Pages |\n|---|---|---|")
    L.append(f"| Puits (PR fort, CheiRank faible) | pages de destination : normal pour des pages de vente | {counts[0]} |")
    L.append(f"| Hubs (PR fort, CheiRank fort) | catégories mères qui reçoivent et redistribuent | {counts[1]} |")
    L.append(f"| Passoires (PR faible, CheiRank fort) | pages pauvres à nombreux liens sortants | {counts[2]} |")
    L.append(f"| Peu reliées et peu reliantes (PR faible, CheiRank faible) | à instruire avec les clics et impressions avant toute décision | {counts[3]} |")
    L.append("")

    if an.cannibal_groups:
        L.append("## 8 bis. Arbitrage des cannibalisations\n")
        L.append("Ratio PR(leader) / PR(seconde). En dessous de 5, l'ascendant structurel n'est pas net.\n")
        L.append("| Groupe | Leader structurel | Ratio | Tranché ? | Pages |\n|---|---|---|---|---|")
        for c in an.cannibal_groups:
            ratio = "∞" if c["ratio"] == float("inf") else f"{c['ratio']:.1f}"
            L.append(f"| {c['group']} | {urls[c['leader']]} | {ratio} | {'oui' if c['decided'] else 'non'} | "
                     + ", ".join(urls[i] for i in c["pages"]) + " |")
        L.append("")

    L.append("## 9. Limites et contrôles non faits\n")
    L.append("- Les poids par position sont des heuristiques ; Google n'a pas publié les siens [Non vérifié].")
    L.append("- Les orphelines listées sont un **plancher** : un crawl ne trouve pas ses propres orphelines. "
             "Fournir `--urls` avec l'inventaire complet (sitemap, logs, GSC) pour les vraies.")
    L.append("- Pas de rendu JavaScript : le graphe reflète les liens de l'export fourni.")
    if clean_stats.get("uncrawled_nodes"):
        L.append(f"- {clean_stats['uncrawled_nodes']} URL découvertes n'ont pas été crawlées (budget de pages) : elles reçoivent du PageRank "
                 "mais n'en redistribuent pas, ce qui gonfle la masse des culs-de-sac. Relancer avec un budget supérieur.")
    if wg.get("embeddings"):
        L.append("- La composante sémantique dépend des vecteurs fournis (modèle, découpage) : la pénalité est une "
                 "fonction documentée, pas une reconstitution de Google [Non vérifié]. Pas de décroissance temporelle.")
    else:
        L.append("- Pas de composante sémantique (Topic-Sensitive, option `--embeddings`) ni de décroissance temporelle.")
    if wg.get("transitions"):
        L.append("- Les clics observés décrivent le design en place (piège de circularité) et ne voient que le trafic mesuré "
                 "(consentement, referrer) : d'où le plancher heuristique, jamais 100 % donnée.")
    else:
        L.append("- Pondération par position seule : deux liens en même position sont indiscernables (option `--transitions`).")
    if not an.value_col:
        L.append("- Aucune donnée business fournie : l'alignement valeur ↔ PageRank n'a pas pu être mesuré.")
    if not pr_info.get("converged"):
        L.append("- La puissance itérée n'a pas convergé sous la tolérance : augmenter `--max-iter`.")
    L.append("")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def write_actions_csv(path: str, actions: list[dict]) -> None:
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticket", "priorite", "famille", "titre", "cibles", "probleme", "action", "gain_pts_money_acquisition",
                    "gain", "liens_retires", "liens_ajoutes", "pages_emettrices"])
        for a in actions:
            w.writerow(_row([a["id"], a["priority"], a["family"], a["title"], " | ".join(a["target_urls"]), a["problem"], a["action"],
                             a["gain_pts"], a["gain_text"], a.get("removed", 0), a.get("added", 0), " | ".join(a.get("donor_urls", []))]))


def write_excel(path: str, out_dir: str) -> None:
    """Classeur mis en forme pour Excel (onglets Synthèse, Plan d'action, Pages...) : `prgraph.workbook`.
    Repli sur le classeur brut si le module n'est pas disponible."""
    try:
        from .workbook import write_workbook
    except ImportError:
        write_workbook = None
    if write_workbook is not None:
        write_workbook(path, out_dir)
        return
    import pandas as pd
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, fn in (("Pages", "pages.csv"), ("Segments", "segments.csv"), ("Flux segments", "flows_segments.csv")):
            p = os.path.join(out_dir, fn)
            if os.path.exists(p):
                df = pd.read_csv(p)
                # openpyxl stocke toute chaîne commençant par « = » comme formule : on neutralise les colonnes texte
                # avant l'écriture, même si le CSV source l'a déjà fait (la fonction est idempotente).
                for col in df.columns:
                    if df[col].dtype == object:
                        df[col] = df[col].map(neutralise_cell)
                df.to_excel(xw, sheet_name=name, index=False)
