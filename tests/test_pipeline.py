# -*- coding: utf-8 -*-
import csv
import io
import json
import os

import numpy as np
import pytest

from prgraph import cli
from prgraph.analysis import AnalysisConfig, analyse, assign_segments, click_depth, guess_home
from prgraph.clean import clean
from prgraph.graph import Graph
from prgraph.loaders import RawEdge, UrlInfo, detect_edge_format, parse_weights, read_business, read_edges
from prgraph.pagerank import cheirank, log_score, power_iteration
from prgraph.simulate import Rules, apply_rules

W = parse_weights("raisonnable")


def E(s, t, pos="content", nofollow=False):
    return RawEdge(source=s, target=t, position=pos, nofollow=nofollow, anchor="")


def small_site():
    b = "https://s.com"
    pages = [b + p for p in ("/", "/c/a/", "/c/b/", "/p/1/", "/p/2/", "/p/3/", "/mentions-legales/", "/cgv/")]
    pages += [b + f"/p/{k}/" for k in range(4, 10)]        # six produits frères bien maillés, p/3 reste isolé
    edges = []
    for p in pages:
        for u in ("/mentions-legales/", "/cgv/"):
            edges.append(E(p, b + u, "footer"))
        for c in ("/c/a/", "/c/b/"):
            edges.append(E(p, b + c, "header"))
        edges.append(E(p, b + "/", "header"))
    edges += [E(b + "/c/a/", b + "/p/1/"), E(b + "/c/a/", b + "/p/2/"), E(b + "/c/b/", b + "/p/3/"),
              E(b + "/p/1/", b + "/p/2/"), E(b + "/p/2/", b + "/p/1/"), E(b + "/p/3/", b + "/c/b/")]
    for k in range(4, 10):
        edges.append(E(b + "/c/b/", b + f"/p/{k}/"))
        for j in range(4, 10):
            if j != k:
                edges.append(E(b + f"/p/{k}/", b + f"/p/{j}/"))
    table = {p: UrlInfo(p, 200) for p in pages}
    return pages, edges, table


def run_analysis(edges, table, business=None, value_col=None, strategic=None):
    cg = clean(iter(edges), table, W)
    g = Graph.from_clean(cg)
    pr = power_iteration(g).pr
    ch = cheirank(g).pr
    score, cscore = log_score(pr), log_score(ch)
    home = guess_home(g.urls)
    segs = assign_segments(g.urls, cg.given_segments, [], {})
    an = analyse(g, pr, score, ch, cscore, segs, business, value_col, strategic, None, home, AnalysisConfig())
    return cg, g, pr, score, an


def test_click_depth_and_segments():
    pages, edges, table = small_site()
    cg, g, pr, score, an = run_analysis(edges, table)
    assert an.home == g.urls.index("https://s.com/")
    d = {g.urls[i]: int(an.depth[i]) for i in range(g.n)}
    assert d["https://s.com/"] == 0 and d["https://s.com/c/a/"] == 1 and d["https://s.com/p/1/"] == 2
    assert an.segments[g.urls.index("https://s.com/p/1/")] == "p"
    assert an.segments[an.home] == "accueil"
    assert an.utility[g.urls.index("https://s.com/cgv/")]
    assert not an.utility[an.home]


def test_business_alignment_flags_starving_money_page():
    pages, edges, table = small_site()
    business = {"https://s.com/p/3/": {"revenue": 10000.0}, "https://s.com/p/1/": {"revenue": 100.0}}
    cg, g, pr, score, an = run_analysis(edges, table, business, "revenue")
    p3 = g.urls.index("https://s.com/p/3/")
    assert an.money[p3]
    assert an.value_share[p3] == pytest.approx(10000 / 10100)
    # p/3 n'a qu'un lien éditorial entrant : score bas, donc en famine
    assert an.starving[p3]
    seg_p = next(r for r in an.seg_table if r["segment"] == "p")
    assert seg_p["value_share"] == pytest.approx(1.0)
    assert seg_p["gap"] > 0


def test_zone_flows_sum_to_one_and_segment_flows_consistent():
    pages, edges, table = small_site()
    cg, g, pr, score, an = run_analysis(edges, table)
    assert sum(an.zone_flows.values()) == pytest.approx(1.0, abs=1e-9)
    assert an.seg_flows.sum() == pytest.approx(sum(v for k, v in an.zone_flows.items() if k not in ("teleportation", "fuites_et_culs_de_sac")), abs=1e-9)


def test_simulation_remove_footer_moves_equity_away_from_legal_pages():
    pages, edges, table = small_site()
    cg = clean(iter(edges), table, W)
    g = Graph.from_clean(cg)
    before = power_iteration(g).pr
    g2, st = apply_rules(g, Rules(remove_positions=["footer"]), W)
    assert st["removed"] == 2 * len(pages) - 2   # moins les deux auto-liens fusionnés
    after = power_iteration(g2).pr
    legal = g.urls.index("https://s.com/mentions-legales/")
    assert after[legal] < before[legal] * 0.5
    assert after.sum() == pytest.approx(1.0)


def test_simulation_add_edges_and_cap():
    pages, edges, table = small_site()
    g = Graph.from_clean(clean(iter(edges), table, W))
    rules = Rules(add_edges=[("https://s.com/", "https://s.com/p/3/", "content"), ("https://s.com/", "https://s.com/nope/", "content")],
                  cap_position=("header", 1))
    g2, st = apply_rules(g, rules, W)
    assert st["added"] == 1 and st["added_unknown_url"] == 1
    hdr = np.bincount(g2.src[g2.pos == 3], minlength=g2.n)   # 3 = header dans POSITIONS
    assert hdr.max() <= 1


def test_screaming_frog_format(tmp_path):
    p = tmp_path / "inlinks.csv"
    with io.open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Type", "Source", "Destination", "Size (Bytes)", "Alt Text", "Anchor", "Status Code", "Status", "Follow", "Target", "Rel", "Path Type", "Link Path", "Link Position", "Link Origin"])
        w.writerow(["Hyperlink", "https://s.com/", "https://s.com/a", "", "", "A", "200", "OK", "True", "", "", "Absolute", "/html/body/main/a", "Content", "HTML"])
        w.writerow(["Hyperlink", "https://s.com/", "https://s.com/legal", "", "", "Legal", "200", "OK", "False", "", "nofollow", "Absolute", "/html/body/footer/a", "Footer", "HTML"])
        w.writerow(["Hyperlink", "https://s.com/a", "https://s.com/old", "", "", "Old", "404", "Not Found", "True", "", "", "Absolute", "/html/body/main/a", "Content", "HTML"])
        w.writerow(["Image", "https://s.com/a", "https://s.com/img.png", "", "x", "", "200", "OK", "True", "", "", "Absolute", "", "Content", "HTML"])
        w.writerow(["Hyperlink", "https://s.com/a", "https://s.com/", "", "", "Home", "200", "OK", "True", "", "", "Absolute", "/html/body/nav/a", "Navigation", "HTML"])
    assert detect_edge_format(["type", "source", "destination"]) == "screamingfrog"
    it, fmt = read_edges(str(p))
    rows = list(it)
    assert fmt == "screamingfrog" and len(rows) == 4
    assert rows[1].nofollow and rows[1].position == "footer"
    assert rows[2].target_status == 404 and rows[3].position == "nav"
    cg = clean(iter(rows), {}, W)
    assert cg.stats["lost_404"] == 1 and cg.stats["nofollow"] == 1
    assert set(cg.urls) == {"https://s.com/", "https://s.com/a"}


def test_business_gsc_export(tmp_path):
    p = tmp_path / "gsc.csv"
    io.open(p, "w", encoding="utf-8").write("Pages les plus populaires,Clics,Impressions,CTR,Position\nhttps://s.com/a,1 234,56 789,2.17%,8.4\n")
    data, cols = read_business(str(p))
    assert cols == ["clicks", "impressions"]
    assert data["https://s.com/a"]["clicks"] == 1234.0


def test_end_to_end_demo(tmp_path):
    out = tmp_path / "demo"
    rc = cli.main(["demo", "--out", str(out), "--products", "300", "--run"])
    assert rc == 0
    o = out / "out"
    for fn in ("pages.csv", "segments.csv", "flows_segments.csv", "findings.md", "graph.json", "report.html"):
        assert (o / fn).exists(), fn
    data = json.load(io.open(o / "graph.json", encoding="utf-8"))
    assert data["meta"]["n_nodes"] == len(data["nodes"]) and not data["meta"]["truncated"]
    assert abs(sum(n[2] for n in data["nodes"]) - 1.0) < 1e-6
    html = io.open(o / "report.html", encoding="utf-8").read()
    assert "/*__GRAPH_DATA__*/null" not in html and '"n_nodes"' in html
    md = io.open(o / "findings.md", encoding="utf-8").read()
    assert md.startswith("Outil : prgraph ") and "## 4. Famine" in md
    assert "\u2014" not in md     # pas de tiret cadratin dans un livrable


def test_truncated_export_keeps_anomalies(tmp_path):
    out = tmp_path / "demo"
    cli.main(["demo", "--out", str(out), "--products", "300"])
    o = out / "small"
    rc = cli.main(["compute", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"), "--business", str(out / "business.csv"),
                   "--segments", str(out / "segments.tsv"), "--max-nodes", "120", "--out", str(o)])
    assert rc == 0
    data = json.load(io.open(o / "graph.json", encoding="utf-8"))
    assert data["meta"]["truncated"] and len(data["nodes"]) <= 140
    flags = [n[10] for n in data["nodes"]]
    assert any(f & 8 for f in flags) and any(f & 1 for f in flags)     # famine et accueil présents


def test_simulate_command(tmp_path):
    out = tmp_path / "demo"
    cli.main(["demo", "--out", str(out), "--products", "200"])
    o = out / "sim"
    rc = cli.main(["simulate", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"), "--business", str(out / "business.csv"),
                   "--segments", str(out / "segments.tsv"), "--remove-position", "footer", "--cap-position", "header:20", "--out", str(o)])
    assert rc == 0
    md = io.open(o / "simulation.md", encoding="utf-8").read()
    assert "Simulation" in md and "Plus fortes hausses" in md
    header = io.open(o / "pages.csv", encoding="utf-8-sig").readline()
    assert "sim_score" in header and "delta_score" in header
