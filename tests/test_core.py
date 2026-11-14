# -*- coding: utf-8 -*-
import math

import networkx as nx
import numpy as np
import pytest

from prgraph.clean import clean, normalize_url
from prgraph.graph import Graph
from prgraph.loaders import RawEdge, UrlInfo, parse_weights, normalize_position
from prgraph.pagerank import cheirank, log_score, power_iteration

W = parse_weights("raisonnable")


def E(s, t, pos="content", nofollow=False, anchor=""):
    return RawEdge(source=s, target=t, position=pos, nofollow=nofollow, anchor=anchor)


def build(edges, table=None, **kw):
    cg = clean(iter(edges), table or {}, W, **kw)
    return cg, Graph.from_clean(cg)


def test_normalize_url():
    assert normalize_url("HTTPS://Example.com:443/A/b#frag") == "https://example.com/A/b"
    assert normalize_url("http://x.com") == "http://x.com/"
    assert normalize_url("http://x.com/p?b=2") == "http://x.com/p?b=2"
    assert normalize_url("http://x.com/p?b=2", strip_query=True) == "http://x.com/p"


def test_position_aliases():
    assert normalize_position("Navigation") == "nav"
    assert normalize_position("Pied de page") == "footer"
    assert normalize_position("Fil d'Ariane") == "breadcrumb"
    assert normalize_position("weird") == "unknown"


def test_pagerank_matches_networkx_weighted():
    # Graphe pondéré sans cul-de-sac : doit coïncider avec nx.pagerank(weight="weight").
    edges = [E("a", "b", "content"), E("a", "c", "footer"), E("b", "c", "content"),
             E("c", "a", "nav"), E("b", "a", "breadcrumb")]
    cg, g = build(edges)
    res = power_iteration(g, alpha=0.85, tol=1e-12)
    assert res.converged
    G = nx.DiGraph()
    for s, d, w in zip(g.src, g.dst, g.w):
        G.add_edge(g.urls[s], g.urls[d], weight=float(w))
    ref = nx.pagerank(G, alpha=0.85, weight="weight", tol=1e-12)
    for i, u in enumerate(g.urls):
        assert res.pr[i] == pytest.approx(ref[u], abs=1e-6)


def test_dangling_mass_is_conserved():
    edges = [E("a", "b"), E("b", "c")]        # c est un cul-de-sac
    cg, g = build(edges)
    res = power_iteration(g)
    assert res.pr.sum() == pytest.approx(1.0, abs=1e-12)
    assert res.dangling_mass > 0
    # Redistribution vers l'accueil : a reçoit plus que dans le cas uniforme
    res_home = power_iteration(g, dangling="home", home=g.urls.index("a"))
    assert res_home.pr[g.urls.index("a")] > res.pr[g.urls.index("a")]


def test_redirect_chain_and_canonical_resolution():
    table = {
        "http://s.com/old": UrlInfo("http://s.com/old", 301, redirect="http://s.com/mid"),
        "http://s.com/mid": UrlInfo("http://s.com/mid", 301, redirect="http://s.com/new"),
        "http://s.com/new": UrlInfo("http://s.com/new", 200),
        "http://s.com/dup": UrlInfo("http://s.com/dup", 200, canonical="http://s.com/new"),
        "http://s.com/home": UrlInfo("http://s.com/home", 200),
        "http://s.com/gone": UrlInfo("http://s.com/gone", 404),
    }
    edges = [E("http://s.com/home", "http://s.com/old"), E("http://s.com/home", "http://s.com/dup"),
             E("http://s.com/home", "http://s.com/gone"), E("http://s.com/dup", "http://s.com/home"),
             E("http://s.com/new", "http://s.com/home")]
    cg, g = build(edges, table)
    assert set(g.urls) == {"http://s.com/home", "http://s.com/new"}
    # home -> old et home -> dup fusionnent vers new ; home -> gone est une fuite
    assert g.m == 2
    assert cg.stats["lost_404"] == 1
    assert cg.stats["duplicates_collapsed"] == 1
    assert cg.stats["dropped_source_not_canonical"] == 1   # dup n'émet pas de liens
    h = g.urls.index("http://s.com/home")
    assert g.leaked_out[h] == pytest.approx(1.0)
    assert cg.stats["redirect_chains_resolved"] >= 2


def test_multi_edge_collapse_keeps_most_visible_position():
    edges = [E("a", "b", "footer", anchor="footer anchor"), E("a", "b", "content", anchor="content anchor"),
             E("a", "c", "nav")]
    cg, g = build(edges)
    ab = [i for i in range(g.m) if g.urls[g.src[i]] == "a" and g.urls[g.dst[i]] == "b"]
    assert len(ab) == 1
    assert g.w[ab[0]] == pytest.approx(1.0)
    assert cg.anchors[ab[0]] == "footer anchor"     # première ancre rencontrée


def test_nofollow_is_wasted_not_redistributed_to_siblings():
    # a → b (follow) et a → c (nofollow). b doit recevoir la moitié du jus de a, pas la totalité.
    edges = [E("a", "b"), E("a", "c", nofollow=True), E("b", "a"), E("c", "a")]
    cg, g = build(edges)
    out = g.out_sum()
    a = g.urls.index("a")
    assert out[a] == pytest.approx(2.0)
    assert g.leak_fraction()[a] == pytest.approx(0.5)


def test_log_score_bounds():
    pr = np.array([1e-6, 1e-4, 1e-2])
    s = log_score(pr)
    assert s[0] == pytest.approx(0.0) and s[2] == pytest.approx(100.0)
    assert s[1] == pytest.approx(50.0)
    assert log_score(np.array([0.5, 0.5])).tolist() == [100.0, 100.0]


def test_cheirank_is_pagerank_of_reversed_graph():
    edges = [E("hub", "p1"), E("hub", "p2"), E("hub", "p3"), E("p1", "hub")]
    cg, g = build(edges)
    pr = power_iteration(g).pr
    ch = cheirank(g).pr
    hub = g.urls.index("hub")
    # Le hub diffuse plus qu'il ne reçoit : CheiRank élevé, PR moyen.
    assert ch[hub] == max(ch)
    assert pr[g.urls.index("p2")] == pytest.approx(pr[g.urls.index("p3")])


def test_weighted_vs_uniform_changes_footer_target():
    # 1 lien footer + 8 liens contextuels : le modèle naïf surévalue la cible du footer.
    edges = [E("home", "legal", "footer")] + [E("home", f"p{i}", "content") for i in range(8)]
    edges += [E(f"p{i}", "home") for i in range(8)] + [E("legal", "home")]
    cg_w, g_w = build(edges)
    cg_u = clean(iter(edges), {}, parse_weights("uniform"))
    g_u = Graph.from_clean(cg_u)
    pw = power_iteration(g_w).pr[g_w.urls.index("legal")]
    pu = power_iteration(g_u).pr[g_u.urls.index("legal")]
    assert pu / pw > 2.5


def test_inventory_nodes_without_links_are_orphans():
    table = {"http://s.com/": UrlInfo("http://s.com/", 200), "http://s.com/a": UrlInfo("http://s.com/a", 200),
             "http://s.com/orphan": UrlInfo("http://s.com/orphan", 200)}
    edges = [E("http://s.com/", "http://s.com/a"), E("http://s.com/a", "http://s.com/")]
    cg, g = build(edges, table)
    assert "http://s.com/orphan" in g.urls
    assert g.in_degree()[g.urls.index("http://s.com/orphan")] == 0


def test_footer_sans_balise_footer():
    """Webflow : <article class="footer"> et <div class="footer_wrapper"> sont un pied de page,
    même sous <main> ou avec un <nav> de langues ; « card-footer » reste du contenu."""
    from bs4 import BeautifulSoup
    from prgraph.crawl_static import detect_position
    html = ('<main><article class="footer"><div class="footer_wrapper"><a id="a" href="/privacy">p</a>'
            '<nav class="dropdown-lang-list"><a id="b" href="/fr">fr</a></nav></div></article>'
            '<article><div class="card-footer"><a id="c" href="/x">x</a></div></article></main>')
    s = BeautifulSoup(html, "html.parser")
    assert detect_position(s.find(id="a")) == "footer"
    assert detect_position(s.find(id="b")) == "footer"
    assert detect_position(s.find(id="c")) == "content"


def test_bloc_contenus_associes():
    """Liste CMS « Other articles » sous le contenu : barre latérale (0,25), pas lien dans le texte."""
    from bs4 import BeautifulSoup
    from prgraph.crawl_static import detect_position
    html = ('<main><div class="rt-blog"><p><a id="a" href="/x">x</a></p></div>'
            '<div class="other-articles-wrapper"><div class="w-dyn-list"><a id="b" href="/y">y</a></div></div></main>'
            '<div class="my---container"><div class="related-posts"><a id="c" href="/z">z</a></div></div>')
    s = BeautifulSoup(html, "html.parser")
    assert detect_position(s.find(id="a")) == "content"
    assert detect_position(s.find(id="b")) == "sidebar"
    assert detect_position(s.find(id="c")) == "sidebar"


def test_classe_de_body_ne_decide_pas_de_la_zone():
    """<body class="footer-sticky"> décrit la mise en page : les liens du texte restent du contenu."""
    from bs4 import BeautifulSoup
    from prgraph.crawl_static import detect_position
    html = ('<html class="footer-fixed"><body class="footer-sticky has-sidebar"><main><p><a id="a" href="/x">x</a></p></main>'
            '<div class="footer_wrapper"><a id="b" href="/p">p</a></div></body></html>')
    s = BeautifulSoup(html, "html.parser")
    assert detect_position(s.find(id="a")) == "content"
    assert detect_position(s.find(id="b")) == "footer"
