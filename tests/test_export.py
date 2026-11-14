# -*- coding: utf-8 -*-
"""Sorties d'export.py : injection sûre dans report.html, neutralisation des formules tableur,
vecteur de téléportation exporté, forme de findings.md (profondeur, barème, matrice des silos)."""
import csv
import io
import json
import os

import numpy as np
import pytest

from prgraph import export as ex
from prgraph.analysis import CLASS_NAMES
from tests.test_pipeline import E, W, run_analysis, small_site

HOSTILE_TITLE = "<!--<script>alert(1)//"
FORMULA_TITLE = '=HYPERLINK("http://evil.example/?x","Accueil")'
B = "https://s.com"


def site_with_unreachable_money_page():
    """small_site + deux pages qui se lient entre elles sans qu'aucun chemin n'y mène depuis l'accueil ;
    /x/1/ porte presque toute la valeur : money, en famine, profondeur -1 (inatteignable)."""
    pages, edges, table = small_site()
    extra = [B + "/x/1/", B + "/x/2/"]
    for p in extra:
        table[p] = type(table[pages[0]])(p, 200)
    edges += [E(B + "/x/1/", B + "/x/2/"), E(B + "/x/2/", B + "/x/1/"), E(B + "/x/1/", B + "/", "header")]
    pages = pages + extra
    business = {B + "/x/1/": {"revenue": 10000.0}, B + "/p/1/": {"revenue": 100.0}, B + "/p/2/": {"revenue": 50.0}}
    return pages, edges, table, business


def site_with_many_segments(k=14):
    """Un segment par dossier /sN/ : plus de SILO_MATRIX_MAX segments pour tester le regroupement « autres »."""
    pages, edges, table = small_site()
    for i in range(1, k + 1):
        p = B + f"/s{i}/page/"
        table[p] = type(table[pages[0]])(p, 200)
        edges += [E(B + "/", p), E(p, B + "/", "header")]
        pages.append(p)
    return pages, edges, table


def meta_for(**over):
    m = {"site": "s.com", "alpha": 0.85, "dangling": "uniform", "weights": W, "weights_profile": "raisonnable",
         "starving_percentile": 40, "overfed_percentile": 80}
    m.update(over)
    return m


def analysed(site):
    pages, edges, table, business = site
    cg, g, pr, score, an = run_analysis(edges, table, business, "revenue")
    return cg, g, pr, score, an


def graph_data(g, pr, score, an, titles, **kw):
    chei = np.ones(g.n) / g.n
    cscore = np.full(g.n, 50.0)
    return ex.build_graph_json(g, pr, score, chei, cscore, an, titles, meta_for(), **kw)


# ── report.html : injection sûre ─────────────────────────────────────────────

def test_hostile_title_keeps_script_elements_intact(tmp_path):
    cg, g, pr, score, an = analysed(site_with_unreachable_money_page())
    normal = ["Titre normal"] * g.n
    hostile = [HOSTILE_TITLE] * g.n
    ex.write_report_html(str(tmp_path / "ok.html"), graph_data(g, pr, score, an, normal))
    ex.write_report_html(str(tmp_path / "bad.html"), graph_data(g, pr, score, an, hostile))
    ok = io.open(tmp_path / "ok.html", encoding="utf-8").read()
    bad = io.open(tmp_path / "bad.html", encoding="utf-8").read()
    assert ok.count("<script") == bad.count("<script")
    assert ok.count("</script") == bad.count("</script")
    assert "<!--<script" not in bad and "\\u003c" in bad
    # La charge reste du JSON équivalent : les titres sont relus tels quels
    start = bad.index('<script id="graph-data"')
    payload = bad[bad.index(">", start) + 1: bad.index("</script>", start)]
    data = json.loads(payload)
    assert data["nodes"][0][12] == HOSTILE_TITLE


def test_json_for_script_escapes_every_dangerous_character():
    s = ex.json_for_script({"t": "<!-- & >   "})
    for ch in ("<", ">", "&", " ", " "):
        assert ch not in s
    assert json.loads(s) == {"t": "<!-- & >   "}


def test_missing_or_duplicated_marker_raises(tmp_path):
    tpl = tmp_path / "tpl.html"
    tpl.write_text("<html><script>var x = null;</script></html>", encoding="utf-8")
    with pytest.raises(ValueError):
        ex.write_report_html(str(tmp_path / "r.html"), {"meta": {}}, template_path=str(tpl))
    tpl.write_text(f"<script>{ex.GRAPH_DATA_MARKER}</script><script>{ex.GRAPH_DATA_MARKER}</script>", encoding="utf-8")
    with pytest.raises(ValueError):
        ex.write_report_html(str(tmp_path / "r.html"), {"meta": {}}, template_path=str(tpl))


# ── CSV et classeur : neutralisation des formules ────────────────────────────

def test_neutralise_cell_rules():
    assert ex.neutralise_cell(FORMULA_TITLE) == "'" + FORMULA_TITLE
    assert ex.neutralise_cell("+cmd") == "'+cmd" and ex.neutralise_cell("@SUM") == "'@SUM"
    assert ex.neutralise_cell("\tx") == "'\tx" and ex.neutralise_cell("\rx") == "'\rx"
    assert ex.neutralise_cell("-exploit") == "'-exploit"
    # Nombres formatés et valeurs non textuelles intactes
    for v in ("-0.0123", "+1.5", "-1", "1.2e-05", "+0.0400", "12,5 %"):
        assert ex.neutralise_cell(v) == v
    assert ex.neutralise_cell(-1) == -1 and ex.neutralise_cell(None) is None and ex.neutralise_cell("") == ""
    # Idempotente
    assert ex.neutralise_cell(ex.neutralise_cell(FORMULA_TITLE)) == "'" + FORMULA_TITLE


def test_formula_title_neutralised_in_csv_and_xlsx(tmp_path):
    cg, g, pr, score, an = analysed(site_with_unreachable_money_page())
    home = g.urls.index(B + "/")
    titles = ["Titre"] * g.n
    titles[home] = FORMULA_TITLE
    chei, cscore = np.ones(g.n) / g.n, np.full(g.n, 50.0)
    ex.write_pages_csv(str(tmp_path / "pages.csv"), g, pr, score, chei, cscore, an, titles)
    ex.write_segments_csv(str(tmp_path / "segments.csv"), an)
    ex.write_flows_csv(str(tmp_path / "flows_segments.csv"), an)
    rows = list(csv.DictReader(io.open(tmp_path / "pages.csv", encoding="utf-8-sig", newline="")))
    by_url = {r["url"]: r for r in rows}
    assert by_url[B + "/"]["title"] == "'" + FORMULA_TITLE
    assert "pr_share_pct" in rows[0]
    assert abs(sum(float(r["pr_share_pct"]) for r in rows) - 100.0) < 0.05
    # Les écarts négatifs des segments restent des nombres
    seg_rows = list(csv.DictReader(io.open(tmp_path / "segments.csv", encoding="utf-8-sig", newline="")))
    assert all(not r["gap_value_minus_pr"].startswith("'") for r in seg_rows)
    assert any(r["gap_value_minus_pr"].startswith("-") for r in seg_rows)

    openpyxl = pytest.importorskip("openpyxl")
    pytest.importorskip("pandas")
    ex.write_excel(str(tmp_path / "report.xlsx"), str(tmp_path))
    wb = openpyxl.load_workbook(tmp_path / "report.xlsx")
    ws = wb["Pages"]
    header = [c.value for c in ws[1]]
    # 0.5.0 : classeur mis en forme (prgraph.workbook), en-têtes en français ; le titre hostile reste du texte
    # grâce au préfixe d'apostrophe d'Excel (quotePrefix), sans apostrophe visible dans la valeur.
    col_t, col_u = header.index("Titre") + 1, header.index("URL") + 1
    found = False
    for row in ws.iter_rows(min_row=2):
        if row[col_u - 1].value == B + "/":
            cell = row[col_t - 1]
            assert cell.data_type == "s" and cell.value.lstrip("'").startswith("=HYPERLINK")
            assert cell.value.startswith("'") or cell.quotePrefix
            found = True
    assert found
    assert all(c.data_type != "f" for row in ws.iter_rows(min_row=2) for c in row)


def test_actions_csv_neutralised(tmp_path):
    actions = [{"id": "SEO-1", "priority": "moyen", "family": "famine", "title": "=cmd|' /C calc'!A0",
                "target_urls": [B + "/p/1/"], "problem": "-x", "action": "@x", "gain_pts": 1.5, "gain_text": "+1 pt",
                "removed": 0, "added": 2, "donor_urls": []}]
    ex.write_actions_csv(str(tmp_path / "actions.csv"), actions)
    r = next(csv.DictReader(io.open(tmp_path / "actions.csv", encoding="utf-8-sig", newline="")))
    assert r["titre"].startswith("'=") and r["probleme"] == "'-x" and r["action"] == "'@x"
    assert r["gain"] == "'+1 pt"      # texte qui commence par « + » et n'est pas un nombre : préfixé
    assert r["gain_pts_money_acquisition"] == "1.5"     # nombre : intact


# ── graph.json : téléportation et KPI ────────────────────────────────────────

def test_meta_teleport_exported_when_vector_given(tmp_path):
    cg, g, pr, score, an = analysed(site_with_unreachable_money_page())
    titles = [""] * g.n
    tp = np.zeros(g.n)
    tp[an.home] = 3.0
    tp[g.urls.index(B + "/p/1/")] = 1.0
    data = graph_data(g, pr, score, an, titles, teleport=tp)
    ex.write_json(str(tmp_path / "graph.json"), data)
    saved = json.load(io.open(tmp_path / "graph.json", encoding="utf-8"))
    t = saved["meta"]["teleport"]
    assert isinstance(t, list) and len(t) == len(saved["nodes"]) == g.n
    assert t[saved["meta"]["home"]] == pytest.approx(0.75) and abs(sum(t) - 1.0) < 1e-6
    # Même chose par la clé meta["teleport"] (pipeline qui la pose dans meta)
    chei, cscore = np.ones(g.n) / g.n, np.full(g.n, 50.0)
    data2 = ex.build_graph_json(g, pr, score, chei, cscore, an, titles, meta_for(teleport=tp))
    assert data2["meta"]["teleport"] == t
    # Vecteur uniforme ou absent : null
    assert graph_data(g, pr, score, an, titles, teleport=np.ones(g.n))["meta"]["teleport"] is None
    assert graph_data(g, pr, score, an, titles)["meta"]["teleport"] is None


def test_graph_json_carries_worst_segment_and_scale():
    cg, g, pr, score, an = analysed(site_with_unreachable_money_page())
    data = graph_data(g, pr, score, an, [""] * g.n)
    ws = data["alignment"]["worst_segment"]
    assert ws and ws["segment"] == "x" and ws["value_share"] > ws["pr_share"]
    assert "sous 70" in data["meta"]["alignment_scale"]
    assert len(data["meta"]["classes"]) == len(CLASS_NAMES)


# ── findings.md ──────────────────────────────────────────────────────────────

def test_findings_md_depth_label_scale_and_wording(tmp_path):
    cg, g, pr, score, an = analysed(site_with_unreachable_money_page())
    x1 = g.urls.index(B + "/x/1/")
    assert an.depth[x1] == -1 and an.starving[x1]
    chei, cscore = np.ones(g.n) / g.n, np.full(g.n, 50.0)
    p = tmp_path / "findings.md"
    ex.write_findings_md(str(p), g, pr, score, chei, cscore, an, meta_for(), cg.stats, {"iterations": 10, "converged": True})
    md = p.read_text(encoding="utf-8")
    famine = md.split("## 4. Famine")[1].split("## 5.")[0]
    assert "| /x/1/ |" in famine and "| inatteignable |" in famine
    assert "| -1 |" not in md
    assert "sous 70 : désaligné ; 70 à 85 : à corriger ; au-dessus de 85" in md
    assert "Pire écart de segment" in md and "segment x reçoit" in md
    assert "Corrélation de rang valeur ↔ score (Spearman)" in md
    assert "(header, nav, footer, sidebar, boilerplate)" in md
    quadrants = md.split("## 8. Quadrants")[1].split("## 9.")[0]
    assert "Peu reliées et peu reliantes" in quadrants and "suppression" not in quadrants and "morts" not in quadrants
    assert "Hôte de référence : `s.com`" in md and "https://s.com/x/1/" not in famine
    assert "Liens vers un autre hôte" in md and "Liens vers des URL non crawlées" in md
    assert chr(0x2014) not in md     # pas de tiret cadratin dans un livrable


def test_findings_md_silo_matrix_is_capped(tmp_path):
    pages, edges, table = site_with_many_segments(14)
    cg, g, pr, score, an = run_analysis(edges, table)
    assert len(an.segment_names) > ex.SILO_MATRIX_MAX
    chei, cscore = np.ones(g.n) / g.n, np.full(g.n, 50.0)
    p = tmp_path / "findings.md"
    ex.write_findings_md(str(p), g, pr, score, chei, cscore, an, meta_for(), cg.stats, {"iterations": 10, "converged": True})
    md = p.read_text(encoding="utf-8")
    sec = md.split("## 7. Étanchéité des silos")[1].split("## 8.")[0]
    header = next(l for l in sec.splitlines() if l.startswith("| Depuis"))
    cols = [c.strip() for c in header.strip("|").split("|")][1:]
    assert len(cols) == ex.SILO_MATRIX_MAX + 1 and cols[-1] == "autres"
    assert "flows_segments.csv" in sec
    body = [l for l in sec.splitlines() if l.startswith("| **")]
    assert len(body) == ex.SILO_MATRIX_MAX + 1
