# -*- coding: utf-8 -*-
"""Mode Présentation (viewer/present.html) : fragment conforme au contrat d'intégration, styles cantonnés à
#presentation, aucune ressource externe, aucune insertion HTML brute, une seule variable globale, et insertion
unique par export.write_report_html."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess

import pytest

from prgraph import export as ex

PRESENT = ex.PRESENTATION_TEMPLATE
SVG_NS = "http://www.w3.org/2000/svg"


def fragment() -> str:
    with io.open(PRESENT, encoding="utf-8") as fh:
        return fh.read()


def parts(src: str):
    """(contenu du <style>, contenu du <script>) ; le fragment en porte exactement un de chaque."""
    styles = re.findall(r"<style>(.*?)</style>", src, re.S)
    scripts = re.findall(r"<script>(.*?)</script>", src, re.S)
    assert len(styles) == 1 and len(scripts) == 1
    return styles[0], scripts[0]


def css_rules(css: str, media: str | None = None):
    """Itère (selecteur, @media englobant) sur les règles d'une feuille sans commentaires. Les at-rules autres que
    @media et @page sont signalées par le sélecteur lui-même (le test les refuse)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            assert not css[i:].strip(), f"texte CSS hors règle : {css[i:]!r}"
            return
        head = css[i:j].strip()
        depth, k = 1, j + 1
        while depth and k < n:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        if head.startswith("@media"):
            yield from css_rules(body, head)
        else:
            yield head, media
        i = k


def test_fragment_existe_et_reste_un_fragment():
    assert os.path.exists(PRESENT)
    src = fragment()
    low = src.lower()
    for tag in ("<!doctype", "<html", "<head", "<body"):
        assert tag not in low, tag
    assert '<div id="presentation" hidden aria-label="Présentation"></div>' in src
    assert "\u2014" not in src, "tiret cadratin interdit"
    # Les marqueurs d'export ne doivent jamais apparaître dans le fragment (ils seraient remplacés à sa place)
    assert ex.PRESENTATION_MARKER not in src and ex.GRAPH_DATA_MARKER not in src


def test_aucune_ressource_externe():
    src = fragment()
    urls = set(re.findall(r"https?://[^\s'\"<>)]+", src))
    assert urls <= {SVG_NS}, urls          # seul l'espace de noms SVG, qui n'est pas chargé
    low = src.lower()
    assert "<link" not in low and "@import" not in low and "<script src" not in low and "src=\"" not in low
    assert not re.search(r"url\((?!data:)", src), "url() distante"
    assert "@font-face" not in low


def test_styles_cantonnes_a_presentation():
    css, _ = parts(fragment())
    seen_print = False
    for head, media in css_rules(css):
        if head.startswith("@page"):
            assert head == "@page slide", head
            continue
        assert not head.startswith("@"), f"at-rule non autorisée : {head}"
        for sel in (s.strip() for s in head.split(",")):
            if media and "print" in media:
                seen_print = True
                assert 'body[data-mode="presentation"]' in sel, sel
            else:
                assert sel.startswith("#presentation"), sel
    assert seen_print, "règles d'impression attendues"
    assert "break-after: page" in css and "print-color-adjust: exact" in css


def test_script_sans_insertion_html_brute_ni_globale():
    _, js = parts(fragment())
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "createContextualFragment",
                "srcdoc", "eval(", "new Function"):
        assert bad not in js, bad
    body = js.strip()
    assert body.startswith("(function () {") and body.endswith("})();"), "IIFE attendue"
    assert set(re.findall(r"window\.(\w+)\s*=(?!=)", js)) == {"PRGRAPH_PRESENT"}
    assert "</script" not in js.lower() and "<!--" not in js
    # L'API attendue par le mode Analyse
    assert "window.PRGRAPH_PRESENT = { open: open, close: close }" in js
    assert "setMode('analyse')" in js and "window.print()" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_syntaxe_du_script(tmp_path):
    _, js = parts(fragment())
    p = tmp_path / "present.js"
    p.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_write_report_html_insere_le_fragment_une_fois(tmp_path):
    tpl = tmp_path / "tpl.html"
    tpl.write_text("<!doctype html><html><body>\n<script>window.D = " + ex.GRAPH_DATA_MARKER + ";</script>\n"
                   + ex.PRESENTATION_MARKER + "\n</body></html>", encoding="utf-8")
    out = tmp_path / "report.html"
    # Un titre crawlé qui imite le marqueur ne doit ni être remplacé ni dupliquer le fragment
    data = {"meta": {"site": ex.PRESENTATION_MARKER + " <script>"}, "nodes": []}
    ex.write_report_html(str(out), data, template_path=str(tpl), cdn=True)
    html = out.read_text(encoding="utf-8")
    assert html.count('<div id="presentation"') == 1
    assert html.count("window.PRGRAPH_PRESENT = { open: open, close: close }") == 1
    assert ex.PRESENTATION_MARKER not in html and ex.GRAPH_DATA_MARKER not in html
    assert html.index('<div id="presentation"') < html.index("</body>")
    assert "\\u003c!--__PRESENTATION__--\\u003e" in html


def test_gabarit_sans_marqueur_sans_presentation(tmp_path):
    tpl = tmp_path / "tpl.html"
    tpl.write_text("<html><body><script>window.D = " + ex.GRAPH_DATA_MARKER + ";</script></body></html>", encoding="utf-8")
    out = tmp_path / "report.html"
    ex.write_report_html(str(out), {"meta": {}}, template_path=str(tpl), cdn=True)
    assert 'id="presentation"' not in out.read_text(encoding="utf-8")


def test_formulations_interdites_absentes_du_fragment():
    """M5 : aucune sortie du modèle écrite comme un fait daté ; plus de mot de métier « recrawl » face au client."""
    src = fragment()
    assert "recrawl" not in src.lower()
    assert "On saura que ça marche" not in src
    assert "estimation du modèle" in src
    # M1 : productive_share n'est jamais libellé « pages qui rapportent »
    assert "pages qui rapportent" not in src


# ── Exécution du deck sous Node, dans un faux DOM minimal ─────────────────────────────────────────────────────
# Le script du fragment tourne tel quel ; le faux DOM ne connaît que ce qu'il utilise (createElement, textContent,
# classList, attributs, querySelector par classe, balise ou [attribut]). Sortie : le texte de chaque diapositive.
RUNNER = r"""
'use strict';
const fs = require('fs'), vm = require('vm');
const [, , jsPath, dataPath] = process.argv;
class T { constructor(t) { this.nodeType = 3; this.data = String(t); this.parentNode = null; this.childNodes = []; } get textContent() { return this.data; } }
class E {
  constructor(tag) { this.nodeType = 1; this.tagName = String(tag).toUpperCase(); this.childNodes = []; this.parentNode = null; this.attrs = {}; this._c = [];
    const o = {}; this.style = new Proxy(o, { get(x, k) { if (k === 'setProperty') return (a, b) => { x[a] = String(b); };
      if (k === 'getPropertyValue') return a => x[a] || ''; return x[k]; }, set(x, k, v) { x[k] = String(v); return true; } }); }
  get className() { return this._c.join(' '); } set className(v) { this._c = String(v).split(/\s+/).filter(Boolean); }
  get classList() { const s = this; return { add(c) { if (!s._c.includes(c)) s._c.push(c); }, remove(c) { s._c = s._c.filter(x => x !== c); },
    contains(c) { return s._c.includes(c); }, toggle(c, on) { if (on === undefined) on = !s._c.includes(c); if (on) this.add(c); else this.remove(c); return on; } }; }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.childNodes.push(c); return c; }
  removeChild(c) { const i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; return c; }
  replaceChildren(...cs) { this.childNodes = []; cs.forEach(c => this.appendChild(c)); }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get textContent() { return this.childNodes.map(c => c.textContent).join(''); }
  set textContent(v) { this.childNodes = []; if (v !== '') this.appendChild(new T(v)); }
  setAttribute(k, v) { if (k === 'class') this.className = v; else this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); } removeAttribute(k) { delete this.attrs[k]; }
  addEventListener() {} focus() {} get clientWidth() { return 1340; } get clientHeight() { return 780; }
  querySelectorAll(sel) { let m, a; if (sel[0] === '.') { const c = sel.slice(1); m = n => n._c.includes(c); }
    else if ((a = /^\[([\w-]+)\]$/.exec(sel))) m = n => n.hasAttribute(a[1]); else { const t = sel.toUpperCase(); m = n => n.tagName === t; }
    const out = []; (function w(n) { n.childNodes.forEach(c => { if (c.nodeType === 1) { if (m(c)) out.push(c); w(c); } }); })(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const data = dataPath !== '-' ? JSON.parse(fs.readFileSync(dataPath, 'utf8')) : null;
const root = new E('div'); root.attrs.id = 'presentation';
const warnings = [];
const con = { log() {}, warn: (...a) => warnings.push(a.map(x => (x && x.stack) || String(x)).join(' ')), error: (...a) => warnings.push(a.join(' ')) };
const win = { PRGRAPH: data ? { data } : {}, addEventListener() {}, console: con, print() {} };
const doc = { body: new E('body'), getElementById: id => (id === 'presentation' ? root : null), createElement: t => new E(t),
  createElementNS: (ns, t) => new E(t), createTextNode: t => new T(t), addEventListener() {} };
vm.runInNewContext(fs.readFileSync(jsPath, 'utf8'), { window: win, document: doc, console: con });
win.PRGRAPH_PRESENT.open();
const chunks = (n, out) => { n.childNodes.forEach(c => { if (c.nodeType === 3) { const t = c.data.trim(); if (t) out.push(t); } else chunks(c, out); }); return out; };
const slides = root.querySelectorAll('section').map(s => { const h = s.querySelector('h1') || s.querySelector('h2');
  return { title: h ? h.textContent : '', em: h ? h.querySelectorAll('em').map(e => e.textContent) : [], chunks: chunks(s, []) }; });
process.stdout.write(JSON.stringify({ slides, warnings, accent: root.style.getPropertyValue('--pr-accent') }));
"""

FAMS = [  # nom, segments, part d'autorité, part de la valeur, classe des pages
    ("solution", ["solution", "fr/solution"], 0.23, 0.0003, 0),
    ("racine", ["racine"], 0.16, 0.07, 4),
    ("blog", ["blog", "fr/blog"], 0.14, 0.60, 1),
    ("customer", ["customer"], 0.11, 0.002, 1),
]
CLASSES = ["money", "acquisition", "confiance", "legal_support", "neutre", "accueil"]
PERIOD = "9 août au 8 novembre 2026"


def deck_data(value_col="clicks", value_kind="trafic", scenario=(66.1, 66.4, 0.0396, 0.0395), links_source=None, brand=None):
    """Petit graph.json synthétique au format de prgraph 0.5.0 (aucune donnée client)."""
    segments, families, nodes = [], [], []
    for fi, (name, segs, pr, val, cls) in enumerate(FAMS):
        families.append({"name": name, "segments": segs, "pages": 6 * len(segs), "pr_share": pr, "value_share": val,
                         "langs": ["fr"] if len(segs) > 1 else [], "slot": fi, "gap": val - pr})
        for sn in segs:
            si = len(segments)
            segments.append({"id": si, "name": sn, "family": fi, "pages": 6, "pr_share": pr / len(segs), "value_share": val / len(segs),
                             "starving": 3 if name == "blog" else 0})
            for k in range(6):
                url = f"https://exemple.test/{sn}/page-{k}"
                nodes.append([url, si, 0.001 * (k + 1), 10.0 * k, 0.0, 0.0, 2 + k, 5, 2, 100.0 * (6 - k) if name == "blog" else 1.0,
                              0, 0, f"Titre {sn} {k}", 0.0, 0.0, cls])
    blog = [i for i, n in enumerate(nodes) if "/blog/" in n[0]]
    valued = value_col is not None

    def action(i, fam, scope, typ, **kw):
        a = {"id": f"SEO-{i:02d}", "family": fam, "scope": scope, "priority": "moyen", "title": kw.pop("title", "Action"),
             "problem": kw.pop("problem", "Problème."), "effort": {"type": typ}, "targets": [], "target_urls": [], "added": 0, "removed": 0,
             "gain_pts": 0.0, "gain_text": ""}
        a.update(kw)
        return a

    t0 = blog[0]
    actions = [action(1, "famine", "page", "editorial", targets=[t0], target_urls=[nodes[t0][0]], added=1, rank_before=1063, rank_after=644,
                      gain_text="1 lien(s) : score 5 → 9 (+4 pt), rang 1063 → 644")]
    for i in (2, 3, 4):
        actions.append(action(i, "cible", "page", "editorial", targets=[blog[i]], target_urls=[nodes[blog[i]][0]], added=6,
                              rank_before=1300 + i, rank_after=330 + i))
    actions.append(action(5, "gouffre", "global", "template", title="Regrouper les liens légaux derrière une seule page centre légal (/legal)",
                          problem="14 pages légales / support sont chacune liées depuis tout le gabarit.", gain_pts=0.48, removed=2794, added=9))
    actions.append(action(6, "menu", "global", "template", title="Plafonner les liens « header » à 50 par page",
                          problem="Jusqu’à 57 liens de menu par page.", gain_pts=1.52))
    for i in range(7, 25):
        actions.append(action(i, "famine", "page", "editorial", targets=[blog[i % len(blog)]], added=2, rank_before=1400, rank_after=700))
    bA, aA, bV, aV = scenario
    seg_share = {s["name"]: s["pr_share"] for s in segments}
    sc = {"label": "Chantiers de gabarit du plan d'action appliqués ensemble", "tickets": ["SEO-05", "SEO-06"],
          "changes": ["2794 liens de gabarit vers 13 pages légales retirés, 9 liens ajoutés dans le texte de la page centre /legal",
                      "liens « header » plafonnés à 50 par page (6484 liens retirés)"],
          "removed": 2794, "moved": 0, "added": 9,
          "before": {"alignment": bA, "productive_share": 0.8123, "waste_share": 0.0075, "segment_share": seg_share,
                     "value_pages_share": bV if valued else None},
          "after": {"alignment": aA, "productive_share": 0.8290, "waste_share": 0.003, "segment_share": dict(seg_share, blog=0.075),
                    "value_pages_share": aV if valued else None}}
    al = {"score": bA, "basis": "valeur business" if valued else "cible par défaut",
          "pr_share": {"money": 0.39, "acquisition": 0.4223, "confiance": 0.007, "legal_support": 0.0075, "neutre": 0.026, "accueil": 0.017},
          "target_share": {"money": 0.07, "acquisition": 0.71, "confiance": 0.003, "legal_support": 0.002, "neutre": 0.005, "accueil": 0.21},
          "counts": {"money": 12, "acquisition": 24, "confiance": 0, "legal_support": 14, "neutre": 6, "accueil": 1},
          "waste_share": 0.0075, "productive_share": 0.8123,
          "worst_segment": {"segment": "blog", "pr_share": 0.07, "value_share": 0.3} if valued else None}
    if valued:
        # Volontairement différent des familles (14 % / 60 %) : le deck doit lire ce champ, pas recalculer
        al.update({"worst_family": {"family": "blog", "segments": ["blog", "fr/blog"], "langs": ["fr"], "pr_share": 0.15,
                                    "value_share": 0.61, "gap": 0.46},
                   "value_pages_pr_share": 0.0396, "value_pages_count": 12, "value_pages_value_share": 0.8})
    meta = {"site": "exemple.test", "value_col": value_col, "value_kind": value_kind if valued else None,
            "value_period": PERIOD if valued else None, "tool_version": "0.5.0", "generated": "2026-11-12T08:00:00",
            "n_nodes": len(nodes), "n_edges": 500, "home": 0, "truncated": False, "classes": CLASSES,
            "class_labels": {"money": "Money / conversion", "acquisition": "Acquisition / piliers", "confiance": "Confiance",
                             "legal_support": "Légal / support", "neutre": "Neutre", "accueil": "Accueil"},
            "node_fields": ["url", "seg", "pr", "score", "chei", "cscore", "indeg", "outdeg", "depth", "value", "flags", "quadrant",
                            "title", "leak", "demand", "cls"],
            "weights": {"content": 1.0, "breadcrumb": 0.5, "sidebar": 0.25, "header": 0.2, "nav": 0.2, "footer": 0.05}, "brand": brand}
    if links_source:
        meta["links_source"] = links_source
    n = len(segments)
    flows = [[0.05 if (a + b) % 3 == 0 and a != b else 0.0 for b in range(n)] for a in range(n)]
    return {"meta": meta, "alignment": al, "actions": actions, "segments": segments, "families": families, "scenario": sc,
            "seg_flows": flows, "zones": {"content": 0.44, "header": 0.3, "footer": 0.1, "nav": 0.0, "unknown": 0.16},
            "nodes": nodes, "edges": [], "top": {"starving": blog[:6] if valued else []}}


def run_deck(tmp_path, data):
    _, js = parts(fragment())
    (tmp_path / "present.js").write_text(js, encoding="utf-8")
    (tmp_path / "runner.js").write_text(RUNNER, encoding="utf-8")
    arg = "-"
    if data is not None:
        (tmp_path / "graph.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        arg = str(tmp_path / "graph.json")
    r = subprocess.run(["node", str(tmp_path / "runner.js"), str(tmp_path / "present.js"), arg], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["warnings"] == [], out["warnings"]   # une diapositive omise sur exception est un défaut
    # Espaces insécables (milliers, avant « % ») ramenées à l'espace simple pour écrire les attentes lisiblement
    sp = str.maketrans({" ": " ", " ": " "})
    for s in out["slides"]:
        s["chunks"] = [c.translate(sp) for c in s["chunks"]]
        s["title"] = s["title"].translate(sp)
        s["em"] = [e.translate(sp) for e in s["em"]]
        s["text"] = " ¦ ".join(s["chunks"])
    return out


def slide(out, needle):
    hits = [s for s in out["slides"] if needle in s["text"]]
    assert hits, f"aucune diapositive ne contient {needle!r}"
    return hits[0]


def all_text(out):
    return "\n".join(s["text"] for s in out["slides"])


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")


@needs_node
def test_deck_trafic_nomme_la_mesure_et_les_pages_qui_portent_la_valeur(tmp_path):
    """M1, M2, M5, m4, m10, m11 sur une mesure de trafic (clics) et un scénario d'effet négligeable."""
    out = run_deck(tmp_path, deck_data())
    txt = all_text(out)
    low = txt.lower()
    # M1 : la part des classes money + acquisition (81,2 %) n'apparaît plus ; la part des pages qui portent la valeur, si
    assert "81,2" not in txt and "81 %" not in txt
    assert "pages qui rapportent" not in txt and "ce qui rapporte" not in low
    assert "Les 12 pages qui font 80 % des clics depuis Google reçoivent 4 % de l’autorité." in txt
    # Le pire écart vient de alignment.worst_family (15 % / 61 %), pas du recalcul sur les familles (14 % / 60 %)
    verdict = slide(out, "Le plus gros écart")
    assert "15 %" in verdict["text"] and "61 % des clics depuis Google" in verdict["text"]
    assert "(toutes langues) (" not in txt                               # m11
    # M2 : la mesure nommée, sa période sur la couverture, les pages mal reliées et la Méthode, la réserve sous 5 et 12
    assert PERIOD in out["slides"][0]["text"]
    assert PERIOD in slide(out, "[ Pages mal reliées ]")["text"]
    assert PERIOD in slide(out, "Poids des liens")["text"]
    caveat = "cette mesure les sous-estime, elle ne dit pas qu’elles ne rapportent pas"
    assert caveat in slide(out, "[ Les clics face à l’autorité ]")["text"]
    avant = slide(out, "[ Avant / après ]")
    assert caveat in avant["text"]
    # Aucune flèche ne part des pages de conversion (rubrique « solution », faite de pages money) vers le blog
    assert "Pages solution" not in avant["text"]
    assert "sans retirer ceux qui mènent aux pages de conversion" in avant["text"]
    # M3 / m4 : effet négligeable, score en entier, « inchangé (66) », chantiers simulés nommés comme les cartes du plan
    assert "ne suffit pas ici" in avant["title"]
    assert "inchangé (66)" in avant["text"] and "66,1" not in txt and "66,4" not in txt
    assert "SEO-05 (carte 05 du plan) et SEO-06 (plan complet)" in avant["text"]
    assert "3,96 % → 3,95 %" in avant["text"]
    # M5 : conditionnel, condition et « estimation du modèle », plus de « recrawl »
    plan = slide(out, "[ Le plan d’action ]")
    assert "recrawl" not in low and "On saura" not in txt
    assert "Son rang d’autorité devrait passer d’environ 1 063 à environ 644 si le lien proposé est posé (estimation du modèle)." in plan["text"]
    assert "Action SEO-05" in plan["text"]
    assert plan["title"] == "Les cinq premiers des 24 chantiers du plan d’action."   # m10


@needs_node
def test_deck_scenario_degradant_titre_baisse_sans_accent(tmp_path):
    """M3 : une baisse n'est jamais titrée comme un progrès, et les cartes concernées du plan le disent."""
    out = run_deck(tmp_path, deck_data(value_col=None, scenario=(47.8, 44.7, None, None)))
    avant = slide(out, "[ Avant / après ]")
    assert avant["title"] == "Les chantiers de gabarit seuls feraient baisser l’alignement (48 → 45)."
    assert avant["em"] == []                                             # aucun chiffre en couleur d'accent
    assert "portent l’alignement" not in all_text(out)
    assert "à arbitrer avant de les lancer" in avant["text"]
    plan = slide(out, "[ Le plan d’action ]")
    assert "feraient baisser l’alignement (48 → 45)" in plan["text"]
    k = out["slides"].index(avant) + 1
    assert f"Voir p. {k}." in plan["text"]
    # m3 : sans valeur, pas de diapositive « pages mal reliées », une ligne dans la Méthode
    assert not [s for s in out["slides"] if "[ Pages mal reliées ]" in s["text"] or "source de valeur" in s["title"]]
    methode = slide(out, "Poids des liens")
    assert "Pages mal reliées" in methode["text"]
    assert "leur nombre est un plancher" in methode["text"]              # m9
    # productive_share n'apparaît qu'avec son libellé de classes d'adresses
    for s in out["slides"]:
        for i, c in enumerate(s["chunks"]):
            if "81,2 %" in c:
                assert "classées par leur adresse" in " ".join(s["chunks"][max(0, i - 2):i + 1]), c


@needs_node
def test_deck_scenario_en_hausse_et_valeur_business(tmp_path):
    out = run_deck(tmp_path, deck_data(value_col="revenue", value_kind="business", scenario=(91.4, 96.4, 0.011, 0.0118)))
    avant = slide(out, "[ Avant / après ]")
    assert avant["title"] == "Les chantiers de gabarit portent l’alignement de 91 à 96 sur 100."
    assert avant["em"] == ["96 sur 100"]
    # M1 : l'avant / après compare la part des pages qui portent la valeur, jamais celle des classes money + acquisition
    assert "Part de l’autorité des 12 pages qui font 80 % du chiffre d’affaires" in avant["text"]
    assert "1,10 %" in avant["text"] and "1,18 %" in avant["text"] and "+0,08 pt" in avant["text"]
    assert "81,2" not in avant["text"] and "82,9" not in avant["text"]
    assert "Les 12 pages qui font 80 % du chiffre d’affaires reçoivent 4 % de l’autorité." in all_text(out)
    assert "ce qui rapporte" in out["slides"][0]["text"]                # mesure business : le mot est juste


@needs_node
@pytest.mark.parametrize("source, attendu, absent", [
    ("crawl", "pas de rendu JavaScript", "liens fournis par l’export"),
    ("export", "liens fournis par l’export", "Liens lus dans le HTML servi"),
])
def test_methode_source_des_liens(tmp_path, source, attendu, absent):
    methode = slide(run_deck(tmp_path, deck_data(links_source=source)), "Poids des liens")
    assert attendu in methode["text"] and absent not in methode["text"]


@needs_node
def test_couleur_de_marque_claire_reste_visible(tmp_path):
    """m2 : une couleur de marque blanche est assombrie pour tous les tracés (3:1 sur le papier au moins)."""
    out = run_deck(tmp_path, deck_data(brand={"name": "Agence", "logo": None, "color": "#ffffff"}))

    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    assert ratio(out["accent"], "#f6f5f1") >= 3
    assert ratio(out["accent"], "#8f8e88") >= 2          # distinct du gris neutre des barres « valeur »


@needs_node
def test_deck_sans_donnees(tmp_path):
    out = run_deck(tmp_path, None)
    assert len(out["slides"]) == 1 and "indisponibles" in out["slides"][0]["title"]
