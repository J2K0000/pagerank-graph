# -*- coding: utf-8 -*-
"""Données 0.5.0 partagées par les modes Analyse et Présentation : familles de segments, scénario avant /
après, marque, bibliothèques embarquées, jointure business avec --strip-query."""
import base64
import json

import pytest

from prgraph import cli
from prgraph.export import (FAMILY_SLOTS, PRESENTATION_MARKER, VENDOR_LIBS, embed_vendor_libs, write_report_html)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo05")
    assert cli.main(["demo", "--out", str(out), "--products", "300", "--run"]) == 0
    return out


def _graph(demo):
    return json.load(open(demo / "out" / "graph.json", encoding="utf-8"))


def test_families_group_language_variants_and_fold_others():
    from prgraph.export import segment_families

    class An:
        seg_table = [{"segment": s, "pages": 10, "pr_share": p, "value_share": v} for s, p, v in (
            ("blog", .20, .40), ("fr/blog", .10, .10), ("uk/blog", .05, 0), ("accueil", .05, .10),
            ("a", .09, 0), ("b", .08, 0), ("c", .07, 0), ("d", .06, 0), ("e", .05, 0), ("f", .04, 0), ("g", .03, .4))]

    fams, seg_fam = segment_families(An)
    names = [f["name"] for f in fams]
    assert names[0] == "blog" and fams[0]["pages"] == 30 and fams[0]["langs"] == ["fr", "uk"]
    assert fams[0]["pr_share"] == pytest.approx(.35)
    assert [f["slot"] for f in fams[:FAMILY_SLOTS]] == list(range(FAMILY_SLOTS))
    assert names[-1] == "autres" and fams[-1]["slot"] is None
    assert seg_fam[1] == seg_fam[2] == 0                     # fr/blog et uk/blog dans la famille blog
    assert fams[seg_fam[-1]]["name"] == "autres"             # « g » replié dans Autres
    assert sum(f["pr_share"] for f in fams) == pytest.approx(sum(r["pr_share"] for r in An.seg_table))


def test_graph_json_carries_families_scenario_and_no_brand(demo):
    d = _graph(demo)
    assert d["families"] and all("family" in s for s in d["segments"])
    sc = d["scenario"]
    assert sc is not None and sc["moved"] > 0 and sc["removed"] >= 0
    assert set(sc["before"]) >= {"alignment", "productive_share", "waste_share", "class_share"}
    assert sc["after"]["waste_share"] < sc["before"]["waste_share"]
    assert d["meta"]["brand"] is None


def test_report_embeds_libraries_and_works_offline(demo):
    html = (demo / "out" / "report.html").read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in html
    for fname in VENDOR_LIBS.values():
        assert f"/* {fname} (MIT" in html
    assert (demo / "out" / "report.xlsx").exists()             # classeur toujours produit


def test_cdn_option_keeps_script_tags(tmp_path):
    tpl = tmp_path / "t.html"
    url = next(iter(VENDOR_LIBS))
    tpl.write_text(f'<html><head><script src="{url}" crossorigin="anonymous"></script></head>'
                   f'<body><script>const D=/*__GRAPH_DATA__*/null;</script>{PRESENTATION_MARKER}</body></html>', encoding="utf-8")
    pres = tmp_path / "p.html"
    pres.write_text('<div id="presentation"></div>', encoding="utf-8")
    out = tmp_path / "r.html"
    write_report_html(str(out), {"a": "<!--<script>"}, template_path=str(tpl), cdn=True, presentation_path=str(pres))
    html = out.read_text(encoding="utf-8")
    assert url in html and html.count('<div id="presentation">') == 1 and PRESENTATION_MARKER not in html
    assert "\\u003c!--\\u003cscript\\u003e" in html
    assert url not in embed_vendor_libs(f'<script src="{url}"></script>')


def test_brand_options(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    a = cli.build_parser().parse_args(["compute", "--edges", "x", "--out", "y", "--brand-name", "Agence X",
                                       "--brand-logo", str(logo), "--brand-color", "#1640EF"])
    b = cli.read_brand(a)
    assert b["name"] == "Agence X" and b["color"] == "#1640ef"
    assert b["logo"].startswith("data:image/png;base64,") and base64.b64decode(b["logo"].split(",")[1])[:4] == b"\x89PNG"
    bad = tmp_path / "evil.svg"
    bad.write_text('<svg onload="alert(1)"></svg>', encoding="utf-8")
    with pytest.raises(ValueError):
        cli.read_brand(cli.build_parser().parse_args(["compute", "--edges", "x", "--out", "y", "--brand-logo", str(bad)]))
    with pytest.raises(ValueError):
        cli.read_brand(cli.build_parser().parse_args(["compute", "--edges", "x", "--out", "y", "--brand-color", "bleu"]))
    assert cli.read_brand(cli.build_parser().parse_args(["compute", "--edges", "x", "--out", "y"])) is None


def test_business_join_strips_query_like_the_graph(tmp_path):
    b = "https://s.fr"
    edges = tmp_path / "e.csv"
    edges.write_text("source,target,position,nofollow,anchor\n" + "\n".join(
        f"{b}{s},{b}{t},content,0," for s, t in (("/", "/a?x=1"), ("/a", "/"), ("/", "/b"), ("/b", "/"))), encoding="utf-8")
    biz = tmp_path / "biz.csv"
    biz.write_text(f"url,clicks\n{b}/a?utm_source=x,30\n{b}/a#top,12\n{b}/b,5\n", encoding="utf-8")
    out = tmp_path / "o"
    assert cli.main(["compute", "--edges", str(edges), "--business", str(biz), "--strip-query", "--out", str(out),
                     "--no-actions", "--no-excel"]) == 0
    rows = {r.split(",")[0]: r for r in (out / "pages.csv").read_text(encoding="utf-8-sig").splitlines()[1:]}
    header = (out / "pages.csv").read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    v = header.index("value")
    assert float(rows[f"{b}/a"].split(",")[v]) == 42.0          # paramètres et ancre joints à la même page


def test_scenario_is_built_from_the_plan_template_actions(demo):
    d = _graph(demo)
    sc = d["scenario"]
    ids = {a["id"] for a in d["actions"] if a.get("rule")}
    assert sc and sc["tickets"] and set(sc["tickets"]) <= ids        # exactement les chantiers de gabarit du plan
    assert sc["changes"] and all(any(ch.isdigit() for ch in c) for c in sc["changes"])   # chaque étape a son volume
    assert "value_pages_share" in sc["before"] and "value_pages_share" in sc["after"]


def test_value_pages_share_and_worst_family_have_a_piece(demo):
    d = _graph(demo)
    al = d["alignment"]
    assert 0 < al["value_pages_pr_share"] < 1 and al["value_pages_count"] > 0 and al["value_pages_value_share"] == 0.8
    findings = (demo / "out" / "findings.md").read_text(encoding="utf-8")
    assert "Pages qui portent 80 % de la valeur" in findings
    wf = al.get("worst_family")
    if wf and len(wf["segments"]) > 1:
        assert "toutes langues" in findings


def test_business_period_reaches_the_report(tmp_path):
    b = "https://s.fr"
    edges = tmp_path / "e.csv"
    edges.write_text("source,target,position,nofollow,anchor\n" + "\n".join(
        f"{b}{s},{b}{t},content,0," for s, t in (("/", "/a"), ("/a", "/"), ("/", "/b"), ("/b", "/"))), encoding="utf-8")
    biz = tmp_path / "biz.csv"
    biz.write_text(f"# période : 2026-08-09 au 2026-11-08\nurl,clicks\n{b}/a,30\n{b}/b,5\n", encoding="utf-8")
    out = tmp_path / "o"
    assert cli.main(["compute", "--edges", str(edges), "--business", str(biz), "--out", str(out), "--no-excel"]) == 0
    meta = json.load(open(out / "graph.json", encoding="utf-8"))["meta"]
    assert meta["value_period"] == "2026-08-09 au 2026-11-08" and meta["value_kind"] == "trafic"
    out2 = tmp_path / "o2"
    assert cli.main(["compute", "--edges", str(edges), "--business", str(biz), "--business-period", "T3 2026",
                     "--out", str(out2), "--no-excel", "--no-actions"]) == 0
    assert json.load(open(out2 / "graph.json", encoding="utf-8"))["meta"]["value_period"] == "T3 2026"
