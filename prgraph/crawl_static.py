# -*- coding: utf-8 -*-
"""Dossier HTML statique (dist Astro, export Next, site généré) → arêtes avec position DOM.

Détection de la position par remontée des ancêtres : <header>/<nav> → header ou nav,
<footer> → footer, <aside> → sidebar, fil d'Ariane (classe, aria-label, BreadcrumbList) →
breadcrumb, <main>/<article> → content, sinon mots-clés de classes et d'identifiants.

Inventaire : chaque fichier `.html` est connu sous ses deux formes, répertoire (`/about/`) et
fichier (`/about.html`) ; la forme secondaire est écrite dans urls.csv avec un `canonical` vers la
forme principale (répertoire par défaut, fichier avec `trailing_slash=False`). `index.html` n'est
retiré qu'en dernier segment (`subindex.html` reste `/subindex/`). Les fichiers non HTML présents
dans le dossier (PDF, documents) sont écrits en 200 sans lien sortant, comme en crawl live. Les
cibles sans fichier correspondant sont écrites en 404 dans urls.csv.
"""
from __future__ import annotations

import csv
import io
import os
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

BREADCRUMB_RX = re.compile(r"breadcrumb|fil-?d-?ariane|ariane", re.I)
CLASS_HINTS = [
    ("footer", re.compile(r"footer|colophon|site-info|subfooter|legal", re.I)),
    ("nav", re.compile(r"\bnav|menu|navbar|navigation|topbar|mega", re.I)),
    ("header", re.compile(r"header|masthead|site-head", re.I)),
    ("sidebar", re.compile(r"sidebar|aside|widget|complementary|secondary", re.I)),
    ("content", re.compile(r"rich-?text|content|entry|post|article|main|prose|body", re.I)),
]
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif", ".css", ".js", ".ico", ".woff", ".woff2",
            ".mp4", ".mp3", ".zip", ".json", ".xml", ".txt")
HTML_EXT = (".html", ".htm")
# Valeurs de l'attribut rel qui retirent le vote du lien (Google : nofollow, sponsored, ugc).
NOFOLLOW_REL = frozenset({"nofollow", "sponsored", "ugc"})


def is_nofollow(rel_values) -> bool:
    """Vrai si `rel` contient nofollow, sponsored ou ugc. Accepte une liste (BeautifulSoup), une chaîne
    séparée par des espaces, ou None. Fonction commune aux deux crawlers."""
    if not rel_values:
        return False
    if isinstance(rel_values, str):
        rel_values = rel_values.split()
    return any(str(v).strip().lower() in NOFOLLOW_REL for v in rel_values)


# Pied de page construit sans balise <footer> (Webflow : <article class="footer">, <div class="footer_wrapper">).
# Un jeton de classe ou un id qui commence par « footer » prime sur <main>/<article>/<nav> ; « card-footer »
# ou « post-footer » ne correspondent pas, le jeton doit commencer par le mot.
FOOTER_TOKEN_RX = re.compile(r"^(site-?|page-?|main-?|global-?)?footer([-_].*)?$", re.I)


def _footer_container(anc) -> bool:
    tokens = list(anc.get("class", []) or []) + [anc.get("id", "") or ""]
    return any(FOOTER_TOKEN_RX.match(t) for t in tokens if t)


# Bloc de contenus associés hors du texte (« Other articles », « Related posts », liste CMS sous l'article) :
# même poids qu'une barre latérale. Sans cette règle, « other-articles-wrapper » tombait sur l'indice
# de classe « article » et comptait comme un lien dans le texte (poids 1).
RELATED_TOKEN_RX = re.compile(r"(^|[-_])(related|other-articles|other-posts|similar|recommended|read-next|more-articles|more-posts)([-_]|$)", re.I)


def _related_container(anc) -> bool:
    tokens = list(anc.get("class", []) or []) + [anc.get("id", "") or ""]
    return any(RELATED_TOKEN_RX.search(t) for t in tokens if t)


# Conteneurs de la page entière : leurs classes décrivent la mise en page (« footer-sticky », « has-sidebar »),
# pas la zone du lien. Les lire ferait basculer tous les liens de la page dans une même zone.
PAGE_CONTAINERS = {"html", "body", "[document]"}


def detect_position(a) -> str:
    """Remonte tous les ancêtres puis tranche par priorité :
    fil d'Ariane > footer > header (nav dans un header) > nav > sidebar > content > mots-clés de classes."""
    seen_footer = seen_header = seen_nav = seen_aside = seen_main = False
    hints = []
    for anc in a.parents:
        name = getattr(anc, "name", None)
        if name is None or name.lower() in PAGE_CONTAINERS:
            continue
        name = name.lower()
        role = (anc.get("role") or "").lower()
        attrs = " ".join([" ".join(anc.get("class", [])), anc.get("id", "") or "", anc.get("aria-label", "") or "",
                          anc.get("itemtype", "") or ""])
        if BREADCRUMB_RX.search(attrs) or "BreadcrumbList" in attrs:
            return "breadcrumb"
        if name == "footer" or role == "contentinfo" or _footer_container(anc):
            seen_footer = True
        elif name == "header" or role == "banner":
            seen_header = True
        elif name == "nav" or role == "navigation":
            seen_nav = True
        elif name == "aside" or role == "complementary" or _related_container(anc):
            seen_aside = True
        elif name in ("main", "article") or role == "main":
            seen_main = True
        hints.append(attrs)
    if seen_footer:
        return "footer"
    if seen_header:
        return "header"
    if seen_nav:
        return "nav"
    if seen_aside:
        return "sidebar"
    if seen_main:
        return "content"
    for attrs in hints:
        for pos, rx in CLASS_HINTS:
            if rx.search(attrs):
                return pos
    return "unknown"


def _rel_path(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/").lstrip("/")


def _join(base_url: str, rel: str) -> str:
    return base_url.rstrip("/") + "/" + rel.lstrip("/")


def html_file_urls(root: str, path: str, base_url: str, trailing_slash: bool = True) -> tuple[str, str | None]:
    """Renvoie (forme principale, forme secondaire) d'un fichier HTML.

    `dir/index.html` → (`/dir/`, `/dir/index.html`) ; `about.html` → (`/about/`, `/about.html`), ou
    l'inverse avec `trailing_slash=False`. `index.html` n'est retiré qu'en dernier segment :
    `subindex.html` donne `/subindex/`. `404.html` garde sa seule forme fichier."""
    rel = _rel_path(root, path)
    head, _, last = rel.rpartition("/")
    prefix = head + "/" if head else ""
    low = last.lower()
    if low in ("index.html", "index.htm"):
        return _join(base_url, prefix), _join(base_url, rel)
    stem = last.rsplit(".", 1)[0]
    if low == "404.html" or not stem:
        return _join(base_url, rel), None
    dir_form = _join(base_url, prefix + stem + "/")
    file_form = _join(base_url, rel)
    return (dir_form, file_form) if trailing_slash else (file_form, dir_form)


def file_to_url(root: str, path: str, base_url: str, trailing_slash: bool = True) -> str:
    """Forme principale de l'URL d'un fichier HTML (compatibilité : signature historique conservée)."""
    return html_file_urls(root, path, base_url, trailing_slash)[0]


def crawl(root: str, base_url: str, out_dir: str, trailing_slash: bool = True) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    pages: dict[str, str] = {}      # forme principale → chemin du fichier
    aliases: dict[str, str] = {}    # forme secondaire → forme principale (canonical)
    assets: list[str] = []          # fichiers non HTML liables (PDF, documents) : 200 sans lien sortant
    for dirpath, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            low = fn.lower()
            if low.endswith(HTML_EXT):
                if low in ("404.html", "500.html"):
                    continue
                main, alt = html_file_urls(root, p, base_url, trailing_slash)
                pages[main] = p
                if alt:
                    aliases[alt] = main
            elif not low.endswith(SKIP_EXT):
                # images, scripts et feuilles de style ne sont jamais des cibles de liens (SKIP_EXT) :
                # on n'inventorie que ce qui peut recevoir un lien (PDF, documents bureautiques, etc.)
                assets.append(_join(base_url, _rel_path(root, p)))
    # une forme secondaire qui est aussi une page principale (fichier `a.html` et dossier `a/index.html`)
    # reste une page : la page prime sur l'alias
    aliases = {k: v for k, v in aliases.items() if k not in pages}
    base_host = urlsplit(base_url).netloc.lower()
    edges = []
    titles = {}
    targets_seen = set()
    for url, path in pages.items():
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            soup = BeautifulSoup(fh.read(), "lxml")
        t = soup.find("title")
        titles[url] = t.get_text(strip=True)[:200] if t else ""
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:", "data:")):
                continue
            abs_url = urljoin(url, href)
            parts = urlsplit(abs_url)
            if parts.scheme not in ("http", "https") or parts.netloc.lower() != base_host:
                continue
            if parts.path.lower().endswith(SKIP_EXT):
                continue
            target = parts._replace(fragment="").geturl()
            if trailing_slash and not parts.path.endswith("/") and "." not in parts.path.rsplit("/", 1)[-1]:
                target = parts._replace(path=parts.path + "/", fragment="").geturl()
            nofollow = is_nofollow(a.get("rel"))
            anchor = a.get_text(" ", strip=True)[:200] or (a.find("img") or {}).get("alt", "")
            edges.append((url, target, detect_position(a), int(nofollow), anchor))
            targets_seen.add(target)
    with io.open(os.path.join(out_dir, "edges.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "target", "position", "nofollow", "anchor"])
        w.writerows(edges)
    known = set(pages) | set(aliases) | set(assets)
    missing = sorted(t for t in targets_seen if t not in known and t.rstrip("/") + "/" not in known)
    with io.open(os.path.join(out_dir, "urls.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "status", "redirect_target", "canonical", "title"])
        for url in pages:
            w.writerow([url, 200, "", "", titles.get(url, "")])
        for alt, main in aliases.items():
            w.writerow([alt, 200, "", main, titles.get(main, "")])
        for url in sorted(assets):
            w.writerow([url, 200, "", "", ""])
        for t in missing:
            w.writerow([t, 404, "", "", ""])
    return {"pages": len(pages), "aliases": len(aliases), "assets": len(assets), "edges": len(edges),
            "missing_targets": len(missing), "out_dir": out_dir}
