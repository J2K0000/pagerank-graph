# -*- coding: utf-8 -*-
"""Gabarit du rapport, mode Analyse : contrat avec le mode Présentation, accessibilité des onglets, aucun appel à
un tiers à l'ouverture (pas de Google Fonts, scripts CDN avec intégrité SRI), script principal syntaxiquement valide."""
import json
import re
import shutil
import subprocess

import pytest

from prgraph import cli
from prgraph.export import GRAPH_DATA_MARKER, PRESENTATION_MARKER, VENDOR_LIBS, VIEWER_TEMPLATE, write_report_html

SRI = {
    "https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js":
        "sha384-643a1dipBp0OHFnm3WeZmMnuJ+ZY/MEll1vTGCjeFzkB/RySh+Almy3Ffn+sxO7y",
    "https://cdn.jsdelivr.net/npm/graphology-library@0.8.0/dist/graphology-library.min.js":
        "sha384-JfW8ehTxF6vKpm17Oz+cpaluYzFMvm6YfXgwbw5Hxzrn0rR6WR+0DDDWPEIv9CtK",
    "https://cdn.jsdelivr.net/npm/sigma@3.0.1/dist/sigma.min.js":
        "sha384-WfseDhOK74KFNUyIuhDyZ+KNI5KYrPcW8bjH6DfpgnJe8jzIpJQbRE9xMSl7/Eb7",
}


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo_tpl")
    assert cli.main(["demo", "--out", str(out), "--products", "300", "--run"]) == 0
    return out


@pytest.fixture(scope="module")
def template():
    return open(VIEWER_TEMPLATE, encoding="utf-8").read()


def _main_script(html: str) -> str:
    start = html.index('<script id="graph-data"')
    i = html.index("<script>", start)
    return html[i + len("<script>"): html.index("</script>", i)]


def test_template_markers_once_and_presentation_before_body_end(template):
    assert template.count(GRAPH_DATA_MARKER) == 1
    assert template.count(PRESENTATION_MARKER) == 1
    tail = template[template.index(PRESENTATION_MARKER) + len(PRESENTATION_MARKER):]
    assert tail.strip().startswith("</body>")


def test_template_contract_and_accessibility(template):
    assert "window.PRGRAPH" in template and "setMode" in template
    assert "PRGRAPH_PRESENT" in template and "#presentation" in template
    assert 'id="app"' in template
    assert 'role="tablist"' in template
    tabs = re.findall(r'<button[^>]*\brole="tab"[^>]*>', template)
    assert len(tabs) >= 10 and all("aria-controls=" in t and "aria-selected=" in t for t in tabs)
    assert template.count('role="tabpanel"') == len(tabs)
    assert "fonts.googleapis.com" not in template and "fonts.gstatic.com" not in template
    assert "\u2014" not in template                       # pas de tiret cadratin


def test_template_cdn_scripts_carry_sri(template):
    tags = re.findall(r"<script[^>]*\bsrc=\"[^\"]+\"[^>]*>", template)
    assert len(tags) == 3
    for url, h in SRI.items():
        tag = next(t for t in tags if f'src="{url}"' in t)
        assert f'integrity="{h}"' in tag and 'crossorigin="anonymous"' in tag
    assert set(SRI) == set(VENDOR_LIBS)


def test_generated_report_embedded_and_cdn(demo, tmp_path):
    html = (demo / "out" / "report.html").read_text(encoding="utf-8")
    assert 'role="tablist"' in html and "fonts.googleapis.com" not in html
    assert GRAPH_DATA_MARKER not in html
    assert html.count(PRESENTATION_MARKER) <= 1           # remplacé quand viewer/present.html existe
    assert "window.PRGRAPH" in html
    data = json.load(open(demo / "out" / "graph.json", encoding="utf-8"))
    out = tmp_path / "cdn.html"
    write_report_html(str(out), data, cdn=True)
    cdn = out.read_text(encoding="utf-8")
    for url, h in SRI.items():
        tag = re.search(r"<script[^>]*src=\"" + re.escape(url) + r"\"[^>]*>", cdn)
        assert tag and f'integrity="{h}"' in tag.group(0) and 'crossorigin="anonymous"' in tag.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent : vérification de syntaxe JavaScript sautée")
def test_main_script_syntax(template, tmp_path):
    js = tmp_path / "main.js"
    js.write_text(_main_script(template), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ── Revue 0.5.0 : vocabulaire du verdict, scénario du plan, simulation dans la vue Familles, historique ──────

def test_verdict_vocabulary_and_new_fields(template):
    js = _main_script(template)
    # M1 : la mesure de « ce qui rapporte » est la part reçue par les pages qui portent la valeur
    for key in ("value_pages_pr_share", "value_pages_count", "value_pages_value_share", "Pages qui portent"):
        assert key in js, key
    # productive_share n'est jamais appelé « pages qui rapportent » ; il est toujours qualifié par l'adresse
    for line in js.splitlines():
        if "productive_share" in line:
            assert "qui rapportent" not in line, line
    assert js.count("(classées par leur adresse)") >= 3
    # Pire écart de rubrique (toutes langues), chiffre du mode Présentation
    assert "worst_family" in js and "toutes langues" in js and "chiffre du mode Présentation" in js
    # M2 : nature et période de la valeur sous le score
    assert "value_kind" in js and "value_period" in js
    assert "Les pages de conversion en reçoivent peu : cette mesure les sous-estime." in js
    assert "clics depuis Google" in js


def test_plan_scenario_and_zero_waste_note(template):
    js = _main_script(template)
    assert "sc.tickets" in js and "sc.changes" in js and "value_pages_share" in js
    assert "data-act-go" in js and "goAction" in js
    # Bouton « Scénario zéro déchet » : dit qu'il s'agit d'un autre jeu de règles que le scénario du plan
    assert template.count("autre jeu de règles") >= 3
    assert 'aria-describedby="zeroNote"' in template and 'id="zeroNote"' in template


def test_families_view_follows_simulation(template):
    js = _main_script(template)
    assert "function metaState(" in js and "function refreshMeta(" in js
    # Simulation lancée, réinitialisée, bascule Avant / Après : la vue Familles ou Segments est remontée
    assert js.count("refreshMeta();") >= 3
    assert "src:simSrc, dst:simDst, w:simW" in js


def test_meta_labels_stay_inside_canvas(template):
    js = _main_script(template)
    assert "function drawLabelFit(" in js and "LABEL_INSET" in js
    assert "defaultDrawNodeLabel:drawLabelFit, defaultDrawNodeHover:drawHoverFit" in js


def test_presentation_mode_uses_history(template):
    js = _main_script(template)
    assert "history.pushState(" in js and "addEventListener('popstate'" in js
    assert "history.back()" in js
    # Retour au mode Analyse : le focus revient au bouton « Présentation »
    assert "getElementById('btnModePres')" in js and 'id="btnModePres"' in template


def _csv_helpers(template: str) -> str:
    start, end = template.index("/*CSV:début*/"), template.index("/*CSV:fin*/")
    return template[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent : test des fonctions CSV sauté")
def test_csv_export_opens_in_french_excel(template, tmp_path):
    harness = _csv_helpers(template) + r"""
const out = {
  body: csvBody(['URL', 'Part du PageRank (%)', 'Ancre'], [
    [csvText('https://x.fr/a;b'), csvNum(12.5), csvText('=HYPERLINK("x")')],
    [csvText('+cmd'), csvNum(-3.25), csvText('@SUM(A1)')],
    [csvText('-1'), csvNum(1.23e-7), csvText('\tsous tab')],
    [csvText('\rretour'), csvNum(''), csvText('texte "cité"')],
  ]),
  nums: [csvNum(0), csvNum(1711), csvNum(0.0395576), csvNum(null), csvNum(NaN), csvNum('4.5')],
};
process.stdout.write(JSON.stringify(out));
"""
    js = tmp_path / "csv.js"
    js.write_text(harness, encoding="utf-8")
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    body = out["body"]
    assert body.startswith("﻿")                                     # BOM UTF-8 : accents lus par Excel
    lines = body[1:].split("\r\n")
    assert lines[0] == '"URL";"Part du PageRank (%)";"Ancre"'            # point-virgule, en-têtes en français
    assert lines[1] == '"https://x.fr/a;b";12,5;"\'=HYPERLINK(""x"")"'  # virgule décimale, formule neutralisée
    assert lines[2] == '"\'+cmd";-3,25;"\'@SUM(A1)"'
    assert lines[3] == '"\'-1";1,23e-7;"\'\tsous tab"'
    assert lines[4] == '"\'\rretour";;"texte ""cité"""'
    assert lines[5] == ""                                                # fin de ligne CRLF après la dernière ligne
    assert out["nums"] == ["0", "1711", "0,0395576", "", "", "4,5"]
    # Tous les exports du navigateur passent par ces fonctions : plus de séparateur virgule ni d'en-tête technique
    main = _main_script(template)
    assert not re.findall(r"csvLabel:'[a-z0-9_]+'", main)                # en-têtes lisibles, pas de noms techniques
    for old in ("'pr_share_pct'", "'click_depth'", "'in_links'", "join(',')"):
        assert old not in main, old


def test_launcher_has_no_third_party_fonts():
    from prgraph.server import VIEWER_DIR
    html = open(f"{VIEWER_DIR}/launcher.html", encoding="utf-8").read()
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert not re.search(r"<link[^>]+href=\"https?://", html)              # aucune feuille de style distante
    assert "system-ui" in html and "\u2014" not in html
