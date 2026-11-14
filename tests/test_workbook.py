# -*- coding: utf-8 -*-
"""Classeur report.xlsx (prgraph.workbook) : onglets, en-têtes français, formats des parts, volets figés, filtres,
mise en page d'impression, aucune formule, cellules hostiles gardées en texte, robustesse aux fichiers absents."""
import csv
import io
import json
import os
import shutil
import zipfile

import openpyxl
import pytest

from prgraph import cli
from prgraph.workbook import write_workbook

SHEETS = ["Synthèse", "Plan d'action", "Pages", "Famine", "Gouffres", "Segments", "Flux entre silos", "Modèle",
          "Lisez-moi"]
HOSTILE = '=HYPERLINK("x","y")'


@pytest.fixture(scope="module")
def demo_out(tmp_path_factory):
    """Dossier de sortie de la démo (300 produits, données business, plan d'action)."""
    root = tmp_path_factory.mktemp("demo")
    assert cli.main(["demo", "--out", str(root), "--products", "300", "--run"]) == 0
    out = root / "out"
    assert (out / "pages.csv").exists() and (out / "graph.json").exists()
    return out


@pytest.fixture(scope="module")
def book(demo_out, tmp_path_factory):
    path = tmp_path_factory.mktemp("xlsx") / "classeur.xlsx"
    write_workbook(str(path), str(demo_out))
    return openpyxl.load_workbook(str(path)), path


def headers(ws):
    return [c.value for c in ws[1]]


def column(ws, header):
    k = headers(ws).index(header) + 1
    return [ws.cell(r, k) for r in range(2, ws.max_row + 1)]


def copy_out(demo_out, tmp_path):
    dst = tmp_path / "out"
    shutil.copytree(demo_out, dst)
    return dst


def rewrite_pages(out, edit):
    """Relit pages.csv (UTF-8 avec BOM), applique `edit(header, rows)` et le réécrit au même format."""
    p = out / "pages.csv"
    with io.open(p, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    edit(rows[0], rows[1:])
    with io.open(p, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)


# ── Structure ──────────────────────────────────────────────────────────────────

def test_onglets_dans_l_ordre_et_noms_courts(book):
    wb, _ = book
    assert wb.sheetnames == SHEETS
    assert all(len(n) <= 31 for n in wb.sheetnames)


def test_en_tetes_francais(book):
    wb, _ = book
    assert headers(wb["Synthèse"]) == ["Indicateur", "Valeur", "Repère", "Lecture"]
    for h in ("Ticket", "Priorité", "Type de chantier", "Effort", "Cible", "Problème", "Consigne", "Gain estimé"):
        assert h in headers(wb["Plan d'action"]), h
    pages = headers(wb["Pages"])
    for h in ("URL", "Titre", "Segment", "Classe", "Score", "Part d'équité", "Signaux", "Liens entrants",
              "Profondeur (clics)", "Part de la valeur"):
        assert h in pages, h
    assert not any(h in pages for h in ("url", "pr_share_pct", "pagerank", "cheirank", "flags", "click_depth"))
    assert headers(wb["Famine"]) == pages and headers(wb["Gouffres"]) == pages
    for h in ("Segment", "Pages", "Part du PageRank", "Part de la valeur", "Écart (points)"):
        assert h in headers(wb["Segments"]), h
    assert headers(wb["Lisez-moi"]) == ["Onglet", "Colonne ou terme", "Ce qu'elle veut dire"]


def test_volets_figes_filtre_et_impression(book):
    wb, _ = book
    assert wb["Synthèse"].freeze_panes == "A2"
    assert wb["Pages"].freeze_panes == "C2"
    for name in SHEETS:
        ws = wb[name]
        assert ws.freeze_panes, name
        assert ws.page_setup.orientation == "landscape", name
        assert ws.page_setup.fitToWidth == 1 and ws.sheet_properties.pageSetUpPr.fitToPage, name
        assert ws.print_title_rows == "$1:$1", name
        if name != "Synthèse":
            assert ws.auto_filter.ref and ws.auto_filter.ref.startswith("A1:"), name
    assert wb["Pages"].auto_filter.ref.endswith(str(wb["Pages"].max_row))


def test_largeurs_de_colonnes_posees(book):
    wb, _ = book
    for name in SHEETS:
        ws = wb[name]
        assert ws.column_dimensions["A"].width and ws.column_dimensions["A"].width >= 6, name
    assert wb["Pages"].column_dimensions["B"].width >= 30          # URL lisible


# ── Formats ────────────────────────────────────────────────────────────────────

def test_parts_en_pourcentage_et_scores_sans_decimale(book):
    wb, _ = book
    share = [c for c in column(wb["Pages"], "Part d'équité") if c.value is not None]
    assert share and all(c.number_format.startswith("0.000 %") for c in share)
    assert all(0 <= c.value <= 1 for c in share)
    assert abs(sum(c.value for c in share) - 1.0) < 0.01           # parts d'équité : somme 100 %
    assert all(c.number_format == "0" for c in column(wb["Pages"], "Score") if c.value is not None)
    seg = [c for c in column(wb["Segments"], "Part du PageRank") if c.value is not None]
    assert seg and all(c.number_format == "0.0 %" for c in seg)
    gap = [c for c in column(wb["Segments"], "Écart (points)") if c.value is not None]
    assert gap and max(abs(c.value) for c in gap) > 1             # en points, pas en fraction
    assert all(c.number_format == "0.0 %" for c in column(wb["Flux entre silos"], "Poids du silo dans le flux")
               if c.value is not None)


def test_aucun_format_scientifique_aucune_formule(book):
    wb, _ = book
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                assert c.data_type != "f", (ws.title, c.coordinate)
                assert "E+" not in (c.number_format or "").upper(), (ws.title, c.coordinate, c.number_format)


def test_aucune_valeur_non_finie_dans_le_xml(book):
    """Une valeur NaN ou infinie écrite telle quelle déclenche la réparation du classeur dans Excel."""
    _, path = book
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.startswith("xl/worksheets/sheet"):
                xml = z.read(name).decode("utf-8")
                for bad in ("<v>nan</v>", "<v>inf</v>", "<v>-inf</v>"):
                    assert bad not in xml.lower(), (name, bad)


def test_liens_cliquables_et_drapeaux_lisibles(book):
    wb, _ = book
    urls = column(wb["Pages"], "URL")
    assert urls[0].hyperlink is not None and urls[0].hyperlink.target.startswith("https://")
    flags = {c.value for c in column(wb["Pages"], "Signaux") if c.value}
    assert any("famine" in f for f in flags) and not any("|" in f for f in flags)
    famine = column(wb["Famine"], "Signaux")
    assert famine and all("famine" in (c.value or "") for c in famine if c.value)
    assert all("gouffre" in (c.value or "") for c in column(wb["Gouffres"], "Signaux") if c.value)


def test_mise_en_forme_conditionnelle_sobre(book):
    wb, _ = book
    rules = [r for rng in wb["Pages"].conditional_formatting for r in rng.rules]
    kinds = {r.type for r in rules}
    assert "dataBar" in kinds and "expression" in kinds
    bar = next(r for r in rules if r.type == "dataBar")
    assert bar.dataBar.color.rgb.upper().endswith("2A78D6")
    scale = [r for rng in wb["Flux entre silos"].conditional_formatting for r in rng.rules if r.type == "colorScale"]
    assert scale and [c.rgb.upper()[-6:] for c in scale[0].colorScale.color] == ["CDE2FB", "2A78D6"]


def test_plan_d_action_lisible(book, demo_out):
    wb, _ = book
    ws = wb["Plan d'action"]
    with io.open(demo_out / "actions.csv", encoding="utf-8-sig", newline="") as fh:
        n_actions = sum(1 for _ in csv.DictReader(fh))
    assert ws.max_row == n_actions + 1
    prio = {c.value for c in column(ws, "Priorité")}
    assert prio <= {"Critique", "Moyen", "Optimisation"}
    assert all(c.alignment.wrap_text for c in column(ws, "Consigne") if c.value)
    assert all(c.value for c in column(ws, "Effort"))                # effort lu dans graph.json pour chaque ticket
    # le texte d'origine est rendu sans l'apostrophe de neutralisation des CSV (« '+1.59 pt » -> « +1.59 pt »)
    gains = [c for c in column(ws, "Gain estimé") if c.value]
    assert gains and not any(c.value.startswith("'") for c in gains)


def test_synthese_indicateurs(book):
    wb, _ = book
    ws = wb["Synthèse"]
    labels = [c.value for c in ws["A"]]
    for lab in ("Site", "Date de l'analyse", "Version de l'outil", "Score d'alignement business", "Pire écart de segment",
                "Corrélation de rang valeur / score", "Part du jus dans le gabarit", "Pages analysées", "Liens internes",
                "Pages en famine", "Gouffres"):
        assert lab in labels, lab
    row = labels.index("Part du jus dans le gabarit") + 1
    assert ws.cell(row, 2).number_format == "0.0 %" and 0 < ws.cell(row, 2).value < 1


# ── Sécurité : rien ne devient une formule ──────────────────────────────────────

def test_titre_hostile_reste_du_texte(demo_out, tmp_path):
    out = copy_out(demo_out, tmp_path)

    def edit(h, rows):
        rows[0][h.index("title")] = HOSTILE
        rows[1][h.index("segment")] = "+cmd|' /C calc'!A0"
        rows[2][h.index("title")] = "@SUM(1+1)"
        rows[3][h.index("title")] = "-2+3"
        rows[4][h.index("url")] = "javascript:alert(1)"
        rows[5][h.index("title")] = "titre\x01avec\x1fcontrôle"
        rows[6][h.index("title")] = "x" * 40000
        rows[7][h.index("click_depth")] = "-1"
    rewrite_pages(out, edit)
    path = tmp_path / "hostile.xlsx"
    write_workbook(str(path), str(out))
    wb = openpyxl.load_workbook(str(path))
    ws = wb["Pages"]
    titles = column(ws, "Titre")
    assert titles[0].value == HOSTILE and titles[0].data_type == "s" and titles[0].quotePrefix
    assert column(ws, "Segment")[1].value.startswith("+cmd") and column(ws, "Segment")[1].data_type == "s"
    assert titles[2].value == "@SUM(1+1)" and titles[2].data_type == "s"
    assert titles[3].value == "-2+3" and titles[3].data_type == "s"
    url = column(ws, "URL")[4]
    assert url.value == "javascript:alert(1)" and url.hyperlink is None      # jamais cliquable
    assert titles[5].value == "titreaveccontrôle"                            # caractères interdits par le XML retirés
    assert len(titles[6].value) <= 32767
    assert column(ws, "Profondeur (clics)")[7].value == "inatteignable"
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for c in row:
                assert c.data_type != "f", (sheet.title, c.coordinate)


def test_titre_deja_neutralise_dans_le_csv(demo_out, tmp_path):
    """Un CSV écrit par prgraph porte « '=... » : le classeur affiche le texte d'origine, protégé en texte."""
    out = copy_out(demo_out, tmp_path)
    rewrite_pages(out, lambda h, rows: rows[0].__setitem__(h.index("title"), "'" + HOSTILE))
    path = tmp_path / "neutralise.xlsx"
    write_workbook(str(path), str(out))
    cell = column(openpyxl.load_workbook(str(path))["Pages"], "Titre")[0]
    assert cell.value == HOSTILE and cell.data_type == "s" and cell.quotePrefix


# ── Robustesse ─────────────────────────────────────────────────────────────────

def test_dossier_vide_donne_des_lignes_donnee_absente(tmp_path):
    path = tmp_path / "vide.xlsx"
    write_workbook(str(path), str(tmp_path))
    wb = openpyxl.load_workbook(str(path))
    assert wb.sheetnames == SHEETS
    for name in ("Plan d'action", "Pages", "Famine", "Gouffres", "Segments", "Flux entre silos", "Modèle"):
        assert str(wb[name]["A2"].value).startswith("Donnée absente"), name
    assert any(str(c.value).startswith("Donnée absente") for c in wb["Synthèse"]["B"])


def test_sans_business_ni_actions_ni_scenario(demo_out, tmp_path):
    out = copy_out(demo_out, tmp_path)
    os.remove(out / "actions.csv")

    def no_value(h, rows):
        for r in rows:
            r[h.index("value")] = "0"
            r[h.index("value_share")] = "0"
    rewrite_pages(out, no_value)
    g = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    g["meta"]["value_col"] = None
    g["scenario"] = None
    g["actions"] = []
    (out / "graph.json").write_text(json.dumps(g), encoding="utf-8")
    path = tmp_path / "sans.xlsx"
    write_workbook(str(path), str(out))
    wb = openpyxl.load_workbook(str(path))
    assert not any(str(h).startswith("Valeur") or h == "Part de la valeur" for h in headers(wb["Pages"]))
    assert "Part de la valeur" not in headers(wb["Segments"])
    assert str(wb["Plan d'action"]["A2"].value).startswith("Donnée absente")
    assert not any(str(c.value or "").startswith("Avant / après") for c in wb["Synthèse"]["A"])


def test_scenario_et_cibles_requalifiees(demo_out, tmp_path):
    out = copy_out(demo_out, tmp_path)
    g = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    g["scenario"] = {"label": "Chantiers de gabarit appliqués ensemble", "changes": ["liens légaux relégués en pied de page"],
                     "removed": 10, "moved": 5, "note": "Sortie du modèle.",
                     "before": {"alignment": 80.0, "productive_share": 0.8, "waste_share": 0.05, "starving_pr_share": 0.0076},
                     "after": {"alignment": 84.5, "productive_share": 0.83, "waste_share": 0.03, "starving_pr_share": 0.0080}}
    g["meta"]["stats"]["boilerplate_target_list"] = [["https://demo-outdoor.example/marques/", 412, 0.85], ["=EVIL()", 3, 0.1]]
    (out / "graph.json").write_text(json.dumps(g), encoding="utf-8")
    path = tmp_path / "scenario.xlsx"
    write_workbook(str(path), str(out))
    wb = openpyxl.load_workbook(str(path))
    syn = wb["Synthèse"]
    labels = [c.value for c in syn["A"]]
    start = next(i for i, v in enumerate(labels, 1) if str(v or "").startswith("Avant / après"))
    assert syn.cell(start, 2).value == "Aujourd'hui" and syn.cell(start, 3).value == "Après chantiers"
    row = labels.index("Part du PageRank des pages en famine") + 1
    assert syn.cell(row, 2).number_format == "0.00 %"            # petite part : deux décimales
    assert "point" in syn.cell(row, 4).value
    mod = wb["Modèle"]
    params = [c for c in mod["B"]]
    target = next(c for c in params if c.value == "https://demo-outdoor.example/marques/")
    assert target.hyperlink is not None and mod.cell(target.row, 3).value == 412
    evil = next(c for c in params if c.value == "=EVIL()")
    assert evil.data_type == "s" and evil.hyperlink is None
