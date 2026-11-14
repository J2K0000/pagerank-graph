# -*- coding: utf-8 -*-
"""Tests de non-régression des corrections 0.4.0 : classification par segments, fuites externes et non
crawlées, inférence de gabarit graduelle et protégée, déplacement de liens, téléportation conservée,
nombres à séparateur de milliers, trous noirs, erreurs d'entrée, diff sur la part d'équité, version."""
import importlib.metadata
import io
import os

import numpy as np
import pytest

import prgraph
from prgraph import cli
from prgraph.analysis import AnalysisConfig, analyse, assign_segments, class_of, guess_home, is_utility
from prgraph.clean import clean, same_site
from prgraph.graph import Graph
from prgraph.loaders import RawEdge, UrlInfo, _to_float, comma_is_thousands, parse_weights, read_business, read_url_table
from prgraph.pagerank import cheirank, log_score, power_iteration
from prgraph.simulate import Rules, apply_rules

W = parse_weights("raisonnable")


def E(s, t, pos="content", nofollow=False):
    return RawEdge(source=s, target=t, position=pos, nofollow=nofollow, anchor="")


# ── PR-02 / PR-13 : classes par segments, pas par sous-chaînes ─────────────────

@pytest.mark.parametrize("path,expected", [
    ("/blog/legal-tech-trends", "acquisition"),
    ("/blog/shadow-accounting", "acquisition"),
    ("/blog/best-sales-commission-jobs", "acquisition"),
    ("/fr/blog/conditions-admissibilite-primes", "acquisition"),
    ("/glossary/account-executive", "acquisition"),
    ("/legal", "legal_support"),
    ("/legal-center", "legal_support"),
    ("/privacy-policy", "legal_support"),
    ("/fr/politique-de-confidentialite", "legal_support"),
    ("/terms-of-use", "legal_support"),
    ("/cart", "legal_support"),
    ("/jobs", "legal_support"),
    ("/about-us", "confiance"),
    ("/fr/a-propos", "confiance"),
    ("/press", "confiance"),
    ("/faq", "confiance"),
    ("/pricing", "money"),
    ("/contact", "money"),
    ("/acme-vs-competitor", "money"),
    ("/sales-compensation-platform", "money"),
    ("/product/widget", "money"),
    ("/customer/acme", "acquisition"),
    ("/ebook/guide-2026", "acquisition"),
    ("/something-else", "neutre"),
    ("/", "accueil"),
    ("/fr/", "accueil"),
    ("/vitamin-c", "neutre"),
    ("/p-value-explained", "neutre"),
    ("/p/widget", "money"),
    ("/job-board-software", "money"),
    ("/privacy-first-analytics", "neutre"),
    ("/cookie-recipes", "neutre"),
    ("/cookie-policy", "legal_support"),
    ("/legal-tech", "neutre"),
    ("/Pricing/", "money"),
    ("/pricing.html", "money"),
    ("/pricing?plan=pro", "money"),
])
def test_class_of_uses_path_segments(path, expected):
    assert class_of("https://s.fr" + path) == expected


def test_utility_never_matches_inside_a_blog_slug():
    assert not is_utility("https://s.fr/blog/modification-sales-commission-legal-obligations")
    assert not is_utility("https://s.fr/blog/shadow-accounting")
    assert is_utility("https://s.fr/legal-center")
    assert is_utility("https://s.fr/fr/panier")
    assert not is_utility("https://s.fr/about-us")     # confiance, pas un gouffre
    assert is_utility("https://s.fr/blog/x", extra_regex="blog")   # regex explicite de l'utilisateur respectée


# ── PR-05 : redirection vers un autre hôte = fuite ─────────────────────────────

def test_external_redirect_is_a_leak_not_a_node():
    table = {"https://s.fr/": UrlInfo("https://s.fr/", 200), "https://s.fr/a": UrlInfo("https://s.fr/a", 200),
             "https://s.fr/b": UrlInfo("https://s.fr/b", 301, redirect="https://autre.fr/")}
    cg = clean(iter([E("https://s.fr/", "https://s.fr/a"), E("https://s.fr/", "https://s.fr/b"), E("https://s.fr/a", "https://s.fr/")]),
               table, W)
    assert "https://autre.fr/" not in cg.urls
    assert cg.stats["lost_external"] == 1
    assert cg.leaked_out[cg.urls.index("https://s.fr/")] == pytest.approx(1.0)
    assert same_site("www.s.fr", "s.fr") and same_site("blog.s.fr", "www.s.fr") and not same_site("autre.fr", "s.fr")


# ── PR-06 : cibles non crawlées = fuite par défaut, nœuds sur demande ───────────

def test_uncrawled_targets_leak_by_default():
    table = {f"https://s.fr/p{i}": UrlInfo(f"https://s.fr/p{i}", 200) for i in range(3)}
    edges = [E(f"https://s.fr/p{i}", f"https://s.fr/p{(i + 1) % 3}") for i in range(3)]
    edges += [E("https://s.fr/p0", f"https://s.fr/x{j}") for j in range(50)]
    cg = clean(iter(edges), dict(table), W)
    assert cg.stats["lost_uncrawled"] == 50 and cg.n == 3
    cg2 = clean(iter(edges), dict(table), W, uncrawled="nodes")
    assert cg2.n == 53 and cg2.stats["uncrawled_nodes"] == 50
    with pytest.raises(ValueError):
        clean(iter(edges), dict(table), W, uncrawled="autre")


def test_robots_status_is_not_a_200(tmp_path):
    p = tmp_path / "urls.csv"
    p.write_text("url,status\nhttps://s.fr/,200\nhttps://s.fr/secret,robots\nhttps://s.fr/late,uncrawled\n", encoding="utf-8")
    rows = {r.url: r for r in read_url_table(str(p))}
    assert rows["https://s.fr/secret"].status == 0 and rows["https://s.fr/late"].status == 0
    table = {u: r for u, r in rows.items()}
    cg = clean(iter([E("https://s.fr/", "https://s.fr/secret")]), table, W)
    assert "https://s.fr/secret" not in cg.urls and cg.stats["lost_404"] == 1


# ── PR-01 : inférence de gabarit graduelle, visible, et jamais sur une page money ──

def _pillar_site(n_articles=24, n_citing=21):
    urls = [f"https://s.fr/blog/article-{i}" for i in range(n_articles)] + ["https://s.fr/pricing"]
    edges = []
    for i in range(n_articles):
        edges.append(E(urls[i], urls[(i + 1) % n_articles]))
        if i < n_citing:
            edges.append(E(urls[i], "https://s.fr/pricing"))
    return urls, edges, {u: UrlInfo(u, 200) for u in urls}


def test_boilerplate_inference_counts_editorial_links_only():
    # Une page de menu liée en header depuis 30 pages et citée 3 fois dans un texte : les 3 liens éditoriaux
    # gardent leur plein poids, l'inférence ne compte que les positions content / unknown.
    urls = [f"https://s.fr/a{i}" for i in range(30)] + ["https://s.fr/menu-page"]
    edges = [E(u, "https://s.fr/menu-page", "header") for u in urls[:30]]
    edges += [E(urls[i], "https://s.fr/menu-page", "content") for i in range(3)]
    edges += [E(urls[i], urls[(i + 1) % 30]) for i in range(30)]
    cg = clean(iter(edges), {u: UrlInfo(u, 200) for u in urls}, W)     # seuil par défaut : 20 pages
    tgt = cg.urls.index("https://s.fr/menu-page")
    assert cg.stats["boilerplate_reclassified"] == 0
    assert float(cg.w[cg.dst == tgt].max()) == 1.0


def test_uncrawled_leak_does_not_depend_on_line_order():
    table = {"https://s.fr/a": UrlInfo("https://s.fr/a", 200)}
    e1 = [E("https://s.fr/a", "https://s.fr/x"), E("https://s.fr/x", "https://s.fr/a")]
    cg1 = clean(iter(e1), dict(table), W)
    cg2 = clean(iter(list(reversed(e1))), dict(table), W)
    assert cg1.stats["lost_uncrawled"] == cg2.stats["lost_uncrawled"] == 1
    assert cg1.stats["edges_out"] == cg2.stats["edges_out"]


def test_boilerplate_inference_spares_money_pages_and_lists_targets():
    urls, edges, table = _pillar_site()
    cg = clean(iter(edges), dict(table), W, protect=lambda u: class_of(u) == "money")
    tgt = cg.urls.index("https://s.fr/pricing")
    assert cg.stats["boilerplate_reclassified"] == 0 and cg.stats["boilerplate_protected"] == 1
    assert all(float(w) == 1.0 for w in cg.w[cg.dst == tgt])
    cg2 = clean(iter(edges), dict(table), W)            # sans protection : requalifiée, mais graduellement
    w2 = sorted(set(float(w) for w in cg2.w[cg2.dst == tgt]))
    share = 21 / 24
    assert cg2.stats["boilerplate_reclassified"] == 21
    assert w2 == [pytest.approx(max(W["boilerplate"], 1.0 - share))]
    assert cg2.stats["boilerplate_target_list"][0][0] == "https://s.fr/pricing"
    assert cg2.stats["boilerplate_target_list"][0][1] == 21
    assert cg2.stats["boilerplate_threshold"] >= 20


# ── PR-03 : déplacer des liens (le lien reste, son poids change) ────────────────

def test_move_rule_changes_weight_not_count():
    table = {u: UrlInfo(u, 200) for u in ("https://s.fr/", "https://s.fr/legal", "https://s.fr/a")}
    cg = clean(iter([E("https://s.fr/", "https://s.fr/legal", "header"), E("https://s.fr/", "https://s.fr/a"),
                     E("https://s.fr/a", "https://s.fr/legal", "nav")]), table, W, boilerplate_share=0)
    g = Graph.from_clean(cg)
    g2, st = apply_rules(g, Rules(move_positions=["header", "nav"], move_positions_target="legal$", move_to="footer"), W)
    assert st["moved"] == 2 and st["removed"] == 0 and g2.m == g.m
    legal = g.urls.index("https://s.fr/legal")
    assert all(float(w) == W["footer"] for w in g2.w[g2.dst == legal])
    pr1, pr2 = power_iteration(g).pr, power_iteration(g2).pr
    assert pr2[legal] < pr1[legal]


# ── CODE-03 : la téléportation survit à simulate ────────────────────────────────

def test_simulate_without_rules_keeps_restart_vector(tmp_path):
    b = "https://x.fr"
    edges = tmp_path / "edges.csv"
    edges.write_text("source,target,position,nofollow,anchor\n" + "\n".join(
        f"{b}/{s},{b}/{t},content,0," for s, t in (("", "a/"), ("", "b/"), ("a/", "b/"), ("b/", ""), ("a/", ""))), encoding="utf-8")
    restart = tmp_path / "restart.csv"
    restart.write_text(f"url,poids\n{b}/a/,10\n{b}/,1\n", encoding="utf-8")
    out = tmp_path / "sim"
    rc = cli.main(["simulate", "--edges", str(edges), "--restart", str(restart), "--out", str(out), "--no-actions"])
    assert rc == 0
    rows = [l.split(",") for l in io.open(out / "pages.csv", encoding="utf-8-sig").read().splitlines()]
    header = rows[0]
    i_pr, i_sim = header.index("pagerank"), header.index("sim_pagerank")
    for r in rows[1:]:
        # pages.csv arrondit les colonnes de simulation à six décimales : tolérance de lecture, pas de modèle
        assert float(r[i_pr]) == pytest.approx(float(r[i_sim]), rel=1e-4)


# ── CODE-07 : nombres anglais à séparateur de milliers ──────────────────────────

def test_thousands_separator_decided_per_column(tmp_path):
    assert _to_float("12,5") == 12.5 and _to_float("1 234,5") == 1234.5
    assert _to_float("1,234", thousands_comma=True) == 1234.0
    assert comma_is_thousands(["1,234", "1,234,567"]) and comma_is_thousands(["1,234", "12.5"])
    assert not comma_is_thousands(["12,5", "0,05"]) and not comma_is_thousands(["1,234"])
    p = tmp_path / "gsc.csv"
    p.write_text('url,clicks,impressions\nhttps://s.fr/,"1,234","12,345,678"\nhttps://s.fr/a,"56","789"\n', encoding="utf-8")
    # ambigu ligne à ligne, mais la colonne impressions a deux groupes : virgule = milliers pour toute la colonne
    data, cols = read_business(str(p))
    assert data["https://s.fr/"]["impressions"] == 12345678.0
    assert data["https://s.fr/"]["clicks"] == 1234.0          # même export, même locale : milliers partout
    # Export anglais sans colonne à deux groupes : une métrique de comptage « 1,234 » reste un millier
    q = tmp_path / "gsc2.csv"
    q.write_text('url,clicks\nhttps://s.fr/,"1,234"\nhttps://s.fr/a,"56"\n', encoding="utf-8")
    data2, _ = read_business(str(q))
    assert data2["https://s.fr/"]["clicks"] == 1234.0
    # Export français : une décimale à un ou deux chiffres dans la colonne impose la virgule décimale
    f = tmp_path / "fr.csv"
    f.write_text('url,revenue\nhttps://s.fr/,"1234,5"\nhttps://s.fr/a,"12,5"\n', encoding="utf-8")
    data3, _ = read_business(str(f))
    assert data3["https://s.fr/"]["revenue"] == 1234.5 and data3["https://s.fr/a"]["revenue"] == 12.5


def test_weights_override_error_is_a_message():
    with pytest.raises(ValueError, match="poids invalide"):
        parse_weights("raisonnable", "footer=abc")


# ── PR-09 : trous noirs = pages réellement alimentées, jamais des fichiers ──────

def test_black_holes_exclude_files_and_unfed_pages():
    b = "https://s.fr"
    pages = [b + "/", b + "/a", b + "/b", b + "/c", b + "/doc.pdf", b + "/dead", b + "/orphan-2"]
    edges = [E(b + "/", b + "/a"), E(b + "/", b + "/b"), E(b + "/a", b + "/b"), E(b + "/b", b + "/a"), E(b + "/a", b + "/c"),
             E(b + "/b", b + "/c"), E(b + "/", b + "/c")] + [E(p, b + "/doc.pdf") for p in (b + "/", b + "/a", b + "/b")] \
            + [E(b + "/", b + "/dead")]
    table = {p: UrlInfo(p, 200) for p in pages}
    cg = clean(iter(edges), table, W, boilerplate_share=0)
    g = Graph.from_clean(cg)
    pr, ch = power_iteration(g).pr, cheirank(g).pr
    an = analyse(g, pr, log_score(pr), ch, log_score(ch), assign_segments(g.urls, cg.given_segments, [], {}),
                 None, None, None, None, guess_home(g.urls), AnalysisConfig())
    holes = {g.urls[i] for i in an.black_holes}
    assert b + "/c" in holes
    assert b + "/doc.pdf" not in holes and b + "/orphan-2" not in holes


# ── CODE-12 : une position inconnue est une erreur, pas un « unknown » silencieux ──

def test_unknown_position_option_fails_loudly(tmp_path):
    edges = tmp_path / "e.csv"
    edges.write_text("source,target,position,nofollow,anchor\nhttps://x.fr/,https://x.fr/a,content,0,\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="position « foo » inconnue"):
        cli.main(["simulate", "--edges", str(edges), "--remove-position", "foo", "--out", str(tmp_path / "o"), "--no-actions"])
    with pytest.raises(SystemExit, match="cap-position"):
        cli.main(["simulate", "--edges", str(edges), "--cap-position", "nav30", "--out", str(tmp_path / "o"), "--no-actions"])


def test_generic_edges_with_relative_paths_do_not_crash(tmp_path):
    edges = tmp_path / "rel.csv"
    edges.write_text("source,target\n/,/a\n/a,/b\n/b,/\n", encoding="utf-8")
    rc = cli.main(["compute", "--edges", str(edges), "--out", str(tmp_path / "o"), "--no-actions"])
    assert rc == 0 and (tmp_path / "o" / "pages.csv").exists()


# ── PR-07 : diff compare la part d'équité et le rang, pas seulement le score ─────

def test_diff_flags_share_drop_even_when_score_is_stable(tmp_path):
    from prgraph.diff import diff_runs
    before, after = tmp_path / "b", tmp_path / "a"
    before.mkdir(), after.mkdir()
    head = "url,score,pagerank,in_links,flags,classe\n"
    (before / "pages.csv").write_text(head + "https://s.fr/m,60,0.40,5,money,money\nhttps://s.fr/x,50,0.30,3,,neutre\n"
                                      "https://s.fr/y,40,0.30,2,,neutre\n", encoding="utf-8")
    (after / "pages.csv").write_text(head + "https://s.fr/m,60,0.20,5,money,money\nhttps://s.fr/x,50,0.40,3,,neutre\n"
                                     "https://s.fr/y,40,0.40,2,,neutre\n", encoding="utf-8")
    d = diff_runs(str(before), str(after))
    assert [m[0] for m in d["regressions"]] == ["https://s.fr/m"]
    assert d["regressions"][0][6] == pytest.approx(-0.5)        # part d'équité divisée par deux


# ── CODE-01 : une seule version ──────────────────────────────────────────────────

def test_version_matches_installed_metadata():
    assert importlib.metadata.version("pagerank-graph") == prgraph.__version__, (
        "métadonnées périmées : relancer « pip install -e . » après une montée de version")
