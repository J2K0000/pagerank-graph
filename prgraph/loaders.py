# -*- coding: utf-8 -*-
"""Lecture des fichiers d'entrée : arêtes, table d'URL, données business, segments.

Formats acceptés (détection automatique par en-tête) :
- arêtes à positions : source,target[,position][,nofollow][,anchor]
- arêtes Screaming Frog « All Inlinks / All Outlinks » : Type,Source,Destination,...,
  Follow,Link Position,Status Code
- arêtes génériques : source,target[,weight]
- table d'URL : url,status[,redirect_target][,canonical][,segment][,title]
  ou export Screaming Frog internal_html (Address,Status Code,Redirect URL,
  Canonical Link Element 1,Title 1,Indexability)
- business : url + colonnes de valeur (revenue, conversions, clicks, sessions,
  impressions, crawl_hits), export Search Console « Pages » accepté.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Iterator

csv.field_size_limit(10**8)

# ── Poids par position (heuristiques, pas une reconstitution de Google) ───────

WEIGHT_PROFILES = {
    # Surfeur raisonnable, lecture praticien : gabarit très atténué.
    "raisonnable": {"content": 1.0, "breadcrumb": 0.5, "sidebar": 0.25, "header": 0.2,
              "nav": 0.2, "footer": 0.05, "boilerplate": 0.2, "unknown": 0.6},
    # Variante modérée : liens de navigation et de pied de page moins atténués.
    "modere": {"content": 1.0, "breadcrumb": 0.5, "sidebar": 0.25, "header": 0.35,
                "nav": 0.35, "footer": 0.10, "boilerplate": 0.35, "unknown": 0.6},
    # PageRank « naïf » de 1998 : tous les liens se valent.
    "uniform": {"content": 1.0, "breadcrumb": 1.0, "sidebar": 1.0, "header": 1.0,
                "nav": 1.0, "footer": 1.0, "boilerplate": 1.0, "unknown": 1.0},
}

POSITIONS = ["content", "breadcrumb", "sidebar", "header", "nav", "footer", "unknown", "boilerplate"]

POSITION_ALIASES = {
    "content": "content", "main": "content", "body": "content", "article": "content",
    "contenu": "content", "texte": "content", "in-content": "content", "editorial": "content",
    "nav": "nav", "navigation": "nav", "menu": "nav", "megamenu": "nav", "mega-menu": "nav",
    "header": "header", "entete": "header", "en-tête": "header", "en-tete": "header",
    "topbar": "header",
    "footer": "footer", "pied de page": "footer", "pied-de-page": "footer",
    "sidebar": "sidebar", "aside": "sidebar", "side": "sidebar", "widget": "sidebar",
    "colonne": "sidebar",
    "breadcrumb": "breadcrumb", "breadcrumbs": "breadcrumb", "fil d'ariane": "breadcrumb",
    "fil-d-ariane": "breadcrumb", "ariane": "breadcrumb",
    "unknown": "unknown", "": "unknown", "multiple": "unknown", "autre": "unknown",
    "boilerplate": "boilerplate", "gabarit": "boilerplate", "template": "boilerplate",
}


# Anciens noms des profils, toujours acceptés.
PROFILE_ALIASES = {"brief": "raisonnable", "library": "modere"}


def profile_name(name: str) -> str:
    return PROFILE_ALIASES.get(name, name)


def parse_weights(profile: str = "raisonnable", override: str | None = None) -> dict[str, float]:
    profile = profile_name(profile)
    if profile not in WEIGHT_PROFILES:
        raise ValueError(f"profil de poids inconnu : {profile} (choix : {', '.join(WEIGHT_PROFILES)})")
    w = dict(WEIGHT_PROFILES[profile])
    if override:
        for part in override.split(","):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = normalize_position(k)
            try:
                w[k] = float(v)
            except ValueError as exc:
                raise ValueError(f"--weights : poids invalide « {v.strip()} » pour {k} (nombre attendu, ex. footer=0.05)") from exc
    return w


def normalize_position(raw: str | None) -> str:
    if raw is None:
        return "unknown"
    key = str(raw).strip().lower()
    if key in POSITION_ALIASES:
        return POSITION_ALIASES[key]
    for alias, canon in POSITION_ALIASES.items():
        if alias and alias in key:
            return canon
    return "unknown"


# ── Lecture CSV générique ────────────────────────────────────────────────────

def _sniff_delimiter(sample: str) -> str:
    first = sample.splitlines()[0] if sample else ""
    counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _skip_preamble(fh) -> None:
    """Saute les lignes de commentaire (`#`, en-tête des exports GA4) et les lignes vides en tête de fichier."""
    pos = fh.tell()
    line = fh.readline()
    while line and (not line.strip() or line.lstrip().startswith("#")):
        pos = fh.tell()
        line = fh.readline()
    fh.seek(pos)


def read_table(path: str) -> Iterator[dict[str, str]]:
    """Itère sur les lignes d'un CSV, clés en minuscules et sans espaces superflus."""
    with io.open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        _skip_preamble(fh)
        start = fh.tell()
        sample = fh.read(65536)
        fh.seek(start)
        delim = _sniff_delimiter(sample)
        reader = csv.reader(fh, delimiter=delim)
        try:
            header = next(reader)
        except StopIteration:
            return
        keys = [h.strip().lower().lstrip("﻿") for h in header]
        for row in reader:
            if not row or all(not c.strip() for c in row):
                continue
            yield {k: (row[i].strip() if i < len(row) else "") for i, k in enumerate(keys)}


def read_header(path: str) -> list[str]:
    with io.open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        _skip_preamble(fh)
        sample = fh.read(65536)
    if not sample:
        return []
    delim = _sniff_delimiter(sample)
    return [h.strip().lower() for h in next(csv.reader(io.StringIO(sample), delimiter=delim))]


def pick(row: dict[str, str], *names: str, default: str = "") -> str:
    for n in names:
        if n in row and row[n] != "":
            return row[n]
    return default


def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "oui", "nofollow", "y", "vrai")


_THOUSANDS_RX = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def comma_is_thousands(values) -> bool:
    """Décide, pour une colonne entière, si la virgule est un séparateur de milliers (format anglais
    « 1,234 ») ou la décimale (format français « 12,5 »). Une cellule seule ne peut pas trancher :
    « 1,234 » vaut 1 234 ou 1,234. La virgule est un séparateur de milliers quand toutes les cellules
    qui en portent ont des groupes de trois chiffres ET qu'au moins une cellule montre deux groupes
    (« 1,234,567 ») ou une décimale à point (« 1,234.5 », ou un « 12.5 » ailleurs dans la colonne)."""
    values = [str(v).strip().replace(" ", "").replace(" ", "").replace(" ", "") for v in values]
    with_comma = [v for v in values if "," in v]
    if not with_comma:
        return False
    if not all(_THOUSANDS_RX.match(v) for v in with_comma):
        return False
    has_dot = any("." in v for v in values)
    two_groups = any(v.count(",") >= 2 for v in with_comma)
    return has_dot or two_groups


def _to_float(v: str, thousands_comma: bool = False) -> float:
    """Nombre lu dans une cellule. `thousands_comma` : convention de la colonne (voir `comma_is_thousands`) ;
    par défaut une virgule sans point est une décimale (exports français)."""
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(" ", "").replace("%", "")
    if not s:
        return 0.0
    if thousands_comma:
        s = s.replace(",", "")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


# ── Arêtes ───────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class RawEdge:
    source: str
    target: str
    position: str
    nofollow: bool
    anchor: str
    weight: float | None = None       # poids explicite (format générique), sinon None
    target_status: int | None = None  # statut de la cible si l'export le fournit (Screaming Frog)


def detect_edge_format(header: list[str]) -> str:
    h = set(header)
    if "destination" in h and "source" in h:
        return "screamingfrog"
    if "source" in h and "target" in h:
        return "positions" if ("position" in h or "zone" in h or "nofollow" in h or "anchor" in h) else "generic"
    if "from" in h and "to" in h:
        return "generic"
    raise ValueError(
        "format d'arêtes non reconnu. En-têtes attendus : source,target[,position,nofollow,anchor] "
        "ou Source,Destination,Link Position (Screaming Frog)."
    )


def read_edges(path: str) -> tuple[Iterator[RawEdge], str]:
    fmt = detect_edge_format(read_header(path))

    def _iter() -> Iterator[RawEdge]:
        for row in read_table(path):
            if fmt == "screamingfrog":
                typ = pick(row, "type").lower()
                if typ and typ not in ("hyperlink", "ahref", "href", "lien"):
                    continue
                follow_raw = pick(row, "follow", default="true").lower()
                nofollow = follow_raw in ("false", "0", "no", "non", "nofollow")
                status = pick(row, "status code", "status")
                yield RawEdge(
                    source=pick(row, "source"),
                    target=pick(row, "destination"),
                    position=normalize_position(pick(row, "link position", "position")),
                    nofollow=nofollow,
                    anchor=pick(row, "anchor", "anchor text", "alt text")[:200],
                    target_status=int(status) if status.isdigit() else None,
                )
            elif fmt == "positions":
                yield RawEdge(
                    source=pick(row, "source"),
                    target=pick(row, "target"),
                    position=normalize_position(pick(row, "position", "zone")),
                    nofollow=_to_bool(pick(row, "nofollow", default="0")),
                    anchor=pick(row, "anchor", "ancre")[:200],
                )
            else:
                w = pick(row, "weight", "poids")
                yield RawEdge(
                    source=pick(row, "source", "from"),
                    target=pick(row, "target", "to"),
                    position="unknown",
                    nofollow=False,
                    anchor="",
                    weight=_to_float(w) if w else None,
                )

    return _iter(), fmt


# ── Table d'URL ───────────────────────────────────────────────────────────────

# Statuts littéraux écrits par les crawlers pour une URL connue mais non récupérée (robots.txt, budget
# de pages épuisé, erreur réseau). Lus comme statut 0 : la page n'est pas un nœud et un lien vers elle
# est une fuite, jamais une page en 200 supposée.
NOT_FETCHED_STATUSES = {"robots", "uncrawled", "blocked", "error", "timeout", "non-crawlee", "non crawlee"}


@dataclass(slots=True)
class UrlInfo:
    url: str
    status: int | None = None
    redirect: str | None = None
    canonical: str | None = None
    segment: str | None = None
    title: str = ""
    indexable: bool | None = None


def read_url_table(path: str) -> Iterator[UrlInfo]:
    header = set(read_header(path))
    sf = "address" in header
    for row in read_table(path):
        url = pick(row, "address") if sf else pick(row, "url", "page", "address")
        if not url:
            continue
        status_raw = pick(row, "status code", "status", "code", "http")
        if status_raw.isdigit():
            status = int(status_raw)
        elif status_raw.strip().lower() in NOT_FETCHED_STATUSES:
            status = 0          # URL connue mais jamais récupérée : ni nœud, ni cible valide (fuite)
        else:
            status = None       # inconnu : supposé 200
        redirect = pick(row, "redirect url", "redirect_target", "redirect", "location") or None
        canonical = pick(row, "canonical link element 1", "canonical", "canonical_url") or None
        idx_raw = pick(row, "indexability", "indexable").lower()
        indexable = None
        if idx_raw:
            indexable = idx_raw in ("indexable", "true", "1", "oui", "yes")
        yield UrlInfo(
            url=url, status=status, redirect=redirect, canonical=canonical,
            segment=pick(row, "segment", "section", "theme", "thème") or None,
            title=pick(row, "title 1", "title", "titre")[:200],
            indexable=indexable,
        )


# ── Données business ──────────────────────────────────────────────────────────

VALUE_COLUMNS = {
    "revenue": ["revenue", "revenu", "ca", "chiffre_affaires", "chiffre d'affaires", "marge",
                "margin", "valeur", "value", "montant"],
    "conversions": ["conversions", "conversion", "leads", "transactions", "ventes", "sales",
                    "key events", "événements clés", "evenements cles", "key_events", "événements_clés"],
    "clicks": ["clicks", "clics", "clic"],
    "sessions": ["sessions", "visits", "visites", "users", "utilisateurs", "vues", "views",
                 "utilisateurs actifs", "active users", "utilisateurs_actifs", "active_users"],
    "impressions": ["impressions"],
    "crawl_hits": ["crawl_hits", "googlebot", "hits", "crawls", "bot_hits", "passages"],
}
VALUE_COLUMNS["revenue"] += ["revenu total", "total revenue", "revenu_total", "total_revenue",
                             "purchase revenue", "revenu d'achat"]
VALUE_PRIORITY = ["revenue", "conversions", "clicks", "sessions", "impressions"]
COUNT_METRICS = {"clicks", "impressions", "sessions", "conversions", "crawl_hits"}   # entiers : « 1,234 » = mille
DEMAND_COLUMN = "impressions"   # lecture « demande » quand la valeur retenue est les clics

URL_COLUMNS = ["url", "page", "top pages", "pages les plus populaires", "address", "landing page",
               "page d'accueil", "page de destination", "landing_page", "landing page + query string",
               "page de destination + chaîne de requête", "chemin de la page et classe d'écran",
               "page path and screen class", "chemin de la page", "page path", "page location",
               "emplacement de la page"]


def read_business(path: str, base_url: str | None = None) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Retourne {url: {metrique: valeur}} et la liste des métriques présentes.

    `base_url` : origine à préfixer aux chemins relatifs (`/produits/a/`), par exemple pour un export
    GA4 « Pages et écrans ». Sans elle, les chemins restent tels quels et ne seront pas joints au graphe.
    Avec elle, les lignes `(not set)` et les totaux sans chemin sont ignorés."""
    header = read_header(path)
    colmap: dict[str, str] = {}
    for canon, aliases in VALUE_COLUMNS.items():
        for h in header:
            if h in aliases or h.replace(" ", "_") in aliases:
                colmap[canon] = h
                break
    origin = None
    if base_url:
        from urllib.parse import urlsplit
        p = urlsplit(base_url.strip())
        if not p.scheme or not p.netloc:
            raise ValueError(f"--business-base-url doit être une URL absolue, reçu : {base_url!r}")
        origin = f"{p.scheme}://{p.netloc}"
    data: dict[str, dict[str, float]] = {}
    rows = list(read_table(path))
    # Convention numérique décidée par colonne, jamais par cellule (« 1,234 » est ambigu seul) : un export
    # vient d'une seule locale, donc si une colonne prouve que la virgule sépare les milliers (« 12,345,678 »),
    # toutes les colonnes suivent ; et pour une métrique de comptage (clics, impressions, sessions...),
    # « 1,234 » sans décimale à un ou deux chiffres ailleurs dans la colonne est un millier, pas 1,234 clic.
    thousands = {canon: comma_is_thousands(r.get(col, "") for r in rows) for canon, col in colmap.items()}
    if any(thousands.values()):
        thousands = {canon: True for canon in thousands}
    else:
        for canon, col in colmap.items():
            if canon in COUNT_METRICS:
                vals = [str(r.get(col, "")).strip() for r in rows]
                with_comma = [v for v in vals if "," in v]
                if with_comma and all(_THOUSANDS_RX.match(v.replace(" ", "")) for v in with_comma):
                    thousands[canon] = True
    for row in rows:
        url = pick(row, *URL_COLUMNS)
        if not url:
            continue
        if origin:
            if url.lower().startswith(("http://", "https://")):
                pass
            elif url.startswith("/"):
                url = origin + url
            else:
                continue       # (not set), (other), ligne de total
        metrics = {canon: _to_float(row.get(col, ""), thousands[canon]) for canon, col in colmap.items()}
        if url in data:
            for k, v in metrics.items():
                data[url][k] = data[url].get(k, 0.0) + v
        else:
            data[url] = metrics
    return data, list(colmap)


_PERIOD_RX = re.compile(r"^\s*#\s*p[ée]riode\s*:\s*(.+?)\s*$", re.I)


def read_business_period(path: str) -> str | None:
    """Période des données business, lue dans une ligne de commentaire de tête `# période : ...` (écrite par
    `fetch-gsc` et `fetch-ga4`), ou None."""
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
            for _ in range(20):
                line = fh.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                if not line.lstrip().startswith("#"):
                    break
                m = _PERIOD_RX.match(line)
                if m:
                    return m.group(1)[:80]
    except OSError:
        return None
    return None


def choose_value_column(available: list[str], forced: str | None = None) -> str | None:
    if forced:
        if forced not in available:
            raise ValueError(f"colonne de valeur « {forced} » absente ; disponibles : {available}")
        return forced
    for c in VALUE_PRIORITY:
        if c in available:
            return c
    return None


# ── Segments ──────────────────────────────────────────────────────────────────

def read_segment_rules(path: str) -> tuple[list[tuple[str, re.Pattern]], dict[str, str]]:
    """Règles `nom<TAB>regex` (premier match gagnant) ou CSV url,segment (mapping exact)."""
    header = read_header(path)
    if "url" in header and any(c in header for c in ("segment", "section", "theme", "thème", "label", "classe", "class")):
        mapping = {}
        for row in read_table(path):
            u, s = pick(row, "url"), pick(row, "segment", "section", "theme", "thème", "label", "classe", "class")
            if u and s:
                mapping[u] = s
        return [], mapping
    rules = []
    ignored = []
    with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
        for n, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\t+|\s{2,}", line, maxsplit=1)
            if len(parts) != 2:
                ignored.append(n)
                continue
            try:
                rules.append((parts[0].strip(), re.compile(parts[1].strip(), re.I)))
            except re.error as exc:
                raise ValueError(f"regex de segment invalide « {parts[1]} » : {exc}") from exc
    if ignored:
        import sys
        print(f"avertissement : {path} : {len(ignored)} ligne(s) ignorée(s) (ligne(s) {', '.join(map(str, ignored[:10]))}) : "
              "format attendu « nom<TAB>regex » (tabulation ou au moins deux espaces entre le nom et la regex)",
              file=sys.stderr)
    if not rules and not ignored:
        import sys
        print(f"avertissement : {path} : aucune règle lue", file=sys.stderr)
    return rules, {}


def read_url_weight_list(path: str, default: float = 1.0) -> dict[str, float]:
    """CSV url[,weight|poids|priorite|sessions|clics] → {url: poids}. Sert pour --strategic et --restart."""
    out: dict[str, float] = {}
    for row in read_table(path):
        u = pick(row, "url", "page", "address")
        if not u:
            continue
        w = pick(row, "weight", "poids", "priorite", "priorité", "priority", "sessions", "clics",
                 "clicks", "backlinks", "valeur", "value")
        out[u] = _to_float(w) if w else default
    return out


def read_groups(path: str) -> dict[str, str]:
    """CSV url,group (ou url,query) pour l'arbitrage des cannibalisations."""
    out = {}
    for row in read_table(path):
        u = pick(row, "url", "page")
        g = pick(row, "group", "groupe", "query", "requete", "requête", "intention", "keyword", "mot-cle")
        if u and g:
            out[u] = g
    return out
