# -*- coding: utf-8 -*-
"""Tests des deux crawlers contre un faux site HTTP sur 127.0.0.1 (aucun appel réseau externe)."""
import csv
import io
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from prgraph.crawl_live import ROBOTS_TOKEN, UA, CrawlConfig, LiveCrawler, crawl
from prgraph.crawl_static import crawl as crawl_static
from prgraph.crawl_static import file_to_url, html_file_urls, is_nofollow


# ── faux site ────────────────────────────────────────────────────────────────

class FakeSite:
    """Site HTTP local dans un thread. `mode` : allow | disallow | robots503 | named | ratelimit | rels.

    Journalise chaque requête (horodatage monotone, chemin) dans `hits`."""

    def __init__(self, mode: str = "allow", n_pages: int = 3, retry_after: str = "1"):
        self.mode = mode
        self.n_pages = n_pages
        self.retry_after = retry_after
        self.hits: list[tuple[float, str]] = []
        self.ua_seen: list[str] = []
        self.rate_limited_once = set()
        self.lock = threading.Lock()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with site.lock:
                    site.hits.append((time.monotonic(), self.path))
                    site.ua_seen.append(self.headers.get("User-Agent", ""))
                p = self.path.split("?")[0]
                if p == "/robots.txt":
                    if site.mode == "disallow":
                        return self._send(200, "User-agent: *\nDisallow: /\n", "text/plain")
                    if site.mode == "robots503":
                        return self._send(503, "down", "text/plain")
                    if site.mode == "named":
                        return self._send(200, f"User-agent: {ROBOTS_TOKEN}\nDisallow: /p/2/\n\nUser-agent: *\nAllow: /\n",
                                          "text/plain")
                    return self._send(200, "User-agent: *\nAllow: /\n", "text/plain")
                if p == "/sitemap.xml":
                    locs = "".join(f"<url><loc>http://{self.headers['Host']}/p/{i}/</loc></url>" for i in range(1, site.n_pages + 1))
                    locs += f"<url><loc>http://{self.headers['Host']}/orpheline/</loc></url>"
                    return self._send(200, f'<?xml version="1.0"?><urlset>{locs}</urlset>', "application/xml")
                if p == "/doc.pdf":
                    return self._send(200, "%PDF-1.4 faux <a href='/p/1/'>pas un lien</a>", "application/pdf")
                if p == "/":
                    links = "".join(f'<li><a href="/p/{i}/">page {i}</a></li>' for i in range(1, site.n_pages + 1))
                    extra = ""
                    if site.mode == "rels":
                        extra = ('<a href="/p/1/" rel="sponsored">sponso</a><a href="/p/2/" rel="ugc">ugc</a>'
                                 '<a href="/p/3/" rel="nofollow">nf</a><a href="/p/1/" rel="noopener">suivi</a>')
                    return self._send(200, f"<html><head><title>Accueil</title></head><body><nav><a href='/'>home</a></nav>"
                                           f"<main><ul>{links}</ul>{extra}<a href='/doc.pdf'>pdf</a></main></body></html>")
                if p.startswith("/p/"):
                    if site.mode == "ratelimit":
                        with site.lock:
                            first = p not in site.rate_limited_once
                            site.rate_limited_once.add(p)
                        if first:
                            return self._send(429, "trop vite", "text/plain", {"Retry-After": site.retry_after})
                    return self._send(200, f"<html><head><title>Page {p}</title></head><body><main><a href='/'>home</a>"
                                           f"<a href='/p/1/'>un</a></main></body></html>")
                return self._send(404, "nope", "text/plain")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def page_hits(self):
        return [h for h in self.hits if h[1].startswith("/p/") or h[1] == "/"]


def read_urls(out_dir):
    with io.open(os.path.join(out_dir, "urls.csv"), encoding="utf-8", newline="") as fh:
        return {row["url"]: row for row in csv.DictReader(fh)}


def read_edges(out_dir):
    with io.open(os.path.join(out_dir, "edges.csv"), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def cfg(site, out, **kw):
    base = dict(start_url=site.url, out_dir=str(out), max_pages=50, delay=-1, workers=2, timeout=5)
    base.update(kw)
    return CrawlConfig(**base)


# ── crawl live ───────────────────────────────────────────────────────────────

def test_user_agent_announces_token_and_version():
    from prgraph import __version__
    assert UA.startswith(f"{ROBOTS_TOKEN}/{__version__}")
    assert "0.2;" not in UA


def test_delay_floor_and_explicit_negative():
    assert CrawlConfig("http://x/", "o", delay=0).delay == pytest.approx(0.1)
    assert CrawlConfig("http://x/", "o", delay=0.05).delay == pytest.approx(0.1)
    assert CrawlConfig("http://x/", "o", delay=0.3).delay == pytest.approx(0.3)
    assert CrawlConfig("http://x/", "o", delay=-1).delay == 0.0


def test_site_fully_disallowed_fetches_nothing(tmp_path):
    with FakeSite("disallow") as site:
        stats = crawl(cfg(site, tmp_path))
        assert stats["fetched"] == 0
        assert stats["pages"] == 0
        assert stats["blocked"] >= 1   # l'accueil, plus les URL du sitemap, toutes refusées
        assert stats["robots_unavailable"] is False
        urls = read_urls(tmp_path)
        assert urls[site.url]["status"] == "robots"
        assert {row["status"] for row in urls.values()} == {"robots"}
        assert stats["statuses"] == {"robots": stats["blocked"]}
        assert site.page_hits() == [], "aucune page ne doit avoir été demandée"


def test_robots_503_refuses_crawl(tmp_path):
    with FakeSite("robots503") as site:
        messages = []
        stats = crawl(cfg(site, tmp_path), progress=lambda m, **kw: messages.append(m))
        assert stats["robots_unavailable"] is True
        assert stats["fetched"] == 0 and stats["pages"] == 0
        assert site.page_hits() == []
        assert [h for h in site.hits if h[1] == "/robots.txt"] and len([h for h in site.hits if h[1] == "/robots.txt"]) >= 2
        assert any("refusé" in m for m in messages)
        assert os.path.exists(os.path.join(tmp_path, "urls.csv"))


def test_robots_503_ignored_when_robots_disabled(tmp_path):
    with FakeSite("robots503", n_pages=2) as site:
        stats = crawl(cfg(site, tmp_path, respect_robots=False))
        assert stats["robots_unavailable"] is True
        assert stats["fetched"] >= 3


def test_named_robots_rule_is_applied(tmp_path):
    with FakeSite("named", n_pages=3) as site:
        stats = crawl(cfg(site, tmp_path))
        urls = read_urls(tmp_path)
        blocked = site.url + "p/2/"
        assert urls[blocked]["status"] == "robots"
        assert urls[site.url + "p/1/"]["status"] == "200"
        assert stats["blocked"] == 1
        assert not any(h[1] == "/p/2/" for h in site.hits)


def test_rate_is_one_request_per_delay_whatever_workers(tmp_path, monkeypatch):
    # Le limiteur garantit l'espacement des envois. L'heure d'arrivée côté serveur peut se tasser
    # quand une requête traîne en route (machine chargée), d'où deux mesures distinctes :
    # l'écart entre deux envois, et la durée totale vue par le serveur (le débit moyen).
    delay = 0.2
    sent = []
    real_get = requests.Session.get

    def timed_get(self, *args, **kwargs):
        sent.append(time.monotonic())
        return real_get(self, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "get", timed_get)
    with FakeSite("allow", n_pages=6) as site:
        crawl(cfg(site, tmp_path, delay=delay, workers=4))
        ts = sorted(t for t, _ in site.hits)
        n = len(ts)
        assert n >= 8   # robots + accueil + 6 pages (+ pdf, sitemap)
        assert ts[-1] - ts[0] >= (n - 1) * delay - 0.05, f"{n} requêtes en {ts[-1] - ts[0]:.2f} s"
    sent.sort()
    assert len(sent) == n
    gaps = [b - a for a, b in zip(sent, sent[1:])]
    assert min(gaps) >= delay - 0.05, f"écart minimal entre deux envois : {min(gaps):.3f} s"


def test_429_retry_after_respected_then_page_fetched(tmp_path):
    with FakeSite("ratelimit", n_pages=1, retry_after="1") as site:
        t0 = time.monotonic()
        stats = crawl(cfg(site, tmp_path, workers=1))
        elapsed = time.monotonic() - t0
        urls = read_urls(tmp_path)
        assert urls[site.url + "p/1/"]["status"] == "200"
        hits_p1 = [t for t, p in site.hits if p == "/p/1/"]
        assert len(hits_p1) == 2
        assert hits_p1[1] - hits_p1[0] >= 0.95, "Retry-After: 1 non respecté"
        assert elapsed < 8, "le repli exponentiel par défaut n'a pas dû s'appliquer"
        assert stats["fetched"] >= 2


def test_429_persistent_gives_up_with_status(tmp_path):
    with FakeSite("ratelimit", n_pages=1, retry_after="1") as site:
        site.rate_limited_once = _AlwaysEmpty()   # chaque requête /p/ est limitée
        stats = crawl(cfg(site, tmp_path, workers=1, retries=1))
        urls = read_urls(tmp_path)
        assert urls[site.url + "p/1/"]["status"] == "429"
        assert len([t for t, p in site.hits if p == "/p/1/"]) == 2
        assert stats["statuses"]["429"] == 1


class _AlwaysEmpty(set):
    def add(self, x):
        pass

    def __contains__(self, x):
        return False


def test_sponsored_and_ugc_are_nofollow(tmp_path):
    with FakeSite("rels", n_pages=3) as site:
        crawl(cfg(site, tmp_path))
        edges = read_edges(tmp_path)
        by_anchor = {e["anchor"]: e["nofollow"] for e in edges if e["source"] == site.url}
        assert by_anchor["sponso"] == "1"
        assert by_anchor["ugc"] == "1"
        assert by_anchor["nf"] == "1"
        assert by_anchor["suivi"] == "0"
        assert by_anchor["page 1"] == "0"


def test_pdf_fetched_without_links(tmp_path):
    with FakeSite("allow", n_pages=1) as site:
        crawl(cfg(site, tmp_path))
        urls = read_urls(tmp_path)
        assert urls[site.url + "doc.pdf"]["status"] == "200"
        assert not any(e["source"] == site.url + "doc.pdf" for e in read_edges(tmp_path))


def test_body_over_limit_keeps_status_but_no_links(tmp_path):
    with FakeSite("allow", n_pages=2) as site:
        crawl(cfg(site, tmp_path, max_body_bytes=40))
        urls = read_urls(tmp_path)
        assert urls[site.url]["status"] == "200"
        assert read_edges(tmp_path) == []


def test_sitemap_urls_uncrawled_when_budget_exhausted(tmp_path):
    with FakeSite("allow", n_pages=4) as site:
        stats = crawl(cfg(site, tmp_path, max_pages=2, workers=1))
        urls = read_urls(tmp_path)
        assert stats["fetched"] == 2
        assert urls[site.url + "orpheline/"]["status"] == "uncrawled"
        assert stats["sitemap_uncrawled"] >= 1
        assert stats["sitemap_urls"] == 5


def test_sitemap_orphan_crawled_when_budget_allows(tmp_path):
    with FakeSite("allow", n_pages=1) as site:
        stats = crawl(cfg(site, tmp_path))
        urls = read_urls(tmp_path)
        assert urls[site.url + "orpheline/"]["status"] == "404"
        assert stats["sitemap_uncrawled"] == 0


def test_allowed_uses_both_tokens(tmp_path):
    with FakeSite("named") as site:
        c = LiveCrawler(cfg(site, tmp_path))
        c._load_robots()
        assert c.robots is not None
        assert not c._allowed(site.url + "p/2/")
        assert c._allowed(site.url + "p/1/")


# ── crawl statique ───────────────────────────────────────────────────────────

def test_is_nofollow():
    assert is_nofollow(["nofollow"]) and is_nofollow(["Sponsored"]) and is_nofollow("ugc noopener")
    assert not is_nofollow(None) and not is_nofollow([]) and not is_nofollow(["noopener", "noreferrer"])


def test_html_file_urls_forms(tmp_path):
    root = str(tmp_path)
    assert html_file_urls(root, os.path.join(root, "about.html"), "https://x.fr") == ("https://x.fr/about/", "https://x.fr/about.html")
    assert html_file_urls(root, os.path.join(root, "about.html"), "https://x.fr", trailing_slash=False) == (
        "https://x.fr/about.html", "https://x.fr/about/")
    assert html_file_urls(root, os.path.join(root, "sub", "index.html"), "https://x.fr") == ("https://x.fr/sub/", "https://x.fr/sub/index.html")
    assert file_to_url(root, os.path.join(root, "subindex.html"), "https://x.fr") == "https://x.fr/subindex/"
    assert file_to_url(root, os.path.join(root, "404.html"), "https://x.fr") == "https://x.fr/404.html"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    _write(str(d / "index.html"), "<html><head><title>Accueil</title></head><body><main>"
           "<a href='/about.html'>about html</a><a href='/about'>about</a><a href='/brochure.pdf'>pdf</a>"
           "<a href='/sub/'>sub</a><a href='/subindex.html'>subindex</a><a href='/absente/'>absente</a>"
           "<a href='/p/' rel='sponsored'>sponso</a><a href='/p/' rel='ugc'>ugc</a><a href='/logo.png'>img</a>"
           "</main></body></html>")
    _write(str(d / "about.html"), "<html><head><title>About</title></head><body><main><a href='/'>home</a></main></body></html>")
    _write(str(d / "sub" / "index.html"), "<html><head><title>Sub</title></head><body></body></html>")
    _write(str(d / "subindex.html"), "<html><head><title>Subindex</title></head><body></body></html>")
    _write(str(d / "p" / "index.html"), "<html><head><title>P</title></head><body></body></html>")
    _write(str(d / "brochure.pdf"), "%PDF-1.4")
    _write(str(d / "logo.png"), "png")
    _write(str(d / "_astro" / "x.css"), "a{}")
    _write(str(d / "404.html"), "<html><title>404</title></html>")
    return d


def test_static_html_link_found_with_canonical(dist, tmp_path):
    out = tmp_path / "out"
    info = crawl_static(str(dist), "https://x.fr", str(out))
    urls = read_urls(out)
    assert urls["https://x.fr/about/"]["status"] == "200" and urls["https://x.fr/about/"]["canonical"] == ""
    assert urls["https://x.fr/about.html"]["status"] == "200"
    assert urls["https://x.fr/about.html"]["canonical"] == "https://x.fr/about/"
    assert "https://x.fr/absente/" in urls and urls["https://x.fr/absente/"]["status"] == "404"
    assert info["missing_targets"] == 1
    assert info["pages"] == 5


def test_static_pdf_is_200_without_links(dist, tmp_path):
    out = tmp_path / "out"
    info = crawl_static(str(dist), "https://x.fr", str(out))
    urls = read_urls(out)
    assert urls["https://x.fr/brochure.pdf"]["status"] == "200"
    assert "https://x.fr/logo.png" not in urls, "les images ne sont pas des cibles de liens (SKIP_EXT)"
    assert "https://x.fr/_astro/x.css" not in urls
    assert info["assets"] == 1
    assert not any(e["source"] == "https://x.fr/brochure.pdf" for e in read_edges(out))


def test_static_subindex_distinct_from_sub(dist, tmp_path):
    out = tmp_path / "out"
    crawl_static(str(dist), "https://x.fr", str(out))
    urls = read_urls(out)
    assert urls["https://x.fr/sub/"]["title"] == "Sub"
    assert urls["https://x.fr/subindex/"]["title"] == "Subindex"
    assert urls["https://x.fr/subindex.html"]["canonical"] == "https://x.fr/subindex/"
    assert urls["https://x.fr/sub/index.html"]["canonical"] == "https://x.fr/sub/"
    assert "https://x.fr/sub" not in urls


def test_static_sponsored_ugc_nofollow(dist, tmp_path):
    out = tmp_path / "out"
    crawl_static(str(dist), "https://x.fr", str(out))
    by_anchor = {e["anchor"]: e["nofollow"] for e in read_edges(out) if e["source"] == "https://x.fr/"}
    assert by_anchor["sponso"] == "1" and by_anchor["ugc"] == "1"
    assert by_anchor["about html"] == "0"
    assert "img" not in by_anchor


def test_static_no_trailing_slash_inverts_canonical(dist, tmp_path):
    out = tmp_path / "out"
    crawl_static(str(dist), "https://x.fr", str(out), trailing_slash=False)
    urls = read_urls(out)
    assert urls["https://x.fr/about.html"]["canonical"] == ""
    assert urls["https://x.fr/about/"]["canonical"] == "https://x.fr/about.html"
