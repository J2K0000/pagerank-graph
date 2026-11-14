# -*- coding: utf-8 -*-
import io
import json

import numpy as np
import pytest

from prgraph import cli
from prgraph.actions import build_plan, write_dev_brief
from prgraph.analysis import (AnalysisConfig, alignment_score, analyse, assign_segments, classify,
                              compile_class_rules, guess_home)
from prgraph.clean import clean
from prgraph.graph import Graph
from prgraph.loaders import RawEdge, UrlInfo, parse_weights
from prgraph.pagerank import cheirank, log_score, power_iteration

W = parse_weights("raisonnable")


def E(s, t, pos="content"):
    return RawEdge(source=s, target=t, position=pos, nofollow=False, anchor="")


def test_classify_default_rules():
    urls = ["https://s.com/", "https://s.com/blog/post", "https://s.com/privacy-policy", "https://s.com/pricing",
            "https://s.com/customer/acme", "https://s.com/zzz"]
    cls = classify(urls, 0, compile_class_rules())
    assert cls == ["accueil", "acquisition", "legal_support", "money", "acquisition", "neutre"]
    cls2 = classify(urls, 0, compile_class_rules(), mapping={"https://s.com/zzz": "money"})
    assert cls2[-1] == "money"
    strategic = np.zeros(len(urls), dtype=bool)
    strategic[1] = True
    assert classify(urls, 0, compile_class_rules(), strategic=strategic)[1] == "money"


def test_alignment_score_bounds():
    pr = np.array([0.5, 0.5])
    assert alignment_score(pr, ["money", "legal_support"], None)["score"] < 100
    # Cible par défaut 0.4.0 : money 0,38, acquisition 0,38, confiance 0,04, accueil 0,08 (exclu de la distance), neutre 0,12.
    perfect = alignment_score(np.array([0.38, 0.38, 0.04, 0.08, 0.12]),
                              ["money", "acquisition", "confiance", "accueil", "neutre"], None)
    assert perfect["score"] == pytest.approx(100.0)
    assert perfect["home_excluded"] is True
    # L'accueil n'entre pas dans la distance : lui donner tout le jus ne change pas le score des autres classes.
    skewed = alignment_score(np.array([0.19, 0.19, 0.02, 0.54, 0.06]),
                             ["money", "acquisition", "confiance", "accueil", "neutre"], None)
    assert skewed["score"] == pytest.approx(100.0)
    vs = np.array([0.0, 1.0])
    with_value = alignment_score(pr, ["money", "acquisition"], vs)
    assert with_value["basis"] == "valeur business" and with_value["score"] == pytest.approx(50.0)


def site_with_waste():
    b = "https://s.com"
    pages = [b + p for p in ("/", "/pricing", "/demo", "/blog/a", "/blog/b", "/blog/c", "/privacy-policy", "/legal")]
    pages += [b + f"/blog/p{k}" for k in range(30)]
    edges = []
    for p in pages:
        for u in ("/privacy-policy", "/legal"):
            edges.append(E(p, b + u, "footer"))
        for u in ("/", "/pricing", "/blog/a"):
            edges.append(E(p, b + u, "header"))
    for k in range(30):
        edges.append(E(b + "/blog/a", b + f"/blog/p{k}"))
        edges.append(E(b + f"/blog/p{k}", b + "/blog/b"))
    edges += [E(b + "/blog/b", b + "/blog/c"), E(b + "/", b + "/demo", "content")]
    table = {p: UrlInfo(p, 200) for p in pages}
    business = {b + "/demo": {"revenue": 5000.0}, b + "/pricing": {"revenue": 3000.0}, b + "/blog/c": {"revenue": 900.0}}
    return pages, edges, table, business


def test_plan_ranks_template_waste_first_and_simulates_gains():
    pages, edges, table, business = site_with_waste()
    cg = clean(iter(edges), table, W, boilerplate_share=0)
    g = Graph.from_clean(cg)
    pr = power_iteration(g).pr
    ch = cheirank(g).pr
    score, cscore = log_score(pr), log_score(ch)
    home = guess_home(g.urls)
    segs = assign_segments(g.urls, cg.given_segments, [], {})
    an = analyse(g, pr, score, ch, cscore, segs, business, "revenue", None, None, home, AnalysisConfig())
    assert an.alignment["score"] < 100 and an.alignment["waste_share"] > 0.05
    actions = build_plan(g, pr, score, an, W, cg.stats)
    assert actions, "le plan doit contenir des actions"
    fams = {a["family"] for a in actions}
    assert "gouffre" in fams
    first = actions[0]
    assert first["id"] == "SEO-01" and first["priority"] in ("critique", "moyen")
    glob = next(a for a in actions if a["family"] == "gouffre" and a["scope"] == "global")
    # 0.4.0 : le gain affiché est celui de l'action prescrite (liens déplacés en pied de page, ou regroupés
    # derrière une page centre légal), jamais celui d'une suppression pure.
    assert glob["gain_pts"] > 0 and (glob.get("moved", 0) > 0 or glob["removed"] > 0)
    assert "suppression" not in glob["title"].lower()
    assert "masquer" in glob["action"] and "obfuscation" in glob["action"]
    fam = [a for a in actions if a["family"] == "famine"]
    if fam:
        assert fam[0]["score_after"] > fam[0]["score_before"] and fam[0]["donor_urls"]
    ids = [a["id"] for a in actions]
    assert ids == sorted(ids)


def test_dev_brief_and_outputs(tmp_path):
    out = tmp_path / "demo"
    rc = cli.main(["demo", "--out", str(out), "--products", "300", "--run"])
    assert rc == 0
    o = out / "out"
    assert (o / "actions.csv").exists() and (o / "brief_dev.md").exists()
    md = io.open(o / "brief_dev.md", encoding="utf-8").read()
    assert md.startswith("Outil : prgraph ") and "### Ticket SEO-01" in md and "\u2014" not in md
    data = json.load(io.open(o / "graph.json", encoding="utf-8"))
    assert "alignment" in data and 0 <= data["alignment"]["score"] <= 100
    assert data["actions"] and all("targets" in a and "gain_text" in a for a in data["actions"])
    assert "cls" in data["meta"]["node_fields"]
    findings = io.open(o / "findings.md", encoding="utf-8").read()
    assert "## 0. Verdict" in findings and "Score d'alignement business" in findings
    header = io.open(o / "pages.csv", encoding="utf-8-sig").readline()
    assert ",classe," in header


def test_no_actions_flag(tmp_path):
    out = tmp_path / "demo"
    cli.main(["demo", "--out", str(out), "--products", "200"])
    o = out / "na"
    rc = cli.main(["compute", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"), "--no-actions", "--out", str(o)])
    assert rc == 0 and not (o / "actions.csv").exists()
    data = json.load(io.open(o / "graph.json", encoding="utf-8"))
    assert data["actions"] == [] and data["alignment"]["basis"] == "cible par défaut"


def test_diff_detects_lost_links_and_regressions(tmp_path):
    import csv
    from prgraph.diff import diff_runs, write_diff_md
    before, after = tmp_path / "a", tmp_path / "b"
    for d in (before, after):
        d.mkdir()
    def write(d, pages, edges):
        with io.open(d / "pages.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh); w.writerow(["url", "score", "in_links", "classe", "flags"]); w.writerows(pages)
        with io.open(d / "edges.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh); w.writerow(["source", "target", "position"]); w.writerows(edges)
    write(before, [["https://s.com/a", "60", "3", "money", "strategique"], ["https://s.com/b", "30", "2", "acquisition", ""]],
          [["https://s.com/b", "https://s.com/a", "content"], ["https://s.com/", "https://s.com/b", "content"]])
    write(after, [["https://s.com/a", "40", "2", "money", "strategique"], ["https://s.com/b", "45", "3", "acquisition", ""]],
          [["https://s.com/", "https://s.com/b", "content"], ["https://s.com/a", "https://s.com/b", "content"]])
    d = diff_runs(str(before), str(after))
    assert "https://s.com/a" in d["protected"]
    assert len(d["regressions"]) == 1 and d["regressions"][0][0] == "https://s.com/a"
    assert d["lost_to_protected"] == [("https://s.com/b", "https://s.com/a", "content")]
    assert len(d["new_edges"]) == 1
    write_diff_md(str(after / "diff.md"), d)
    md = io.open(after / "diff.md", encoding="utf-8").read()
    assert "🚨" in md and "\u2014" not in md


def test_strategic_targets_become_chosen_actions(tmp_path):
    out = tmp_path / "demo"
    cli.main(["demo", "--out", str(out), "--products", "300"])
    # une page produit ordinaire déclarée stratégique doit apparaître comme « cible choisie »
    import csv
    rows = list(csv.DictReader(io.open(out / "urls.csv", encoding="utf-8")))
    target = next(r["url"] for r in rows if "/p/" in r["url"])
    strat = out / "strat.csv"
    io.open(strat, "w", encoding="utf-8").write(f"url,poids\n{target},3\n")
    o = out / "chosen"
    rc = cli.main(["compute", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"), "--strategic", str(strat), "--out", str(o)])
    assert rc == 0
    data = json.load(io.open(o / "graph.json", encoding="utf-8"))
    chosen = [a for a in data["actions"] if a["family"] == "cible"]
    assert chosen and chosen[0]["target_urls"] == [target] and chosen[0]["effort"]["type"] == "editorial"
    assert any(a.get("quick_win") for a in data["actions"])
    assert all("effort" in a and "gain_per_effort" in a for a in data["actions"])


def test_resolve_strategic_matches_paths_and_regex(tmp_path):
    from prgraph.server import resolve_strategic
    urls = tmp_path / "urls.csv"
    io.open(urls, "w", encoding="utf-8").write("url,status\nhttps://s.com/\nhttps://s.com/offres/a,200\nhttps://s.com/offres/b,200\n"
                                               "https://s.com/blog/x,200\nhttps://s.com/old,301\n")
    got = resolve_strategic(["/blog/x", "offres/.*", "https://s.com/nope", "/old"], str(urls), "https://s.com")
    assert got == ["https://s.com/blog/x", "https://s.com/offres/a", "https://s.com/offres/b"]
    assert resolve_strategic([], str(urls), "https://s.com") == []


def test_prescription_computes_link_count_donors_and_anchors(tmp_path):
    out = tmp_path / "demo"
    cli.main(["demo", "--out", str(out), "--products", "300"])
    import csv
    # première passe sans cible pour choisir la fiche produit la moins alimentée
    o0 = out / "base"
    assert cli.main(["compute", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"), "--no-actions", "--out", str(o0)]) == 0
    pages = list(csv.DictReader(io.open(o0 / "pages.csv", encoding="utf-8-sig")))
    target = min((r for r in pages if "/p/" in r["url"]), key=lambda r: float(r["score"]))["url"]
    strat = out / "strat.csv"
    io.open(strat, "w", encoding="utf-8").write(f"url,poids\n{target},3\n")
    o = out / "pres"
    assert cli.main(["compute", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"), "--strategic", str(strat), "--out", str(o)]) == 0
    data = json.load(io.open(o / "graph.json", encoding="utf-8"))
    a = next(x for x in data["actions"] if x["family"] == "cible")
    p = a["prescription"]
    assert p["objective"] > p["score_before"] and p["objective_label"]
    assert len(p["steps"]) >= 2, "deux liens minimum pour une cible choisie"
    assert all(st["anchor"] and st["position"] == "content" and st["score_after"] >= p["score_before"] for st in p["steps"])
    assert p["anchors"][0] and "en savoir plus" not in " ".join(p["anchors"]).lower()
    assert a["effort"]["units"] == len(p["steps"])
    md = io.open(o / "brief_dev.md", encoding="utf-8").read()
    assert "Liens à poser" in md and "Ancres déclarées" in md


def _write_run(d, pages, edges=None):
    import csv
    d.mkdir(exist_ok=True)
    with io.open(d / "pages.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh); w.writerow(["url", "score", "in_links", "classe", "flags"]); w.writerows(pages)
    if edges is not None:
        with io.open(d / "edges.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh); w.writerow(["source", "target", "position"]); w.writerows(edges)


def test_diff_sans_edges_est_incomplet(tmp_path):
    """edges.csv absent : jamais « ✅ » ni « 0 lien disparu » en silence, code de sortie 2."""
    from prgraph.diff import diff_runs, exit_code, write_diff_md
    pages = [["https://s.com/a", "60", "3", "money", "strategique"]]
    _write_run(tmp_path / "a", pages, [["https://s.com/b", "https://s.com/a", "content"]])
    _write_run(tmp_path / "b", pages)
    d = diff_runs(str(tmp_path / "a"), str(tmp_path / "b"))
    assert d["edges_missing"] == [str(tmp_path / "b")] and exit_code(d) == 2
    write_diff_md(str(tmp_path / "b" / "diff.md"), d)
    md = io.open(tmp_path / "b" / "diff.md", encoding="utf-8").read()
    assert md.startswith("Outil : prgraph ") and "Comparaison incomplète" in md and "✅" not in md
    assert cli.main(["diff", "--before", str(tmp_path / "a"), "--after", str(tmp_path / "b")]) == 2


def test_diff_dossier_sans_pages_csv(tmp_path):
    """Un dossier de crawl (edges.csv seul) n'est pas une analyse : message clair, code 2, pas de trace Python."""
    import pytest
    from prgraph.diff import DiffInputError, diff_runs
    _write_run(tmp_path / "a", [["https://s.com/a", "60", "3", "money", "strategique"]], [])
    (tmp_path / "b").mkdir()
    with pytest.raises(DiffInputError):
        diff_runs(str(tmp_path / "a"), str(tmp_path / "b"))
    assert cli.main(["diff", "--before", str(tmp_path / "a"), "--after", str(tmp_path / "b")]) == 2


def test_diff_page_protegee_absente(tmp_path):
    """Recette sur un autre hôte ou URL changée : la page protégée manque, c'est une alarme, pas un ✅."""
    from prgraph.diff import diff_runs, exit_code
    _write_run(tmp_path / "a", [["https://s.com/a", "60", "3", "money", "strategique"]], [])
    _write_run(tmp_path / "b", [["https://recette.s.com/a", "60", "3", "money", "strategique"]], [])
    d = diff_runs(str(tmp_path / "a"), str(tmp_path / "b"))
    assert d["protected_missing"] == ["https://s.com/a"] and d["n_common"] == 0 and exit_code(d) == 1
