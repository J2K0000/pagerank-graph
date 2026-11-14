# -*- coding: utf-8 -*-
"""Pondération des arêtes par les clics observés et par la similarité sémantique."""
import csv
import io
import json

import numpy as np
import pytest

from prgraph import cli, demo
from prgraph.analysis import AnalysisConfig, analyse, assign_segments, guess_home
from prgraph.clean import clean
from prgraph.graph import Graph
from prgraph.loaders import RawEdge, UrlInfo, parse_weights
from prgraph.pagerank import cheirank, log_score, power_iteration
from prgraph.weights import (apply_semantic, apply_transitions, read_embeddings, read_transitions)

W = parse_weights("raisonnable")


def E(s, t, pos="content", nofollow=False):
    return RawEdge(source=s, target=t, position=pos, nofollow=nofollow, anchor="")


def build(edges, table=None):
    cg = clean(iter(edges), table or {}, W)
    return cg, Graph.from_clean(cg)


def edge_w(g, s, t):
    si, ti = g.urls.index(s), g.urls.index(t)
    m = (g.src == si) & (g.dst == ti)
    assert m.sum() == 1
    return float(g.w[m][0])


# ── Clics observés ────────────────────────────────────────────────────────────

def test_read_transitions_accepts_transitions_or_count(tmp_path):
    p = tmp_path / "t.csv"
    io.open(p, "w", encoding="utf-8").write("source,target,transitions\nhttps://s.com/a,https://s.com/b,80\nhttps://s.com/a,https://s.com/c,20\nhttps://s.com/a,https://s.com/b,5\n")
    obs = read_transitions(str(p))
    assert obs[("https://s.com/a", "https://s.com/b")] == 85.0
    assert obs[("https://s.com/a", "https://s.com/c")] == 20.0
    q = tmp_path / "u.csv"
    io.open(q, "w", encoding="utf-8").write("source;target;count\nhttps://s.com/a;https://s.com/b;3\n")
    assert read_transitions(str(q)) == {("https://s.com/a", "https://s.com/b"): 3.0}


def test_blend_refuses_full_data():
    cg, g = build([E("https://s.com/a", "https://s.com/b")])
    with pytest.raises(ValueError, match="circularit"):
        apply_transitions(g, {("https://s.com/a", "https://s.com/b"): 1.0}, blend=1.0)
    with pytest.raises(ValueError):
        apply_transitions(g, {}, blend=-0.1)


def test_blend_twins_and_floor():
    a, b, c, d, z = (f"https://s.com/{x}/" for x in "abcdz")
    # a : deux liens de contenu jumeaux (b, c) et un lien footer (d) ; z : page sans donnée.
    edges = [E(a, b), E(a, c), E(a, d, "footer"), E(z, b), E(z, c, "footer")]
    cg, g = build(edges)
    obs = {(a, b): 80.0, (a, c): 20.0, ("https://s.com/nope/", b): 7.0}
    g2, st = apply_transitions(g, obs, blend=0.7)
    S = 1.0 + 1.0 + 0.05
    wb, wc, wd = edge_w(g2, a, b), edge_w(g2, a, c), edge_w(g2, a, d)
    assert wb == pytest.approx(S * (0.7 * 0.8 + 0.3 * 1.0 / S))
    assert wc == pytest.approx(S * (0.7 * 0.2 + 0.3 * 1.0 / S))
    assert wb > wc
    # Arête sans clic sur une page qui en a : plancher (1 − blend) × poids heuristique.
    assert wd == pytest.approx(0.3 * 0.05)
    # La masse sortante de a est conservée (la part de fuite ne bouge pas).
    assert wb + wc + wd == pytest.approx(S)
    # Page sans donnée : heuristique intacte.
    assert edge_w(g2, z, b) == 1.0 and edge_w(g2, z, c) == 0.05
    assert g2.w.sum() == pytest.approx(g.w.sum())
    assert st["blend"] == 0.7
    assert st["edges_total"] == 5 and st["edges_covered"] == 2
    assert st["edges_covered_share"] == pytest.approx(2 / 5)
    assert st["mass_covered_share"] == pytest.approx(2.0 / g.w.sum())
    assert st["sources_with_data"] == 1 and st["sources_total"] == 2
    assert st["pairs_read"] == 3 and st["pairs_matched"] == 2 and st["pairs_unmatched"] == 1


# ── Composante sémantique ─────────────────────────────────────────────────────

def test_read_embeddings_two_formats(tmp_path):
    p = tmp_path / "wide.csv"
    io.open(p, "w", encoding="utf-8").write("url,d1,d2,d3\nhttps://s.com/a,1,0,0\nhttps://s.com/b,0.5,0.5,0\n")
    emb = read_embeddings(str(p))
    assert set(emb) == {"https://s.com/a", "https://s.com/b"}
    assert emb["https://s.com/b"].tolist() == [0.5, 0.5, 0.0]
    q = tmp_path / "json.csv"
    io.open(q, "w", encoding="utf-8").write('url,vector\nhttps://s.com/a,"[1, 0, 0]"\nhttps://s.com/b,"[0, 1, 0]"\n')
    emb2 = read_embeddings(str(q))
    assert emb2["https://s.com/b"].tolist() == [0.0, 1.0, 0.0]
    with pytest.raises(ValueError):
        io.open(tmp_path / "bad.csv", "w", encoding="utf-8").write("url,vector\nhttps://s.com/a,\"[1,0]\"\nhttps://s.com/b,\"[1,0,0]\"\n")
        read_embeddings(str(tmp_path / "bad.csv"))


def test_semantic_penalty_renormalises_per_source():
    a, b, c, d, n = (f"https://s.com/{x}/" for x in ("a", "b", "c", "d", "novec"))
    edges = [E(a, b), E(a, c), E(a, n), E(d, c)]
    cg, g = build(edges)
    emb = {a: np.array([1.0, 0.0]), b: np.array([1.0, 0.0]), c: np.array([0.0, 1.0]),
           d: np.array([-1.0, 0.0])}
    g2, st, factor = apply_semantic(g, emb, decay=0.5)
    # cos(a,b) = 1 → facteur 1 ; cos(a,c) = 0 → facteur 0 ; n sans vecteur → facteur 1.
    fb, fc, fn = (factor[(g.src == g.urls.index(a)) & (g.dst == g.urls.index(x))][0] for x in (b, c, n))
    assert fb == 1.0 and fc == 0.0 and fn == 1.0
    # Renormalisation : a émettait 3, émet toujours 3, répartis sur b et n.
    assert edge_w(g2, a, b) == pytest.approx(1.5) and edge_w(g2, a, n) == pytest.approx(1.5)
    assert edge_w(g2, a, c) == 0.0
    # d → c : cos négatif → facteur 0, mais seule arête de d : poids d'origine conservé.
    assert edge_w(g2, d, c) == 1.0
    assert st["decay"] == 0.5 and st["nodes_embedded"] == 4 and st["nodes_total"] == 5
    assert st["edges_scored"] == 3 and st["edges_zeroed"] == 2 and st["sources_all_zero"] == 1
    with pytest.raises(ValueError):
        apply_semantic(g, emb, decay=-1)
    # decay plus fort = pénalité plus forte sur un cos intermédiaire.
    emb2 = {**emb, c: np.array([1.0, 1.0])}
    _, _, f1 = apply_semantic(g, emb2, decay=0.5)
    _, _, f2 = apply_semantic(g, emb2, decay=2.0)
    ic = (g.src == g.urls.index(a)) & (g.dst == g.urls.index(c))
    assert 0 < f2[ic][0] < f1[ic][0] < 1


def test_thermal_loss_by_segment():
    a, b, c = (f"https://s.com/{x}/" for x in ("shop/a", "blog/x", "shop/y"))
    # a (shop) lie un article de blog hors sujet et un produit du même silo ; les deux renvoient vers a.
    edges = [E(a, b), E(a, c), E(b, a), E(c, a)]
    cg, g = build(edges)
    emb = {a: np.array([0.0, 1.0]), b: np.array([1.0, 0.0]), c: np.array([0.0, 1.0])}
    g2, st, _ = apply_semantic(g, emb, decay=0.5)
    pr = power_iteration(g2).pr
    assert np.isfinite(pr).all() and pr.sum() == pytest.approx(1.0)
    ch = cheirank(g2).pr
    assert np.isfinite(ch).all()          # une arête de poids nul ne produit pas de NaN dans le graphe inversé
    segs = assign_segments(g2.urls, cg.given_segments, [], {})
    an = analyse(g2, pr, log_score(pr), ch, log_score(ch), segs, None, None, None, None, guess_home(g2.urls),
                 AnalysisConfig(), w_ref=g.w)
    assert an.thermal is not None
    rows = {r["segment"]: r for r in an.seg_table}
    # Sans pénalité, a aurait envoyé la moitié de sa masse vers blog ; avec, tout reste dans shop.
    assert rows["blog"]["thermal_loss"] > 0 and rows["shop"]["thermal_loss"] < 0
    assert rows["blog"]["thermal_in_eff"] == pytest.approx(0.0)
    assert rows["blog"]["thermal_loss_share"] == pytest.approx(1.0)
    assert sum(r["thermal_loss"] for r in an.seg_table) == pytest.approx(0.0, abs=1e-12)
    assert an.thermal["rerouted_mass"] == pytest.approx(rows["blog"]["thermal_loss"])
    assert an.thermal["cross_segment_eff"] < an.thermal["cross_segment_ref"]
    assert an.thermal["edges_penalised"] == 1
    # Sans w_ref : pas de mesure.
    an0 = analyse(g2, pr, log_score(pr), ch, log_score(ch), segs, None, None, None, None, guess_home(g2.urls), AnalysisConfig())
    assert an0.thermal is None and "thermal_loss" not in an0.seg_table[0]


# ── Bout en bout sur la démo ──────────────────────────────────────────────────

def _pr_by_url(out_dir):
    with io.open(out_dir / "pages.csv", encoding="utf-8-sig", newline="") as fh:
        return {r["url"]: float(r["pagerank"]) for r in csv.DictReader(fh)}


def test_end_to_end_weighting_options(tmp_path):
    out = tmp_path / "demo"
    info = demo.generate(str(out), seed=42, n_products=300)
    assert (out / "transitions.csv").exists() and (out / "embeddings.csv").exists()
    base_args = ["compute", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"),
                 "--business", str(out / "business.csv"), "--segments", str(out / "segments.tsv")]
    assert cli.main(base_args + ["--out", str(out / "base")]) == 0
    assert cli.main(base_args + ["--transitions", str(out / "transitions.csv"), "--data-blend", "0.7", "--out", str(out / "clicks")]) == 0
    assert cli.main(base_args + ["--embeddings", str(out / "embeddings.csv"), "--semantic-decay", "0.5", "--out", str(out / "sem")]) == 0
    assert cli.main(base_args + ["--transitions", str(out / "transitions.csv"), "--embeddings", str(out / "embeddings.csv"),
                                 "--out", str(out / "both")]) == 0
    base, clicks, sem, both = (_pr_by_url(out / d) for d in ("base", "clicks", "sem", "both"))

    # Un lien très cliqué transmet plus que son jumeau en même position jamais cliqué.
    hot, cold = info["twins"]
    assert clicks[hot] > base[hot] * 1.2
    assert clicks[cold] < base[cold]
    assert clicks[hot] / clicks[cold] > 3 * (base[hot] / base[cold])

    # Les liens parasites entre silos transmettent moins avec la composante sémantique :
    # la part de la masse sortante de la source portée par chaque lien parasite baisse.
    def edge_shares(d):
        data = json.load(io.open(out / d / "graph.json", encoding="utf-8"))
        ids = {n[0]: i for i, n in enumerate(data["nodes"])}
        out_w, ew = {}, {}
        for s, t, w, _ in data["edges"]:
            out_w[s] = out_w.get(s, 0.0) + w
            ew[(s, t)] = w
        return {(s, t): ew[(ids[s], ids[t])] / out_w[ids[s]] for s, t in info["parasites"] if (ids[s], ids[t]) in ew}, data
    sh_base, _ = edge_shares("base")
    sh_sem, d_sem = edge_shares("sem")
    sh_clicks, _ = edge_shares("clicks")
    sh_both, d_both = edge_shares("both")
    assert len(sh_base) >= 3
    assert all(sh_sem[k] < sh_base[k] for k in sh_base)
    assert all(sh_both[k] < sh_clicks[k] for k in sh_clicks)
    assert d_sem["thermal"]["cross_segment_eff"] < d_sem["thermal"]["cross_segment_ref"]

    # Rapport, graph.json et visualiseur portent les paramètres et la déperdition.
    md = io.open(out / "both" / "findings.md", encoding="utf-8").read()
    assert "Couverture" in md and "perdition thermique" in md and "\u2014" not in md
    data = json.load(io.open(out / "both" / "graph.json", encoding="utf-8"))
    wg = data["meta"]["weighting"]
    assert wg["transitions"]["blend"] == 0.7 and wg["embeddings"]["decay"] == 0.5
    assert 0 < wg["transitions"]["edges_covered_share"] < 1
    assert data["thermal"]["rerouted_mass"] > 0
    assert all("thermal_loss" in s for s in data["segments"])
    html = io.open(out / "both" / "report.html", encoding="utf-8").read()
    assert "weighting" in html
    base_data = json.load(io.open(out / "base" / "graph.json", encoding="utf-8"))
    assert base_data["meta"].get("weighting") is None and base_data.get("thermal") is None

    # blend = 1.0 refusé explicitement.
    with pytest.raises(SystemExit):
        cli.main(base_args + ["--transitions", str(out / "transitions.csv"), "--data-blend", "1.0", "--out", str(out / "bad")])


def test_anciens_noms_de_profils_acceptes():
    """brief et library restent lus comme raisonnable et modere."""
    assert parse_weights("brief") == parse_weights("raisonnable")
    assert parse_weights("library") == parse_weights("modere")
    assert parse_weights("modere")["footer"] == 0.10
