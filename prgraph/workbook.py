# -*- coding: utf-8 -*-
"""Classeur report.xlsx mis en forme, lisible tel quel dans un Excel français par un consultant et son client.

Les CSV du dossier de sortie restent au format machine (virgule, point décimal, UTF-8 avec BOM) : ils sont
relus par `prgraph diff` et par d'autres outils. Un CSV à virgules s'ouvre pourtant en une seule colonne dans
un Excel réglé en français ; ce module relit ces CSV (et `graph.json`) et écrit un classeur à onglets :
Synthèse, Plan d'action, Pages, Famine, Gouffres, Segments, Flux entre silos, Modèle, Lisez-moi.

Choix de construction :
- mode « écriture seule » d'openpyxl : la mémoire reste bornée sur un site de 100 000 pages ; les largeurs,
  les volets figés et la mise en page sont donc posés avant la première ligne de chaque onglet ;
- toute cellule texte est forcée en texte (`data_type = "s"`) et, si elle commence par un déclencheur de formule
  (=, +, -, @, tabulation, retour chariot), porte le préfixe d'apostrophe d'Excel (`quotePrefix`) : un titre
  crawlé « =HYPERLINK(...) » s'affiche tel quel et ne devient jamais une formule, même après édition ;
- aucune valeur non finie, aucun caractère de contrôle interdit par le XML, aucun texte au-delà de 32 767
  caractères, au plus 60 000 liens cliquables par onglet : les quatre causes connues du message « Excel a
  détecté un problème dans le contenu » ;
- couleurs limitées au bleu des barres et échelles (#2a78d6, #cde2fb) et au rouge d'alerte (#d03b3b) éclairci ;
  police par défaut du classeur (thème Office).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.fonts import DEFAULT_FONT
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

from .export import neutralise_cell

# ── Limites d'Excel ──────────────────────────────────────────────────────────
MAX_CELL_CHARS = 32767        # au-delà, Excel tronque et propose une réparation
MAX_SHEET_ROWS = 1048576
MAX_LINKS_PER_SHEET = 60000   # Excel refuse plus de 65 530 liens par feuille
MAX_LINK_CHARS = 2000         # adresse d'un lien hypertexte (Excel : 2 079 au plus)
MATRIX_MAX = 40               # silos affichés dans la matrice des flux, le reste regroupé en « autres »
TARGETS_SHOWN = 10            # URL cibles listées dans une cellule du plan d'action

# ── Couleurs (aucune autre couleur décorative) ───────────────────────────────
BLUE = "2A78D6"
BLUE_LIGHT = "CDE2FB"
ALERT = "D03B3B"
ALERT_OPACITY = 0.15
HEADER_FILL = "F2F2F2"        # gris neutre de l'en-tête
RULE_GRAY = "BFBFBF"


def _tint(hex_color: str, opacity: float) -> str:
    """Couleur posée à `opacity` sur fond blanc (Excel n'a pas de transparence)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    mix = [round(255 + (c - 255) * opacity) for c in (r, g, b)]
    return "".join(f"{c:02X}" for c in mix)


ALERT_PALE = _tint(ALERT, ALERT_OPACITY)

# ── Formats numériques (codes OOXML ; Excel les affiche avec la virgule décimale en français) ─
FMT_SCORE = "0"
FMT_COUNT = "#,##0"
FMT_PCT1 = "0.0 %"
FMT_PCT2 = "0.00 %"
FMT_PCT3 = "0.000 %;-0.000 %;0 %"      # part d'une page : trois décimales, « 0 % » plutôt que « 0,000 % »
FMT_DEC2 = "0.00"
FMT_SIGNED2 = "+0.00;-0.00;0.00"
FMT_POINTS = '+0.0" pt";-0.0" pt";0.0" pt"'
FMT_DATE = "dd/mm/yyyy hh:mm"
FMT_GENERAL = "General"

NBSP = " "

# ── Libellés ─────────────────────────────────────────────────────────────────
PRIORITY_LABEL = {"critique": "Critique", "moyen": "Moyen", "optimisation": "Optimisation"}
FAMILY_LABEL_DEFAULT = {"gouffre": "Colmater un gouffre", "famine": "Sauvetage de famine",
                        "cible": "Renforcer une cible choisie", "trou_noir": "Élaguer un trou noir",
                        "menu": "Alléger le gabarit", "orphelines": "Raccrocher des orphelines",
                        "nofollow": "Nofollow interne"}
CLASS_LABEL_DEFAULT = {"money": "Money / conversion", "acquisition": "Acquisition / piliers",
                       "confiance": "Confiance (à propos, presse, FAQ)", "legal_support": "Légal / support",
                       "neutre": "Neutre", "accueil": "Accueil"}
CLASS_SHORT = {"money": "Money", "acquisition": "Acquisition", "confiance": "Confiance",
               "legal_support": "Légal / support", "neutre": "Neutre", "accueil": "Accueil"}
QUADRANT_LABEL = {"puits": "Puits", "hub": "Hub", "passoire": "Passoire", "noeud_mort": "Peu reliée"}
FLAG_LABEL = {"accueil": "accueil", "strategique": "stratégique", "money": "money", "famine": "famine",
              "gouffre": "gouffre", "utilitaire": "utilitaire", "cul-de-sac": "cul-de-sac", "orpheline": "orpheline",
              "inatteignable": "inatteignable", "non-crawlee": "non crawlée"}
BUSINESS_LABEL = {"revenue": "chiffre d'affaires", "totalrevenue": "chiffre d'affaires", "ca": "chiffre d'affaires",
                  "conversions": "conversions", "keyevents": "conversions", "clicks": "clics", "clics": "clics",
                  "impressions": "impressions", "sessions": "sessions"}
POSITION_LABEL = {"content": "Contenu éditorial", "breadcrumb": "Fil d'Ariane", "sidebar": "Barre latérale",
                  "header": "En-tête", "nav": "Menu de navigation", "footer": "Pied de page",
                  "unknown": "Position inconnue", "boilerplate": "Bloc répété (gabarit déduit)"}
TEMPLATE_ZONES = ("header", "nav", "footer", "sidebar", "boilerplate")   # même périmètre que le résumé de la CLI
DANGLING_LABEL = {"uniform": "Redistribution uniforme", "home": "Vers l'accueil"}
PROFILE_LABEL = {"raisonnable": "Surfeur raisonnable", "modere": "Surfeur raisonnable, modéré",
                 "uniform": "Naïf 1998 (tous les liens égaux)"}
STAT_LABEL = {
    "edges_in": ("Liens lus dans l'export", "Lignes du fichier d'arêtes avant nettoyage."),
    "dropped_source_not_canonical": ("Liens écartés (source non canonique)",
                                     "Liens émis par une page qui redirige ou pointe ailleurs en canonical."),
    "lost_404": ("Liens vers des pages en erreur", "Cible en 4xx ou 5xx : le jus est perdu."),
    "nofollow": ("Liens nofollow retirés", "Liens internes marqués nofollow, retirés du graphe."),
    "self_loops": ("Liens d'une page vers elle-même", "Ignorés : ils ne transmettent rien."),
    "redirect_or_canonical_rewrites": ("Liens réécrits (301, canonical)", "Cible remplacée par sa destination finale."),
    "external_or_empty": ("Liens externes ou vides", "Hors du site ou sans adresse exploitable."),
    "duplicates_collapsed": ("Doublons fusionnés", "Plusieurs liens d'une même page vers une même cible comptés une fois."),
    "lost_external": ("Liens vers un autre hôte", "Comptés comme des fuites, jamais comme des pages."),
    "lost_uncrawled": ("Liens vers des pages non crawlées", "Comptés comme des fuites (politique « leak »)."),
    "uncrawled_policy": ("Traitement des pages non crawlées", "leak : le jus vers une page inconnue est une fuite ; nodes : la page devient un nœud sans sortie."),
    "boilerplate_reclassified": ("Liens requalifiés en gabarit", "Liens déclarés dans le contenu mais présents sur une grande part des pages : traités comme un bloc répété."),
    "boilerplate_targets": ("Cibles requalifiées en gabarit", "Nombre de pages cibles concernées (liste en bas de cet onglet)."),
    "boilerplate_protected": ("Cibles protégées de la requalification", "Pages money ou stratégiques jamais requalifiées."),
    "boilerplate_threshold": ("Seuil de requalification (pages émettrices)", "Nombre de pages qui doivent lier une cible depuis le contenu pour qu'elle soit traitée comme du gabarit."),
    "nodes": ("Pages du graphe", "Pages retenues après nettoyage."),
    "edges_out": ("Liens du graphe", "Liens uniques retenus après nettoyage."),
    "redirect_chains_resolved": ("Chaînes de redirection résolues", "Redirections en plusieurs sauts suivies jusqu'au bout."),
    "redirect_loops": ("Boucles de redirection", "Redirections qui tournent en rond : cible abandonnée."),
    "uncrawled_nodes": ("Pages non crawlées gardées en nœuds", "Seulement avec la politique « nodes »."),
    "site_host": ("Hôte du site", "Un lien vers un autre hôte est une fuite."),
}


# ── Lecture des fichiers ─────────────────────────────────────────────────────

def _restore(v: str) -> str:
    """Retire l'apostrophe posée par la neutralisation des CSV : le classeur protège ses cellules autrement
    (texte forcé et préfixe d'apostrophe d'Excel), le texte d'origine s'affiche donc sans le guillemet."""
    if len(v) > 1 and v[0] == "'" and neutralise_cell(v[1:]) != v[1:]:
        return v[1:]
    return v


def _read_csv(path: str) -> tuple[list[str], list[dict]] | None:
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [{k: (_restore(v) if isinstance(v, str) else v) for k, v in r.items() if k is not None} for r in reader]
        return [_restore(h) for h in (reader.fieldnames or [])], rows


def _read_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with io.open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}


def _f(v) -> float | None:
    """Nombre fini, ou None (vide, texte, NaN, infini : Excel répare un fichier qui contient « nan »)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip()
        if not s:
            return None
        try:
            x = float(s)
        except ValueError:
            return None
    return x if math.isfinite(x) else None


def _i(v) -> int | None:
    x = _f(v)
    return int(round(x)) if x is not None else None


def _fr_num(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}".replace(",", NBSP).replace(".", ",")


def _fr_pct(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{_fr_num(100 * x, d)}{NBSP}%"


def _fr_int(n: int | None) -> str:
    return "n/a" if n is None else f"{int(n):,}".replace(",", NBSP)


def _business_label(col: str | None) -> str:
    if not col:
        return "valeur business"
    return BUSINESS_LABEL.get(re.sub(r"[^a-z]", "", col.lower()), col)


def _family_labels() -> dict:
    try:
        from .actions import FAMILY_LABEL
        return {**FAMILY_LABEL_DEFAULT, **FAMILY_LABEL}
    except Exception:  # noqa: BLE001  (module en cours d'évolution : les libellés par défaut suffisent)
        return dict(FAMILY_LABEL_DEFAULT)


@dataclass
class Link:
    """Cellule URL : texte affiché tel quel, lien cliquable seulement pour une adresse http(s) sûre."""
    url: str


@dataclass
class Col:
    header: str
    style: str = "text"       # style des cellules de données
    width: float | None = None  # largeur imposée ; sinon calculée sur le contenu entre min_w et max_w
    min_w: float = 8
    max_w: float = 60
    help: str = ""            # ligne de l'onglet Lisez-moi
    left: bool = False        # nombres alignés à gauche (onglets « indicateur / valeur »)


# ── Styles ───────────────────────────────────────────────────────────────────

def _font(**kw) -> Font:
    base = dict(name=DEFAULT_FONT.name, sz=DEFAULT_FONT.sz, family=DEFAULT_FONT.family, scheme=DEFAULT_FONT.scheme,
                color=copy(DEFAULT_FONT.color))
    base.update(kw)
    return Font(**base)


# Texte aligné à gauche avec un retrait (un cran = trois espaces) : un nombre aligné à droite n'est jamais collé au
# texte de la colonne suivante. Le retrait à droite des nombres n'est pas utilisé : LibreOffice l'ignore.
_TOP = dict(horizontal="left", vertical="top", indent=1)
_WRAP = dict(horizontal="left", vertical="top", indent=1, wrap_text=True)
STYLE_SPECS = {
    "header": dict(font=_font(b=True), fill=HEADER_FILL, align=dict(horizontal="left", vertical="center", indent=1,
                                                                    wrap_text=True), border=True),
    "text": dict(align=_TOP),
    "text_r": dict(align=dict(horizontal="right", vertical="top")),
    "wrap": dict(align=_WRAP),
    "bold": dict(font=_font(b=True), align=_TOP),
    "bold_wrap": dict(font=_font(b=True), align=_WRAP),
    "note": dict(font=_font(i=True), align=_TOP),
    "alert": dict(fill=ALERT_PALE, align=_TOP),
    "link": dict(font=_font(color=BLUE, u="single"), align=_TOP),
    "link_wrap": dict(font=_font(color=BLUE, u="single"), align=_WRAP),
    "date": dict(fmt=FMT_DATE, align=_TOP),
}
TEXT_STYLES = {"header", "text", "text_r", "wrap", "bold", "bold_wrap", "note", "alert", "link", "link_wrap"}
_NUMBER_FORMATS = {"score": FMT_SCORE, "count": FMT_COUNT, "pct1": FMT_PCT1, "pct2": FMT_PCT2, "pct3": FMT_PCT3,
                   "dec2": FMT_DEC2, "signed2": FMT_SIGNED2, "points": FMT_POINTS, "general": FMT_GENERAL}
for _k, _fmt in _NUMBER_FORMATS.items():
    STYLE_SPECS[_k] = dict(fmt=_fmt, align=dict(horizontal="right", vertical="top"))
    STYLE_SPECS[_k + "_l"] = dict(fmt=_fmt, align=_TOP)
NUMBER_STYLES = {k for k in STYLE_SPECS if k not in TEXT_STYLES}
_STEP = re.compile(r" (?=\d{1,2}\. depuis )")    # étapes « 1. depuis /p/... » d'une consigne : une par ligne


class _Styles:
    """Tableaux de styles calculés une fois par classeur puis copiés dans chaque cellule (rapide en écriture seule)."""

    def __init__(self):
        self._cache: dict[tuple[str, bool], object] = {}

    def get(self, ws, key: str, quote_prefix: bool = False):
        k = (key, quote_prefix)
        if k not in self._cache:
            spec = STYLE_SPECS[key]
            c = WriteOnlyCell(ws)
            if "font" in spec:
                c.font = spec["font"]
            if "fill" in spec:
                c.fill = PatternFill(fill_type="solid", start_color=spec["fill"], end_color=spec["fill"])
            if "align" in spec:
                c.alignment = Alignment(**spec["align"])
            if spec.get("border"):
                c.border = Border(bottom=Side(style="thin", color=RULE_GRAY))
            if "fmt" in spec:
                c.number_format = spec["fmt"]
            if quote_prefix:
                c.quotePrefix = True
            self._cache[k] = c._style
        return copy(self._cache[k])


# ── Écriture ─────────────────────────────────────────────────────────────────

def _clean_text(v) -> str:
    s = ILLEGAL_CHARACTERS_RE.sub("", str(v))
    if len(s) > MAX_CELL_CHARS:
        s = s[: MAX_CELL_CHARS - 1] + "…"
    return s


_SAFE_URL = re.compile(r"^https?://[^\s\"<>\\^`{|}]+$", re.I)


def _link_target(url: str) -> str | None:
    """Adresse cliquable : http(s) seulement, sans espace ni caractère de contrôle, encodée en ASCII."""
    if not _SAFE_URL.match(url) or len(url) > MAX_LINK_CHARS:
        return None
    return quote(url, safe=":/?#[]@!$&'()*+,;=%~-._")


class _Book:
    def __init__(self, path: str, out_dir: str):
        self.path = path
        self.out_dir = out_dir
        self.wb = Workbook(write_only=True)
        self.styles = _Styles()
        self.doc: list[tuple[str, str, str]] = []   # (onglet, colonne ou terme, explication) pour Lisez-moi
        self._links = 0

    # cellules ---------------------------------------------------------------
    def cell(self, ws, v, style: str = "text", left: bool = False):
        """Cellule typée. `v` peut être un tuple (valeur, style) pour imposer un style à une seule cellule ;
        `left` aligne les nombres à gauche (colonne « Valeur » d'un onglet indicateur / valeur)."""
        if isinstance(v, tuple):
            v, style = v
        if v is None:
            return None
        if isinstance(v, Link):
            return self._link_cell(ws, v.url, style)
        if isinstance(v, bool):
            v = "oui" if v else "non"
        if isinstance(v, datetime):
            c = WriteOnlyCell(ws, value=v)
            c._style = self.styles.get(ws, "date")
            return c
        if isinstance(v, (int, float)):
            x = _f(v)
            if x is None:
                return None
            key = style if style in NUMBER_STYLES else "general"
            if left and not key.endswith("_l"):
                key += "_l"
            c = WriteOnlyCell(ws, value=int(v) if isinstance(v, int) else x)
            c._style = self.styles.get(ws, key)
            return c
        return self._text_cell(ws, v, style if style in TEXT_STYLES else ("wrap" if style.endswith("wrap") else "text"))

    def _text_cell(self, ws, v, style: str):
        s = _clean_text(v)
        if not s:
            return None
        c = WriteOnlyCell(ws)
        c.value = s
        c.data_type = "s"        # jamais une formule, même si le texte commence par « = »
        c._style = self.styles.get(ws, style, quote_prefix=neutralise_cell(s) != s)
        return c

    def _link_cell(self, ws, url: str, style: str):
        url = _clean_text(url)
        target = _link_target(url) if self._links < MAX_LINKS_PER_SHEET else None
        if target is None:
            return self._text_cell(ws, url, "wrap" if style.endswith("wrap") else "text")
        c = WriteOnlyCell(ws)
        c.value = url
        c.data_type = "s"
        c.hyperlink = target
        c._style = self.styles.get(ws, "link_wrap" if style.endswith("wrap") else "link")
        self._links += 1
        return c

    # onglets ----------------------------------------------------------------
    def sheet(self, title: str, widths: list[float], freeze: str | None = "A2", landscape: bool = True):
        """Crée l'onglet et pose ce que le mode écriture seule exige avant la première ligne :
        largeurs, volets figés, propriétés d'impression."""
        ws = self.wb.create_sheet(title=title[:31])
        self._links = 0
        self._widths = getattr(self, "_widths", {})
        self._widths[ws.title] = list(widths)
        self._rows = getattr(self, "_rows", {})
        self._rows[ws.title] = 0
        for k, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(k)].width = round(w, 1)
        if freeze:
            ws.freeze_panes = freeze
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.page_setup.orientation = "landscape" if landscape else "portrait"
        ws.page_setup.paperSize = 9          # A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0        # autant de pages que nécessaire en hauteur
        ws.print_title_rows = "1:1"
        ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.55, bottom=0.55, header=0.25, footer=0.25)
        ws.HeaderFooter.oddFooter.center.text = "&A, page &P sur &N"
        return ws

    LINE_HEIGHT = 15.0      # points par ligne de texte en police par défaut (Calibri 11 / Carlito)

    def _row_height(self, ws, values, styles) -> float | None:
        """Hauteur estimée d'une ligne dont une cellule passe à la ligne. Excel n'ajuste pas à l'ouverture la
        hauteur d'une ligne écrite par un programme, et LibreOffice la garde à une ligne : sans hauteur posée, les
        textes longs se chevauchent (constaté au rendu). L'estimation compte large (largeur d'un caractère moyen
        un peu au-dessus de celle de « 0 », unité de largeur d'Excel) : mieux vaut une ligne de blanc qu'un
        texte coupé."""
        widths = self._widths.get(ws.title, [])
        lines = 1
        for k, v in enumerate(values):
            st = styles[k] if k < len(styles) else "text"
            if not isinstance(v, str) or "wrap" not in st or k >= len(widths):
                continue
            # Mesuré au rendu LibreOffice (Carlito, métriques de Calibri) : environ 1,2 caractère de texte courant
            # par unité de largeur ; 1,15 garde une petite marge pour les mots longs qui passent à la ligne.
            per_line = max(4.0, widths[k] * 1.15)
            n = sum(max(1, -(-len(par) // int(per_line))) for par in v.split("\n"))
            lines = max(lines, n)
        return None if lines <= 1 else lines * self.LINE_HEIGHT + 3

    def append(self, ws, values, styles=None, lefts=None):
        """Ajoute une ligne, avec une hauteur estimée quand une cellule passe à la ligne (voir `_row_height`)."""
        styles = styles or ["text"] * len(values)
        lefts = lefts or [False] * len(values)
        self._rows[ws.title] = self._rows.get(ws.title, 0) + 1
        h = self._row_height(ws, values, styles)
        if h:
            ws.row_dimensions[self._rows[ws.title]].height = round(h, 1)
        ws.append([self.cell(ws, v, styles[k] if k < len(styles) else "text", lefts[k] if k < len(lefts) else False)
                   for k, v in enumerate(values)])

    def blank(self, ws):
        self._rows[ws.title] = self._rows.get(ws.title, 0) + 1
        ws.append([])

    def table(self, title: str, cols: list[Col], rows: list[list], *, freeze: str = "A2", bars=(), alert_on=None,
              empty: str | None = None, notes: list[str] | None = None, doc: bool = True):
        """Onglet tabulaire : en-tête, lignes, filtre automatique, barres de données sur les colonnes `bars`
        ((index, min, max) ; max None = maximum de la colonne), fond rouge pâle sur les lignes dont la colonne
        `alert_on` = (index, mot) contient le mot. `empty` : ligne affichée quand il n'y a aucune donnée."""
        rows = rows[: MAX_SHEET_ROWS - 3]
        widths = [self._width(c, k, rows) for k, c in enumerate(cols)]
        ws = self.sheet(title, widths, freeze=freeze)
        self.append(ws, [c.header for c in cols], ["header"] * len(cols))
        if not rows and empty:
            self.append(ws, [empty], ["note"])
        for r in rows:
            self.append(ws, r, [c.style for c in cols], [c.left for c in cols])
        last_col = get_column_letter(len(cols))
        last_row = 1 + max(len(rows), 1 if (empty and not rows) else 0)
        ws.auto_filter.ref = f"A1:{last_col}{max(last_row, 1)}"
        if rows:
            for k, lo, hi in bars:
                letter = get_column_letter(k + 1)
                rule = (DataBarRule(start_type="num", start_value=lo, end_type="num", end_value=hi, color=BLUE)
                        if hi is not None else
                        DataBarRule(start_type="num", start_value=lo, end_type="max", color=BLUE))
                ws.conditional_formatting.add(f"{letter}2:{letter}{last_row}", rule)
            if alert_on is not None:
                k, word = alert_on
                letter = get_column_letter(k + 1)
                fill = PatternFill(fill_type="solid", start_color=ALERT_PALE, end_color=ALERT_PALE)
                ws.conditional_formatting.add(f"A2:{last_col}{last_row}",
                                              FormulaRule(formula=[f'ISNUMBER(SEARCH("{word}",${letter}2))'], fill=fill))
        if notes:
            self.blank(ws)
            for n in notes:
                self.append(ws, [n], ["note"])
        if doc:
            for c in cols:
                if c.help:
                    self.doc.append((title, c.header, c.help))
        return ws

    @staticmethod
    def _width(col: Col, k: int, rows: list[list]) -> float:
        if col.width is not None:
            return col.width
        # en-tête en retour à la ligne : son mot le plus long, plus le retrait et la flèche du filtre automatique
        longest = max((len(w) for w in col.header.split()), default=4) + 4
        for r in rows[:3000]:
            if k >= len(r):
                continue
            v = r[k]
            if isinstance(v, tuple):
                v = v[0]
            if isinstance(v, Link):
                v = v.url
            if v is None:
                continue
            if isinstance(v, str):
                n = max((len(line) for line in v.split("\n")), default=0) + 1     # retrait du texte
            elif isinstance(v, (int, float)):
                x = _f(v) or 0.0
                n = {"pct1": len(f"{100 * x:,.1f} %"), "pct2": len(f"{100 * x:,.2f} %"), "pct3": len(f"{100 * x:,.3f} %"),
                     "count": len(f"{x:,.0f}"), "score": len(f"{x:.0f}"), "dec2": len(f"{x:.2f}"),
                     "signed2": len(f"{x:+.2f}"), "points": len(f"{x:+.1f} pt")}.get(col.style, len(f"{x:.6g}"))
                n += 1                       # marge : un nombre trop large s'afficherait « ##### »
            else:
                n = 10
            longest = max(longest, n + 2)
        return float(min(col.max_w, max(col.min_w, longest)))

    def save(self):
        self.wb.save(self.path)


# ── Données du dossier de sortie ─────────────────────────────────────────────

class _Data:
    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        g = _read_json(os.path.join(out_dir, "graph.json"))
        self.graph_found = bool(g)
        self.graph = g if isinstance(g, dict) else {}
        self.meta = g.get("meta") or {}
        self.alignment = g.get("alignment") or {}
        self.zones = g.get("zones") or {}
        self.scenario = g.get("scenario") or None
        self.json_actions = {str(a.get("id")): a for a in (g.get("actions") or []) if isinstance(a, dict)}
        self.pages_csv = _read_csv(os.path.join(out_dir, "pages.csv"))
        self.segments_csv = _read_csv(os.path.join(out_dir, "segments.csv"))
        self.flows_csv = _read_csv(os.path.join(out_dir, "flows_segments.csv"))
        self.actions_csv = _read_csv(os.path.join(out_dir, "actions.csv"))
        self.class_labels = {**CLASS_LABEL_DEFAULT, **(self.meta.get("class_labels") or {})}
        self.pages = self._pages()
        self.value_col = self.meta.get("value_col")
        self.has_value = bool(self.value_col) or any((p["value"] or 0) > 0 for p in self.pages)

    def _pages(self) -> list[dict]:
        if not self.pages_csv:
            return []
        header, rows = self.pages_csv
        known = {"url", "segment", "classe", "score", "pr_share_pct", "pagerank", "cheirank_score", "cheirank", "quadrant",
                 "in_links", "out_links", "click_depth", "value", "value_share", "flags", "leak_fraction", "title"}
        self.demand_key = next((h for h in header if h.startswith("demand_")), None)
        self.extra_keys = [h for h in header if h not in known and h != self.demand_key]
        out = []
        for rank, r in enumerate(rows, 1):
            share = _f(r.get("pr_share_pct"))
            if share is None and _f(r.get("pagerank")) is not None:
                share = 100 * _f(r.get("pagerank"))
            out.append({
                "rank": rank, "url": r.get("url") or "", "title": r.get("title") or "", "segment": r.get("segment") or "",
                "classe": r.get("classe") or "", "score": _f(r.get("score")),
                "share": share / 100 if share is not None else None, "chei": _f(r.get("cheirank_score")),
                "quadrant": r.get("quadrant") or "", "in": _i(r.get("in_links")), "out": _i(r.get("out_links")),
                "depth": _i(r.get("click_depth")), "value": _f(r.get("value")), "vshare": _f(r.get("value_share")),
                "flags": [f for f in (r.get("flags") or "").split("|") if f], "leak": _f(r.get("leak_fraction")),
                "demand": _f(r.get(self.demand_key)) if self.demand_key else None,
                "extra": [r.get(k) for k in self.extra_keys],
            })
        return out

    def count_flag(self, flag: str) -> int | None:
        if not self.pages_csv or "flags" not in self.pages_csv[0]:
            return None
        return sum(1 for p in self.pages if flag in p["flags"])


# ── Onglets ──────────────────────────────────────────────────────────────────

def _synthese(b: _Book, d: _Data) -> None:
    m, al = d.meta, d.alignment
    cols = [Col("Indicateur", width=40), Col("Valeur", width=16), Col("Repère", width=18), Col("Lecture", width=78)]
    absent = "Donnée absente : graph.json introuvable"
    rows: list[list] = []
    styles = ["bold_wrap", "text", "text", "wrap"]

    def row(label, value, ref=None, reading=None):
        rows.append([label, value, ref, reading])

    row("Site", m.get("site") or (absent if not d.graph_found else "n/a"))
    gen = m.get("generated")
    try:
        gen_dt = datetime.fromisoformat(gen) if gen else None
    except ValueError:
        gen_dt = None
    # En texte : une date Excel dans une colonne de largeur 16 s'affiche « ### » selon le format régional.
    row("Date de l'analyse", gen_dt.strftime("%d/%m/%Y %H:%M") if gen_dt else (gen or "n/a"), None,
        "Date de production des fichiers de cette analyse.")
    row("Version de l'outil", f"prgraph {m['tool_version']}" if m.get("tool_version") else "n/a", None,
        "Version du calcul : deux analyses ne se comparent qu'avec la même version et les mêmes réglages.")
    if al:
        score = _f(al.get("score"))
        row("Score d'alignement business", (score, "score") if score is not None else "n/a", "/ 100",
            f"Barème : {al.get('scale') or m.get('alignment_scale') or 'n/a'}. Base du calcul : "
            f"{al.get('basis') or 'n/a'}. 100 = le PageRank se répartit entre les classes de pages comme la valeur.")
        ws_ = al.get("worst_segment")
        if ws_:
            gap = (_f(ws_.get("value_share")) or 0.0) - (_f(ws_.get("pr_share")) or 0.0)
            row("Pire écart de segment", str(ws_.get("segment")), f"{'+' if gap >= 0 else '-'}{_fr_num(abs(100 * gap))} points",
                f"Le segment {ws_.get('segment')} reçoit {_fr_pct(_f(ws_.get('pr_share')))} du PageRank pour "
                f"{_fr_pct(_f(ws_.get('value_share')))} de la valeur : le principal démenti du score par classe.")
        else:
            row("Pire écart de segment", "aucun", None, "Aucun segment ne reçoit moins de PageRank que sa part de la valeur, "
                "ou pas de données business.")
        wf = al.get("worst_family")
        if wf and len(wf.get("segments") or []) > 1:
            gapf = (_f(wf.get("value_share")) or 0.0) - (_f(wf.get("pr_share")) or 0.0)
            row("Pire écart de rubrique (toutes langues)", str(wf.get("family")), f"+{_fr_num(abs(100 * gapf))} points",
                f"La rubrique {wf.get('family')} ({', '.join(map(str, wf.get('segments') or []))}) reçoit "
                f"{_fr_pct(_f(wf.get('pr_share')))} du PageRank pour {_fr_pct(_f(wf.get('value_share')))} de la valeur : "
                "le chiffre du mode Présentation.")
        vp = _f(al.get("value_pages_pr_share"))
        if vp is not None:
            row(f"Part du PageRank sur les pages qui portent {_fr_num(100 * (_f(al.get('value_pages_value_share')) or 0.8), 0)} % de la valeur",
                (vp, "pct1"), f"{al.get('value_pages_count', 0)} pages",
                "Les pages qui cumulent l'essentiel de la valeur mesurée : la seule mesure qui dit si l'autorité va à ce qui rapporte.")
        sp = _f(al.get("spearman", m.get("spearman")))
        sp = round(sp, 2) + 0.0 if sp is not None else None     # jamais « -0,00 »
        row("Corrélation de rang valeur / score", (sp, "signed2") if sp is not None else "n/a", "de -1 à +1",
            "Corrélation de Spearman entre la valeur business des pages et leur score : proche de +1, les pages qui "
            "rapportent sont les mieux servies ; proche de 0, le maillage ignore la valeur ; négative, il sert d'abord "
            "les pages qui rapportent le moins.")
        row("Part du PageRank sur money et acquisition", (_f(al.get("productive_share")), "pct1"), None,
            "Types de pages classés par leur adresse (money, acquisition, piliers). Pour savoir si l'autorité va à ce qui rapporte, lire la ligne des pages qui portent 80 % de la valeur.")
        row("Part du PageRank sur légal et support", (_f(al.get("waste_share")), "pct1"), None,
            "Mentions légales, CGV, panier, compte : utiles, mais elles ne rapportent pas de trafic qualifié.")
    else:
        row("Score d'alignement business", "Donnée absente : alignement non calculé", None, None)
    total = sum(v for v in (_f(x) for x in d.zones.values()) if v is not None)
    if d.zones and total > 0:
        tmpl = sum(_f(d.zones.get(z)) or 0.0 for z in TEMPLATE_ZONES) / total
        row("Part du jus dans le gabarit", (tmpl, "pct1"), None,
            "Part du PageRank transmise par les liens répétés (en-tête, menu, pied de page, barre latérale, blocs "
            "répétés) plutôt que par les liens du texte. Un changement de gabarit la déplace d'un coup.")
        row("Part du jus dans le contenu éditorial", ((_f(d.zones.get("content")) or 0.0) / total, "pct1"), None,
            "Part transmise par les liens du texte des pages.")
        row("Part perdue (fuites et culs-de-sac)", ((_f(d.zones.get("fuites_et_culs_de_sac")) or 0.0) / total, "pct1"), None,
            "Jus qui part vers des pages non crawlées, cassées, externes, ou qui entre dans une page sans lien sortant.")
    else:
        row("Part du jus dans le gabarit", "Donnée absente : zones de flux introuvables", None, None)
    n_pages = _i(m.get("n_nodes")) if m.get("n_nodes") is not None else (len(d.pages) if d.pages_csv else None)
    row("Pages analysées", (n_pages, "count") if n_pages is not None else "n/a", None, "Pages du graphe après nettoyage.")
    n_edges = _i(m.get("n_edges"))
    row("Liens internes", (n_edges, "count") if n_edges is not None else "n/a", None,
        "Liens uniques entre pages après nettoyage (301 résolues, doublons fusionnés, nofollow retirés).")
    for flag, label, reading in (
            ("famine", "Pages en famine", "Pages qui rapportent (ou choisies comme cibles) mais reçoivent peu de jus : onglet Famine."),
            ("gouffre", "Gouffres", "Pages utilitaires qui captent beaucoup de jus sans rapporter : onglet Gouffres."),
            ("cul-de-sac", "Culs-de-sac", "Pages sans lien sortant suivi : le jus y entre et ne repart pas."),
            ("orpheline", "Orphelines (plancher)", "Pages qu'aucun lien crawlé ne cible ; un crawl ne trouve pas ses propres "
                                                   "orphelines, le vrai nombre peut être plus élevé.")):
        n = d.count_flag(flag)
        if n is None:
            row(label, "Donnée absente : colonne flags de pages.csv", None, None)
            continue
        ref = f"{_fr_pct(n / len(d.pages))} des pages" if d.pages else None
        row(label, (n, "count"), ref, reading)

    sc = d.scenario
    sc_rows: list[list] = []
    if sc and isinstance(sc.get("before"), dict) and isinstance(sc.get("after"), dict):
        bf, af = sc["before"], sc["after"]

        def delta(a, b_, pct):
            if a is None or b_ is None:
                return None
            x = abs(100 * (b_ - a) if pct else b_ - a)
            digits = 2 if 0 < x < 0.1 else 1          # un écart de 0,04 point ne s'affiche pas « +0,0 »
            return (f"{'+' if b_ >= a else '-'}{_fr_num(x, digits)} point{'s' if x >= 2 else ''}")

        def pct_style(a, z):
            # parts de moins de 5 % : deux décimales, sinon l'avant et l'après s'affichent identiques
            return "pct2" if max(abs(a or 0.0), abs(z or 0.0)) < 0.05 else "pct1"

        for key, label, style, pct in (("alignment", "Score d'alignement business", "score", False),
                                       ("productive_share", "Part du PageRank sur money et acquisition", None, True),
                                       ("waste_share", "Part du PageRank sur légal et support", None, True),
                                       ("starving_pr_share", "Part du PageRank des pages en famine", None, True)):
            a, z = _f(bf.get(key)), _f(af.get(key))
            if a is None and z is None:
                continue
            st = style or pct_style(a, z)
            sc_rows.append([label, (a, st), (z, st), delta(a, z, pct)])
        wb_, wa = bf.get("worst_segment") or {}, af.get("worst_segment") or {}
        if wb_ or wa:
            a, z = _f(wb_.get("pr_share")), _f(wa.get("pr_share"))
            seg = wb_.get("segment") or wa.get("segment")
            st = pct_style(a, z)
            sc_rows.append([f"Part du PageRank du segment {seg}", (a, st), (z, st),
                            (delta(a, z, True) or "") + f" ; ce segment porte {_fr_pct(_f(wb_.get('value_share') or wa.get('value_share')))} de la valeur"])
        for key, label in (("removed", "Liens retirés par la simulation"), ("moved", "Liens déplacés en pied de page")):
            if _i(sc.get(key)):
                sc_rows.append([label, None, (_i(sc.get(key)), "count"), None])
        changes = [str(c) for c in (sc.get("changes") or [])]
        if changes:
            sc_rows.append(["Chantiers simulés", None, None, "\n".join(f"{k}. {c}" for k, c in enumerate(changes, 1))])
        if sc.get("note"):
            sc_rows.append(["Avertissement", None, None, str(sc["note"])])

    lefts = [False, True, True, False]      # valeurs alignées à gauche : jamais collées au repère voisin
    ws = b.sheet("Synthèse", [c.width for c in cols], freeze="A2")
    b.append(ws, [c.header for c in cols], ["header"] * 4)
    for r in rows:
        b.append(ws, r, styles, lefts)
    if sc_rows:
        b.blank(ws)
        b.append(ws, [f"Avant / après : {sc.get('label') or 'chantiers de gabarit'}", "Aujourd'hui", "Après chantiers", "Lecture"],
                 ["header"] * 4)
        for r in sc_rows:
            b.append(ws, r, styles, lefts)
    b.doc += [("Synthèse", "Indicateur, Valeur, Repère, Lecture",
               "Les chiffres clés de l'analyse : la valeur, un repère pour la situer (barème, part des pages) et une "
               "phrase de lecture en langage courant."),
              ("Synthèse", "Avant / après",
               "Présent quand l'analyse simule les chantiers de gabarit appliqués ensemble : situation actuelle, "
               "situation après chantiers et écart. Sortie du modèle, pas une prédiction de trafic.")]


def _plan(b: _Book, d: _Data) -> None:
    fam = _family_labels()
    cols = [Col("Ticket", min_w=9, help="Identifiant du chantier, repris dans brief_dev.md et le rapport HTML."),
            Col("Priorité", min_w=12, help="Critique, Moyen ou Optimisation, selon le gain simulé. Les tickets critiques "
                                           "sont sur fond rouge pâle."),
            Col("Victoire rapide", min_w=10, help="« oui » pour les cinq tickets au meilleur rapport gain / effort."),
            Col("Type de chantier", "wrap", min_w=14, max_w=20, help="Famille du ticket : gabarit (menus, pied de page) ou "
                                                                   "éditorial (liens posés page par page)."),
            Col("Intitulé", "wrap", width=32, help="Ce qu'il faut faire, en une phrase."),
            Col("Effort", "wrap", width=24, help="Gabarit : un composant global, modifié une fois pour toutes les pages. "
                                                 "Éditorial : liens à poser page par page. Durées : ordres de grandeur à confirmer."),
            Col("Cible", "wrap", width=36, help="Pages visées par le ticket (dix au plus ici ; liste complète dans actions.csv)."),
            Col("Problème", "wrap", width=40, help="Le constat qui motive le ticket."),
            Col("Consigne", "wrap", width=52, help="La consigne à appliquer, telle que remise à l'équipe technique ou éditoriale."),
            Col("Gain estimé", "wrap", width=28, help="Effet simulé par le modèle de PageRank interne : un ordre de priorité, "
                                                      "pas une prédiction de positions ni de trafic."),
            Col("Gain (points)", "dec2", min_w=9, help="Points de PageRank gagnés par les pages money et acquisition "
                                                       "(sur 100), pour trier les tickets."),
            Col("Liens retirés", "count", min_w=9, help="Liens que la consigne retire ou déplace."),
            Col("Liens ajoutés", "count", min_w=9, help="Liens que la consigne ajoute."),
            Col("Pages émettrices", "wrap", width=36, help="Pages proposées pour porter les nouveaux liens, choisies dans le "
                                                           "même silo que la cible.")]

    def targets(urls: list[str]) -> str | None:
        urls = [u for u in urls if u]
        if not urls:
            return None
        txt = "\n".join(urls[:TARGETS_SHOWN])
        if len(urls) > TARGETS_SHOWN:
            txt += f"\n(+{_fr_int(len(urls) - TARGETS_SHOWN)} autres, voir actions.csv)"
        return txt

    def effort_text(a_json: dict, raw: str | None) -> str | None:
        if raw:
            return raw
        eff = a_json.get("effort") if isinstance(a_json, dict) else None
        if not isinstance(eff, dict):
            return None
        head = str(eff.get("label") or "")
        if eff.get("who"):
            head += f"{', ' if head else ''}équipe {eff['who']}"
        txt = "\n".join(p for p in (head, str(eff.get("estimate") or "")) if p)
        return txt or None

    def steps(text: str | None) -> str | None:
        return _STEP.sub("\n", text) if text else text

    rows: list[list] = []
    src = d.actions_csv
    if src:
        _, acts = src
        for r in acts:
            tid = r.get("ticket") or ""
            aj = d.json_actions.get(tid, {})
            prio = (r.get("priorite") or aj.get("priority") or "").strip()
            family = (r.get("famille") or aj.get("family") or "").strip()
            label = PRIORITY_LABEL.get(prio, prio or None)
            rows.append([tid, (label, "alert") if prio == "critique" else label,
                         "oui" if aj.get("quick_win") else None,
                         r.get("type_chantier") or fam.get(family, family or None), r.get("titre") or aj.get("title"),
                         effort_text(aj, r.get("effort")),
                         targets((r.get("cibles") or "").split(" | ")), r.get("probleme"), steps(r.get("action")),
                         r.get("gain"), _f(r.get("gain_pts_money_acquisition")), _i(r.get("liens_retires")),
                         _i(r.get("liens_ajoutes")), targets((r.get("pages_emettrices") or "").split(" | "))])
    elif d.json_actions:
        for tid, a in d.json_actions.items():
            prio = a.get("priority") or ""
            label = PRIORITY_LABEL.get(prio, prio or None)
            rows.append([tid, (label, "alert") if prio == "critique" else label, "oui" if a.get("quick_win") else None,
                         fam.get(a.get("family"), a.get("family")), a.get("title"), effort_text(a, None),
                         targets(a.get("target_urls") or []), a.get("problem"), steps(a.get("action")), a.get("gain_text"),
                         _f(a.get("gain_pts")), _i(a.get("removed")), _i(a.get("added")), targets(a.get("donor_urls") or [])])
    b.table("Plan d'action", cols, rows, freeze="B2",
            empty="Donnée absente : aucun plan d'action (analyse lancée sans plan, ou actions.csv introuvable).")


def _page_cols(d: _Data) -> tuple[list[Col], callable]:
    biz = _business_label(d.value_col)
    cols = [Col("Rang", "count", min_w=7, help="Classement de la page par score, de la mieux servie (1) à la moins bien servie."),
            Col("URL", "link", min_w=30, max_w=60, help="Adresse de la page, cliquable."),
            Col("Titre", "text", min_w=20, max_w=40, help="Titre de la page relevé au crawl."),
            Col("Segment", min_w=10, max_w=22, help="Rubrique du site (premier dossier de l'URL ou règle fournie)."),
            Col("Classe", min_w=10, max_w=18, help="Rôle business de la page : money, acquisition, confiance, légal / support, "
                                                   "neutre, accueil (voir Définitions)."),
            Col("Score", "score", min_w=8, help="Score de 0 à 100, position relative de la page dans ce rapport (voir "
                                                "Définitions) : 100 = la mieux servie, 0 = la moins bien servie."),
            Col("Part d'équité", "pct3", min_w=10, help="Part du PageRank interne total reçue par la page ; la somme sur le "
                                                        "site vaut 100 %. Comparable d'une analyse à l'autre."),
            Col("Signaux", min_w=10, max_w=30, help="Drapeaux de la page : famine, gouffre, cul-de-sac, orpheline, "
                                                    "inatteignable, non crawlée, money, stratégique, utilitaire, accueil.")]
    if d.has_value:
        cols += [Col(f"Valeur ({biz})", "count", min_w=11, help=f"Valeur business de la page ({biz}) sur la période des "
                                                                f"données fournies."),
                 Col("Part de la valeur", "pct3", min_w=10, help="Part de la valeur business totale du site portée par la page.")]
    if getattr(d, "demand_key", None):
        dl = _business_label(d.demand_key[len("demand_"):])
        cols.append(Col(f"Demande ({dl})", "count", min_w=11, help=f"Demande lue dans les données business ({dl}) : "
                                                                  "repère les pages très cherchées mais peu servies."))
    cols += [Col("Liens entrants", "count", min_w=9, help="Nombre de pages qui lient cette page."),
             Col("Liens sortants", "count", min_w=9, help="Nombre de pages que cette page lie."),
             Col("Profondeur (clics)", "count", min_w=11, help="Nombre minimal de clics depuis l'accueil ; « inatteignable » "
                                                               "si aucun chemin de liens n'y mène."),
             Col("Score de diffusion", "score", min_w=11, help="CheiRank, de 0 à 100 : capacité de la page à redistribuer "
                                                               "du jus par ses liens sortants, sur la même échelle relative."),
             Col("Profil de liens", min_w=12, max_w=20, help="Croisement score / score de diffusion, coupé à la médiane : "
                                                             "puits, hub, passoire, peu reliée (voir Définitions)."),
             Col("Jus perdu", "pct1", min_w=9, help="Part du jus de la page qui ne suit aucun lien du graphe (cibles non "
                                                    "crawlées, cassées ou externes) ; 100 % pour un cul-de-sac.")]
    cols += [Col(k, "general", min_w=10, max_w=24, help="Colonne ajoutée à pages.csv par l'analyse, reprise telle quelle.")
             for k in getattr(d, "extra_keys", [])]

    def to_row(p: dict) -> list:
        depth = p["depth"]
        r = [p["rank"], Link(p["url"]) if p["url"] else None, p["title"] or None, p["segment"] or None,
             CLASS_SHORT.get(p["classe"], p["classe"]) or None, p["score"], p["share"],
             ", ".join(FLAG_LABEL.get(f, f) for f in p["flags"]) or None]
        if d.has_value:
            r += [p["value"], p["vshare"]]
        if getattr(d, "demand_key", None):
            r.append(p["demand"])
        r += [p["in"], p["out"], ("inatteignable", "text_r") if depth is not None and depth < 0 else depth, p["chei"],
              QUADRANT_LABEL.get(p["quadrant"], p["quadrant"]) or None, p["leak"]]
        for v in p["extra"]:
            x = _f(v)
            r.append(x if x is not None else (v or None))
        return r

    return cols, to_row


def _pages_sheets(b: _Book, d: _Data) -> None:
    if not d.pages_csv:
        for t in ("Pages", "Famine", "Gouffres"):
            b.table(t, [Col("URL", width=60)], [], empty="Donnée absente : pages.csv introuvable dans le dossier de sortie.",
                    doc=False)
        return
    cols, to_row = _page_cols(d)
    score_k = next(k for k, c in enumerate(cols) if c.header == "Score")
    flags_k = next(k for k, c in enumerate(cols) if c.header == "Signaux")
    b.table("Pages", cols, [to_row(p) for p in d.pages], freeze="C2", bars=[(score_k, 0, 100)],
            alert_on=(flags_k, "famine"))
    has_flags = "flags" in d.pages_csv[0]
    starving = [p for p in d.pages if "famine" in p["flags"]]
    starving.sort(key=lambda p: (-(p["value"] or 0.0), p["score"] if p["score"] is not None else 0.0))
    reason = ("Donnée absente : colonne flags de pages.csv." if not has_flags else
              "Aucune page en famine : pas de données business ni de pages stratégiques, ou toutes sont au-dessus du seuil."
              if not d.has_value else "Aucune page en famine dans cette analyse.")
    b.table("Famine", cols, [to_row(p) for p in starving], freeze="C2", bars=[(score_k, 0, 100)], empty=reason, doc=False,
            notes=["Tri : valeur décroissante, puis score croissant (les pages qui rapportent le plus et reçoivent le moins "
                   "en premier)."] if starving else None)
    sinks = [p for p in d.pages if "gouffre" in p["flags"]]
    sinks.sort(key=lambda p: ((p["value"] or 0.0), -(p["score"] if p["score"] is not None else 0.0)))
    b.table("Gouffres", cols, [to_row(p) for p in sinks], freeze="C2", bars=[(score_k, 0, 100)], doc=False,
            empty=("Donnée absente : colonne flags de pages.csv." if not has_flags else
                   "Aucun gouffre : aucune page utilitaire au-dessus du seuil haut."),
            notes=["Tri : valeur croissante, puis score décroissant (les pages utilitaires qui ne rapportent rien et captent "
                   "le plus de jus en premier)."] if sinks else None)
    b.doc += [("Famine", "Toutes les colonnes", "Mêmes colonnes que l'onglet Pages, limitées aux pages en famine."),
              ("Gouffres", "Toutes les colonnes", "Mêmes colonnes que l'onglet Pages, limitées aux gouffres.")]


def _segments(b: _Book, d: _Data) -> None:
    cols = [Col("Segment", min_w=12, max_w=28, help="Rubrique du site."),
            Col("Pages", "count", min_w=8, help="Nombre de pages du segment."),
            Col("Part du PageRank", "pct1", min_w=11, help="Part du PageRank interne total reçue par le segment.")]
    if d.has_value:
        cols += [Col("Part de la valeur", "pct1", min_w=11, help="Part de la valeur business totale portée par le segment."),
                 Col("Écart (points)", "points", min_w=11, help="Part de la valeur moins part du PageRank, en points : "
                                                                "positif, le segment rapporte plus qu'il ne reçoit (sous-alimenté).")]
    cols += [Col("Score moyen", "score", min_w=9, help="Moyenne des scores 0-100 des pages du segment."),
             Col("Score médian", "score", min_w=9, help="Score de la page du milieu du segment."),
             Col("Fuite sortante vers d'autres silos", "pct1", min_w=13,
                 help="Part du jus sortant des pages du segment qui part vers un autre segment (liens transversaux)."),
             Col("Pages en famine", "count", min_w=9, help="Pages du segment en famine."),
             Col("Gouffres", "count", min_w=9, help="Pages du segment en gouffre."),
             Col("Culs-de-sac", "count", min_w=9, help="Pages du segment sans lien sortant.")]
    rows = []
    # Parts en pleine précision depuis graph.json quand il existe : segments.csv est arrondi à quatre décimales,
    # ce qui décalait l'arrondi au dixième par rapport à la Synthèse, findings.md et le rapport.
    full = {str(s.get("name")): s for s in (d.graph.get("segments") or []) if isinstance(s, dict)} \
        if isinstance(getattr(d, "graph", None), dict) else {}
    if d.segments_csv:
        for r in d.segments_csv[1]:
            g_ = full.get(str(r.get("segment")), {})
            pr = _f(g_.get("pr_share")) if g_.get("pr_share") is not None else _f(r.get("pr_share"))
            vs = _f(g_.get("value_share")) if g_.get("value_share") is not None else _f(r.get("value_share"))
            row = [r.get("segment"), _i(r.get("pages")), pr]
            if d.has_value:
                gap = _f(r.get("gap_value_minus_pr"))
                if gap is None and vs is not None and pr is not None:
                    gap = vs - pr
                # en points de pourcentage, arrondi au dixième : jamais « -0,0 pt »
                row += [vs, round(100 * gap, 1) + 0.0 if gap is not None else None]
            row += [_f(r.get("score_mean")), _f(r.get("score_median")), _f(r.get("leak_out_share")), _i(r.get("starving")),
                    _i(r.get("overfed")), _i(r.get("dangling"))]
            rows.append(row)
    bars = [(2, 0, None)] + ([(3, 0, None)] if d.has_value else [])
    b.table("Segments", cols, rows, freeze="B2", bars=bars,
            empty="Donnée absente : segments.csv introuvable dans le dossier de sortie.")


def _flux(b: _Book, d: _Data) -> None:
    if not d.flows_csv:
        b.table("Flux entre silos", [Col("Depuis \\ vers", width=40)], [], doc=False,
                empty="Donnée absente : flows_segments.csv introuvable dans le dossier de sortie.")
        return
    flows: dict[tuple[str, str], float] = {}
    for r in d.flows_csv[1]:
        m = _f(r.get("pagerank_mass"))
        if m is None or m <= 0:
            continue
        key = (r.get("from_segment") or "", r.get("to_segment") or "")
        flows[key] = flows.get(key, 0.0) + m
    names = [r.get("segment") or "" for r in d.segments_csv[1]] if d.segments_csv else []
    for a, z in flows:
        for s in (a, z):
            if s not in names:
                names.append(s)
    if len(names) > MATRIX_MAX:
        shown, others = names[: MATRIX_MAX - 1], set(names[MATRIX_MAX - 1:])
        group = {s: ("autres" if s in others else s) for s in names}
        labels = shown + ["autres"]
    else:
        group = {s: s for s in names}
        labels = names
    mat = {(a, z): 0.0 for a in labels for z in labels}
    for (a, z), m in flows.items():
        mat[(group[a], group[z])] += m
    grand = sum(mat.values()) or 1.0
    rows = []
    for a in labels:
        tot = sum(mat[(a, z)] for z in labels)
        rows.append([a] + [(mat[(a, z)] / tot if tot > 0 and mat[(a, z)] > 0 else None) for z in labels] + [tot / grand])
    cols = ([Col("Depuis \\ vers", "bold", min_w=14, max_w=26, help="Silo d'où part le jus (une ligne par silo).")]
            + [Col(z, "pct1", min_w=9, max_w=14) for z in labels]
            + [Col("Poids du silo dans le flux", "pct1", min_w=12, help="Part du flux total de PageRank qui part de ce silo : "
                                                                       "une ligne de faible poids compte peu.")])
    notes = ["Lecture : chaque ligne totalise 100 %. De tout le jus qui sort des pages du silo en ligne, la part qui "
             "arrive dans le silo en colonne ; la diagonale est la part qui reste dans le silo. Case vide : aucun lien."]
    if len(names) > MATRIX_MAX:
        notes.append(f"Matrice limitée aux {MATRIX_MAX - 1} silos de plus grand PageRank ; les {len(names) - MATRIX_MAX + 1} "
                     "autres sont regroupés dans « autres ». Détail complet dans flows_segments.csv.")
    ws = b.table("Flux entre silos", cols, rows, freeze="B2", notes=notes, doc=False)
    if rows:
        first, last = get_column_letter(2), get_column_letter(1 + len(labels))
        ws.conditional_formatting.add(
            f"{first}2:{last}{1 + len(rows)}",
            ColorScaleRule(start_type="num", start_value=0, start_color=BLUE_LIGHT, end_type="max", end_color=BLUE))
    b.doc += [("Flux entre silos", "Depuis \\ vers", "Silo d'où part le jus (une ligne par silo)."),
              ("Flux entre silos", "Colonnes de silos", "Part du jus sortant du silo en ligne qui arrive dans le silo en "
                                                        "colonne ; chaque ligne totalise 100 %. Plus la case est foncée, plus "
                                                        "le flux est fort."),
              ("Flux entre silos", "Poids du silo dans le flux", "Part du flux total de PageRank qui part de ce silo.")]


def _modele(b: _Book, d: _Data) -> None:
    m = d.meta
    cols = [Col("Rubrique", "wrap", width=24, help="Famille du paramètre."),
            Col("Paramètre", "wrap", width=42, help="Nom du réglage ou de la statistique."),
            Col("Valeur", "wrap", width=24, left=True, help="Valeur retenue pour cette analyse."),
            Col("Explication", "wrap", width=80, help="Ce que le réglage change, en langage courant.")]
    rows: list[list] = []
    if not m:
        b.table("Modèle", cols, [], empty="Donnée absente : graph.json introuvable, paramètres du modèle inconnus.")
        return

    def add(section, label, value, help_=None, style=None):
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value) or "aucune"
        if value is None:
            value = "n/a"
        rows.append([section, label, (value, style) if style else value, help_])

    calc = "Calcul du PageRank"
    add(calc, "Facteur d'amortissement (alpha)", _f(m.get("alpha")),
        "Probabilité qu'un visiteur suive un lien plutôt que de repartir d'une page au hasard (0,85 : valeur de l'article "
        "fondateur du PageRank).", "dec2")
    add(calc, "Culs-de-sac", DANGLING_LABEL.get(m.get("dangling"), m.get("dangling")),
        "Où va le jus d'une page sans lien sortant : réparti sur tout le site, ou renvoyé vers l'accueil.")
    add(calc, "Profil de poids des liens", PROFILE_LABEL.get(m.get("weights_profile"), m.get("weights_profile")),
        "Surfeur raisonnable : un lien du texte pèse plus qu'un lien de menu ou de pied de page.")
    add(calc, "Itérations", _i(m.get("iterations")), "Nombre de passes du calcul jusqu'à stabilité.", "count")
    if m.get("converged") is not None:
        add(calc, "Calcul convergé", "oui" if m.get("converged") else "non",
            "« non » : relancer avec plus d'itérations (--max-iter) avant de lire les résultats.")
    add(calc, "Pondération sémantique", m.get("weighting") if m.get("weighting") else "aucune",
        "Pénalité appliquée aux liens entre pages de sujets éloignés, si des vecteurs de contenu ont été fournis.")
    for pos, w in (m.get("weights") or {}).items():
        add("Poids des liens par position", POSITION_LABEL.get(pos, pos), _f(w),
            "Poids relatif d'un lien placé à cet endroit de la page (1 = lien du texte).", "dec2")
    th = m.get("thresholds") or {}
    seuil = "Seuils de lecture"
    add(seuil, "Percentile de famine", _f(m.get("starving_percentile")),
        "Une page money ou stratégique est en famine si son score est sous ce percentile des scores du site.", "score")
    add(seuil, "Score seuil de famine", _f(th.get("starving_score")), "Score correspondant à ce percentile.", "score")
    add(seuil, "Percentile de gouffre", _f(m.get("overfed_percentile")),
        "Une page utilitaire est un gouffre si son score dépasse ce percentile.", "score")
    add(seuil, "Score seuil de gouffre", _f(th.get("overfed_score")), "Score correspondant à ce percentile.", "score")
    add(seuil, "Score médian du site", _f(th.get("median_score")), "La moitié des pages est au-dessus.", "score")
    if th.get("money_share") is not None:
        add(seuil, "Part de la valeur des pages money", _f(th.get("money_share")),
            "Les pages qui cumulent cette part de la valeur business, de la plus forte à la plus faible, sont « money ».",
            "pct1")
    biz = "Données business"
    add(biz, "Colonne de valeur", m.get("value_col") or "aucune", "Métrique qui définit la valeur des pages (famine, alignement).")
    add(biz, "Colonne de demande", m.get("demand_col") or "aucune", "Métrique lue comme la demande (impressions).")
    add(biz, "Colonnes disponibles", m.get("business_cols") or "aucune", "Colonnes lues dans le fichier business.")
    stats = m.get("stats") or {}
    for key, value in stats.items():
        if key == "boilerplate_target_list":
            continue
        label, help_ = STAT_LABEL.get(key, (key, "Statistique de nettoyage, reprise telle quelle."))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            add("Nettoyage du graphe", label, value, help_, "count")
        elif isinstance(value, (str, bool)) or value is None:
            add("Nettoyage du graphe", label, value, help_)
    for pos, n in (m.get("position_counts") or {}).items():
        add("Liens par position", POSITION_LABEL.get(pos, pos), _i(n), "Nombre de liens détectés à cet endroit des pages.",
            "count")
    exp = "Export"
    add(exp, "Pages exportées dans le rapport HTML", _i(m.get("exported_nodes")),
        "Le rapport HTML échantillonne au-delà de son plafond ; ce classeur et pages.csv portent toutes les pages.", "count")
    add(exp, "Rapport HTML tronqué", "oui" if m.get("truncated") else "non", None)
    cible = "Cibles requalifiées en gabarit"
    targets = stats.get("boilerplate_target_list") or []
    if not targets:
        rows.append([cible, "Aucune cible requalifiée en gabarit", None,
                     "Aucune page n'est liée depuis le contenu d'une part assez grande des pages pour être traitée comme "
                     "un bloc répété."])
    for t in targets:
        if isinstance(t, (list, tuple)) and t:
            url, n, share = (list(t) + [None, None])[:3]
        else:
            url, n, share = t, None, None
        reading = "Liée depuis le contenu d'une grande part des pages : ces liens sont pesés comme du gabarit."
        if _f(share) is not None:
            reading = (f"Liée depuis le contenu de {_fr_pct(_f(share))} des pages émettrices : ces liens sont pesés "
                       "comme du gabarit.")
        rows.append([cible, Link(str(url)), (_i(n), "count") if _i(n) is not None else None, reading])
    b.table("Modèle", cols, rows, freeze="A2")


def _lisez_moi(b: _Book, d: _Data) -> None:
    m = d.meta
    sp = _f(m.get("starving_percentile"))
    op = _f(m.get("overfed_percentile"))
    ms = _f((m.get("thresholds") or {}).get("money_share"))
    sp_t = f"{sp:.0f}e percentile" if sp is not None else "seuil de famine"
    op_t = f"{op:.0f}e percentile" if op is not None else "seuil haut"
    ms_t = _fr_pct(ms, 0) if ms is not None else "l'essentiel"
    cl = d.class_labels
    defs = [
        ("Score (0-100) relatif", "Position de la page dans ce rapport, sur une échelle logarithmique : 100 pour la page qui "
         "reçoit le plus de PageRank interne, 0 pour celle qui en reçoit le moins. Le score bouge dès qu'une page est "
         "ajoutée ou retirée : il ne se compare ni d'une analyse à l'autre, ni d'un site à l'autre. Pour comparer, "
         "utiliser la part d'équité."),
        ("Part d'équité", "Part du PageRank interne total que reçoit la page ; la somme sur toutes les pages vaut 100 %. "
         "Elle se compare d'une analyse à l'autre du même site, avant et après une modification du maillage."),
        ("PageRank interne", "Probabilité qu'un visiteur qui clique de lien en lien se trouve sur la page, en suivant plus "
         "volontiers les liens du texte que ceux des menus. Calculé sur les liens internes crawlés seulement."),
        ("Famine", f"Page qui compte (money : elle fait partie des pages qui cumulent {ms_t} de la valeur business ; ou "
         f"stratégique : cible choisie) mais dont le score est sous le {sp_t} des scores du site. Elle rapporte et reçoit "
         "peu de jus interne : priorité du maillage."),
        ("Gouffre", f"Page utilitaire (mentions légales, CGV, panier, compte, recrutement, livraison) dont le score dépasse le "
         f"{op_t} : elle capte du jus que les pages qui rapportent ne reçoivent pas."),
        ("Cul-de-sac", "Page sans aucun lien sortant suivi : le jus qui y entre ne repart vers aucune autre page du site."),
        ("Orpheline", "Page qu'aucun lien interne crawlé ne cible. Le nombre est un plancher : un crawl ne découvre que les "
         "pages liées ; l'inventaire complet (sitemap, logs, Search Console) donne les vraies orphelines."),
        ("Inatteignable", "Aucun chemin de liens ne mène à la page depuis l'accueil."),
        ("Non crawlée", "URL connue mais non récupérée (budget de pages atteint, robots.txt) : le jus qui part vers elle est "
         "compté comme une fuite."),
        ("Gabarit", "Liens répétés sur toutes les pages : en-tête, menu, pied de page, barre latérale, blocs répétés. Un "
         "changement de gabarit touche toutes les pages d'un coup."),
        ("Classes", "; ".join(f"{cl.get(k, k)}" for k in ("money", "acquisition", "confiance", "legal_support", "neutre",
                                                          "accueil"))
         + ". Money : les pages qui font la valeur ; acquisition : les pages qui attirent (articles, guides, piliers) ; "
         "confiance : à propos, presse, FAQ ; légal / support : mentions, CGV, panier, compte."),
        ("Profil de liens", "Croisement du score (jus reçu) et du score de diffusion (jus redistribué), coupés à la médiane. "
         "Puits : reçoit et redistribue peu, normal pour une page de vente. Hub : reçoit et redistribue, une catégorie mère. "
         "Passoire : reçoit peu et redistribue beaucoup. Peu reliée : reçoit peu et redistribue peu, à instruire avec les clics avant "
         "toute décision. Une position dans le graphe, jamais une décision."),
        ("Score d'alignement", "100 = le PageRank se répartit entre les classes de pages exactement comme la valeur business "
         "(ou une cible par défaut sans données business). Barème : " + (d.alignment.get("scale") or m.get("alignment_scale")
                                                                          or "n/a") + "."),
        ("Victoire rapide", "Un des cinq tickets au meilleur rapport gain / effort, toutes priorités confondues."),
        ("Gain estimé", "Sortie du modèle de PageRank interne : un ordre de priorité entre chantiers, pas une prédiction de "
         "positions ni de trafic."),
        ("Fichiers CSV", "pages.csv, segments.csv, flows_segments.csv et actions.csv restent au format machine (virgule, "
         "point décimal) pour les outils ; ce classeur en est la version lisible. Les valeurs brutes de PageRank et de "
         "CheiRank sont dans pages.csv."),
    ]
    rows = [[t, c, h] for t, c, h in b.doc] + [["Définitions", term, text] for term, text in defs]
    cols = [Col("Onglet", "bold", width=18), Col("Colonne ou terme", "bold_wrap", width=30),
            Col("Ce qu'elle veut dire", "wrap", width=100)]
    b.table("Lisez-moi", cols, rows, freeze="A2", doc=False)


# ── Point d'entrée ───────────────────────────────────────────────────────────

def write_workbook(path: str, out_dir: str) -> None:
    """Écrit `path` (report.xlsx) à partir des fichiers du dossier de sortie `out_dir` : pages.csv, segments.csv,
    flows_segments.csv, actions.csv s'il existe et graph.json (meta, alignment, zones, scenario s'il existe).
    Un fichier ou une colonne absente donne une ligne « Donnée absente : ... », jamais une erreur."""
    d = _Data(out_dir)
    b = _Book(path, out_dir)
    _synthese(b, d)
    _plan(b, d)
    _pages_sheets(b, d)
    _segments(b, d)
    _flux(b, d)
    _modele(b, d)
    _lisez_moi(b, d)
    if d.meta.get("site"):
        b.wb.properties.title = _clean_text(f"Maillage interne : {d.meta['site']}")[:200]
    b.wb.properties.creator = "prgraph"
    b.save()
