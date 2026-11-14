# -*- coding: utf-8 -*-
"""Site synthétique reproduisant des pathologies courantes : mégamenu, footer géant, pagination
qui enterre des produits rentables, PDF cul-de-sac, anciennes catégories en 301, lien mort,
nofollow interne, cannibalisation, liens parasites entre silos. Produit aussi transitions.csv (clics
observés concentrés sur quelques liens de contenu, zéro sur le footer) et embeddings.csv (un vecteur
par page, construit par catégorie avec du bruit) pour les options --transitions et --embeddings."""
from __future__ import annotations

import csv
import io
import os
import random

BASE = "https://demo-outdoor.example"
CATS = ["randonnee", "trail", "camping", "velo", "escalade", "ski", "natation", "running",
        "fitness", "peche", "kayak", "voyage"]
SUBS = ["chaussures", "vestes", "sacs", "accessoires", "equipement"]
UTILITIES = ["mentions-legales", "cgv", "confidentialite", "cookies", "contact", "compte", "panier",
             "livraison", "retours", "faq", "a-propos", "plan-du-site"]


def generate(out_dir: str, seed: int = 42, n_products: int = 1200) -> dict:
    rnd = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    u = lambda p: BASE + p  # noqa: E731

    home = u("/")
    cat_pages = {c: u(f"/c/{c}/") for c in CATS}
    sub_pages = {(c, s): u(f"/c/{c}/{s}/") for c in CATS for s in SUBS}
    sub_list = list(sub_pages)
    per_sub = max(1, n_products // len(sub_list))
    products: dict[str, tuple[str, str, int]] = {}
    sub_products: dict[tuple[str, str], list[str]] = {k: [] for k in sub_list}
    pid = 0
    for (c, s) in sub_list:
        for k in range(per_sub):
            pid += 1
            url = u(f"/p/{s}-{c}-{pid}/")
            products[url] = (c, s, k)
            sub_products[(c, s)].append(url)
    page_size = 8
    listing: dict[tuple[str, str], list[str]] = {}
    for key, plist in sub_products.items():
        n_pages = max(1, -(-len(plist) // page_size))
        pages = [sub_pages[key]] + [sub_pages[key] + f"page/{k}/" for k in range(2, n_pages + 1)]
        listing[key] = pages
    articles = [u(f"/blog/{CATS[i % len(CATS)]}-conseils-{i + 1}/") for i in range(80)]
    cannibal_a = u("/blog/choisir-ses-chaussures-de-trail/")
    cannibal_b = u("/guides/guide-chaussures-trail/")
    articles.append(cannibal_a)
    blog_index = [u("/blog/")] + [u(f"/blog/page/{k}/") for k in range(2, 6)]
    guides = [u(f"/guides/guide-{c}/") for c in CATS[:10]] + [cannibal_b]
    utilities = {name: u(f"/{name}/") for name in UTILITIES}
    pdf = u("/catalogue-2026.pdf")
    newsletter = u("/newsletter/")
    old_cats = {u(f"/categorie/{c}/"): cat_pages[c] for c in CATS[:3]}
    dead = u("/promo-ete-2024/")

    all_pages = ([home] + list(cat_pages.values()) + [p for pages in listing.values() for p in pages]
                 + list(products) + articles + blog_index + guides + list(utilities.values()) + [pdf, newsletter])

    # Mégamenu : accueil, 12 catégories, 40 sous-catégories (les 8 premières catégories), blog, guides, utilitaires
    menu_subs = [sub_pages[(c, s)] for c in CATS[:8] for s in SUBS]
    header_links = [home] + list(cat_pages.values()) + menu_subs + [blog_index[0], guides[0], utilities["contact"],
                                                                      utilities["compte"], utilities["panier"]]
    footer_links = list(utilities.values()) + list(cat_pages.values()) + list(old_cats) + [pdf, dead, newsletter]
    nofollow_targets = {utilities["compte"], utilities["panier"]}

    edges: list[tuple[str, str, str, int, str]] = []
    parasites: list[tuple[str, str]] = []

    def add(src, tgt, pos, anchor="", nofollow=0):
        edges.append((src, tgt, pos, nofollow, anchor))

    def boilerplate(src):
        for t in header_links:
            add(src, t, "header", "menu", 0)
        for t in footer_links:
            add(src, t, "footer", "footer", 1 if t in nofollow_targets else 0)

    def breadcrumb(src, chain):
        for t in chain:
            add(src, t, "breadcrumb", "fil")

    # Accueil
    boilerplate(home)
    _feat_pool = [p for p, (c, s, k) in products.items() if k < 4]
    featured = rnd.sample(_feat_pool, min(6, len(_feat_pool)))
    for t in featured:
        add(home, t, "content", "produit vedette")
    for c in CATS[:4]:
        add(home, cat_pages[c], "content", c)
    for a in articles[:3]:
        add(home, a, "content", "article")
    add(home, guides[0], "content", "guide")

    # Catégories
    for c in CATS:
        src = cat_pages[c]
        boilerplate(src)
        breadcrumb(src, [home])
        for s in SUBS:
            add(src, sub_pages[(c, s)], "content", s)
        for p in sub_products[(c, SUBS[0])][:4]:
            add(src, p, "content", "produit")
        if c == "velo":   # liens parasites entre silos
            for p in rnd.sample(sub_products[("randonnee", "chaussures")], 3):
                add(src, p, "content", "voir aussi")
                parasites.append((src, p))

    # Sous-catégories paginées
    for key, pages in listing.items():
        c, s = key
        plist = sub_products[key]
        for k, src in enumerate(pages):
            boilerplate(src)
            breadcrumb(src, [home, cat_pages[c]])
            for p in plist[k * page_size:(k + 1) * page_size]:
                add(src, p, "content", "produit")
            if k > 0:
                add(src, pages[k - 1], "content", "précédent")
            if k + 1 < len(pages):
                add(src, pages[k + 1], "content", "suivant")

    # Produits
    for p, (c, s, k) in products.items():
        boilerplate(p)
        breadcrumb(p, [home, cat_pages[c], sub_pages[(c, s)]])
        siblings = [q for q in sub_products[(c, s)] if q != p]
        for q in rnd.sample(siblings, min(3, len(siblings))):
            add(p, q, "content", "produit similaire")
        if rnd.random() < 0.02:   # parasite transversal
            other = rnd.choice([q for q, (c2, _, _) in products.items() if c2 != c])
            add(p, other, "content", "vous aimerez aussi")
            parasites.append((p, other))

    # Blog
    for i, a in enumerate(articles):
        boilerplate(a)
        breadcrumb(a, [home, blog_index[0]])
        c = CATS[i % len(CATS)]
        for q in rnd.sample(sub_products[(c, rnd.choice(SUBS))], 2):
            add(a, q, "content", "produit")
        add(a, cat_pages[c], "content", c)
        for b in rnd.sample([x for x in articles if x != a], 2):
            add(a, b, "content", "lire aussi")
        if i < len(CATS[:10]) * 7 and rnd.random() < 0.6:   # les guides sont liés depuis une partie des articles
            add(a, guides[i % 10], "content", "guide complet")
    for k, src in enumerate(blog_index):
        boilerplate(src)
        breadcrumb(src, [home])
        for a in articles[k * 20:(k + 1) * 20]:
            add(src, a, "content", "article")
        if k > 0:
            add(src, blog_index[k - 1], "content", "précédent")
        if k + 1 < len(blog_index):
            add(src, blog_index[k + 1], "content", "suivant")

    # Guides piliers
    for i, gd in enumerate(guides):
        boilerplate(gd)
        breadcrumb(gd, [home])
        c = CATS[i % len(CATS)]
        for a in [x for x in articles if f"/{c}-" in x][:6]:
            add(gd, a, "content", "article")
        for s in SUBS[:3]:
            add(gd, sub_pages[(c, s)], "content", s)
        add(gd, blog_index[0], "content", "blog")
    add(cannibal_a, cannibal_b, "content", "guide complet")

    # Utilitaires
    for name, src in utilities.items():
        boilerplate(src)
        breadcrumb(src, [home])
    add(utilities["contact"], utilities["faq"], "content", "faq")
    add(utilities["faq"], utilities["livraison"], "content", "livraison")
    add(utilities["faq"], utilities["retours"], "content", "retours")
    for t in list(cat_pages.values()) + list(sub_pages.values()):
        add(utilities["plan-du-site"], t, "content", "plan")
    # PDF et newsletter : aucun lien sortant (culs-de-sac)

    # ── Valeur business ──
    heroes = []
    buried = [p for p, (c, s, k) in products.items() if k >= page_size and c in CATS[:8]]
    hidden_cat = [p for p, (c, s, k) in products.items() if c in CATS[8:]]
    visible = [p for p, (c, s, k) in products.items() if k < 4 and c in CATS[:8]]
    heroes += rnd.sample(buried, min(18, len(buried))) + rnd.sample(hidden_cat, min(12, len(hidden_cat))) + rnd.sample(visible, min(10, len(visible)))
    business = {}
    for p in products:
        rev = rnd.lognormvariate(5.0, 1.0) if rnd.random() < 0.4 else 0.0
        business[p] = {"revenue": round(rev, 2), "conversions": round(rev / 120, 1), "clicks": int(rev / 3 + rnd.randint(0, 40))}
    for p in heroes:
        rev = rnd.uniform(8000, 45000)
        business[p] = {"revenue": round(rev, 2), "conversions": round(rev / 110, 1), "clicks": int(rev / 6)}
    money_subs = rnd.sample(sub_list, min(10, len(sub_list)))
    for key in money_subs:
        rev = rnd.uniform(3000, 12000)
        business[sub_pages[key]] = {"revenue": round(rev, 2), "conversions": round(rev / 100, 1), "clicks": int(rev / 2)}
    for a in articles:
        business[a] = {"revenue": 0.0, "conversions": 0.0, "clicks": rnd.randint(50, 3000)}
    for gd in guides:
        business[gd] = {"revenue": round(rnd.uniform(0, 600), 2), "conversions": round(rnd.uniform(0, 5), 1), "clicks": rnd.randint(500, 6000)}
    business[home] = {"revenue": 2500.0, "conversions": 20.0, "clicks": 20000}
    for name, src in utilities.items():
        business[src] = {"revenue": 0.0, "conversions": 0.0, "clicks": rnd.randint(0, 300)}

    # ── Clics observés (transitions.csv) ──
    # Clics concentrés sur deux ou trois liens de contenu par page, un filet sur le menu, zéro sur le
    # footer et le fil d'Ariane. Deux jumeaux en même position sur l'accueil : `hot` très cliqué partout
    # où il est lié, `cold` jamais cliqué nulle part.
    hot, cold = featured[0], featured[1]
    by_source: dict[str, list[tuple[str, str]]] = {}
    for s, t, pos, nf, _ in edges:
        if not nf:
            by_source.setdefault(s, []).append((t, pos))
    transitions: list[tuple[str, str, int]] = []
    for s, lst in by_source.items():
        content = [t for t, pos in lst if pos == "content"]
        header = [t for t, pos in lst if pos == "header"]
        clicks: dict[str, int] = {}
        for k, t in enumerate(content[:3]):
            clicks[t] = clicks.get(t, 0) + max(1, int(240 / (k + 1) ** 1.6 * rnd.uniform(0.6, 1.4)))
        for t in content[3:]:
            if rnd.random() < 0.15:
                clicks[t] = clicks.get(t, 0) + rnd.randint(1, 8)
        for t in header[:3]:
            clicks[t] = clicks.get(t, 0) + rnd.randint(3, 12)
        for t in content:
            if t == hot:
                clicks[t] = clicks.get(t, 0) + 600
        clicks.pop(cold, None)
        for t, n in clicks.items():
            if n > 0:
                transitions.append((s, t, n))

    # ── Vecteurs sémantiques (embeddings.csv) ──
    # Une dimension par catégorie, plus éditorial, utilitaire et générique ; bruit gaussien.
    dims = CATS + ["editorial", "utilitaire", "generique"]
    di = {d: i for i, d in enumerate(dims)}

    def vec(**weights) -> list[float]:
        v = [rnd.gauss(0.0, 0.05) for _ in dims]
        v[di["generique"]] += 0.4        # socle commun : deux pages hors sujet gardent un cosinus faible mais positif
        for k, x in weights.items():
            v[di[k]] += x
        return [round(x, 4) for x in v]

    embeddings: dict[str, list[float]] = {home: vec(generique=1.0, **{c: 0.3 for c in CATS})}
    for c in CATS:
        embeddings[cat_pages[c]] = vec(**{c: 1.0})
        for pages in (listing[(c, s)] for s in SUBS):
            for pg in pages:
                embeddings[pg] = vec(**{c: 1.0})
    for p, (c, s, k) in products.items():
        embeddings[p] = vec(**{c: 1.0})
    for i, a in enumerate(articles):
        c = CATS[i % len(CATS)] if a != cannibal_a else "trail"
        embeddings[a] = vec(editorial=0.7, **{c: 0.7})
    for pg in blog_index:
        embeddings[pg] = vec(editorial=1.0)
    for i, gd in enumerate(guides):
        c = CATS[i % len(CATS)] if gd != cannibal_b else "trail"
        embeddings[gd] = vec(editorial=0.8, **{c: 0.6})
    for src in list(utilities.values()) + [pdf, newsletter]:
        embeddings[src] = vec(utilitaire=1.0)

    # ── Écriture ──
    with io.open(os.path.join(out_dir, "transitions.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "target", "transitions"])
        w.writerows(transitions)
    with io.open(os.path.join(out_dir, "embeddings.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url"] + [f"d_{d}" for d in dims])
        for p in all_pages:
            if p in embeddings:
                w.writerow([p] + embeddings[p])
    with io.open(os.path.join(out_dir, "edges.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "target", "position", "nofollow", "anchor"])
        w.writerows(edges)
    with io.open(os.path.join(out_dir, "urls.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "status", "redirect_target", "title"])
        for p in all_pages:
            w.writerow([p, 200, "", p.replace(BASE, "").strip("/").replace("-", " ").replace("/", " · ") or "Accueil"])
        for old, new in old_cats.items():
            w.writerow([old, 301, new, ""])
        w.writerow([dead, 404, "", ""])
    with io.open(os.path.join(out_dir, "business.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "revenue", "conversions", "clicks"])
        for p, m in business.items():
            w.writerow([p, m["revenue"], m["conversions"], m["clicks"]])
    with io.open(os.path.join(out_dir, "segments.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# nom<TAB>regex, premier match gagnant\n")
        fh.write("accueil\t^https://demo-outdoor\\.example/$\n")
        fh.write("produits\t/p/\n")
        fh.write("categories\t/c/\n")
        fh.write("blog\t/blog/\n")
        fh.write("guides\t/guides/\n")
        fh.write("fichiers\t\\.pdf$\n")
        fh.write("utilitaires\t.\n")
    with io.open(os.path.join(out_dir, "strategic.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "poids"])
        for p in heroes:
            w.writerow([p, 3])
        for key in money_subs:
            w.writerow([sub_pages[key], 2])
    with io.open(os.path.join(out_dir, "cannibalisation.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "group"])
        w.writerow([cannibal_a, "chaussures trail"])
        w.writerow([cannibal_b, "chaussures trail"])
    return {"pages": len(all_pages), "edges": len(edges), "heroes": len(heroes), "out_dir": out_dir,
            "transitions": len(transitions), "embedded": len(embeddings), "twins": (hot, cold), "parasites": parasites}
