# -*- coding: utf-8 -*-
"""Crawl live d'un site : BFS depuis l'URL de départ, même hôte, robots.txt respecté par défaut.

Produit edges.csv (source,target,position,nofollow,anchor) et urls.csv
(url,status,redirect_target,canonical,title). Les positions sont détectées dans le DOM
(header, nav, footer, sidebar, breadcrumb, content). Les redirections ne sont pas suivies
automatiquement : chaque saut est enregistré avec sa cible pour que le nettoyage résolve la
chaîne. Le sitemap est lu pour compléter la file quand le budget le permet (inventaire des
orphelines) ; les URL du sitemap que le budget n'a pas permis de crawler sont écrites dans
urls.csv avec le statut littéral `uncrawled`.

Robots : `robots.txt` est lu avant tout. Un 4xx vaut « absent » (tout est permis) ; un 5xx ou une
erreur réseau, après trois essais, interdit le crawl (RFC 9309 § 2.3.1.4) : aucune page n'est
récupérée et `stats["robots_unavailable"]` vaut True. Les règles sont testées avec le jeton
`pagerank-graph` ET avec `*` : une URL refusée par l'un des deux est écrite dans urls.csv avec le
statut littéral `robots`, jamais récupérée. Les statuts `robots` et `uncrawled` sont lus comme
« page non récupérée » par le lecteur de table.

Débit : au plus une requête HTTP toutes les `delay` secondes, quel que soit le nombre de
`workers` (verrou et horodatage partagés). Plancher de 0,1 s : un `delay` compris entre 0 et 0,1
est relevé à 0,1 ; seul un `delay` explicitement négatif supprime toute attente. Sur 429 ou 503,
la page est retentée après `Retry-After` (plafonné) ou, à défaut, 2 s, 4 s puis 8 s ; au bout de
trois nouveaux essais elle est abandonnée avec son statut.

Taille : les réponses sont lues en flux, seules les réponses HTML sont parsées, et le corps lu
est plafonné à `max_body_bytes` (5 Mo par défaut) ; au-delà la page garde son statut mais ses
liens ne sont pas extraits.

Statistiques renvoyées : `fetched` (réponses HTTP reçues, tous codes), `blocked` (refusées par
robots), `errors` (erreurs réseau, écrites en 599), `pages` (alias de `fetched`, conservé pour
compatibilité), `edges`, `statuses`, `sitemap_urls`, `sitemap_uncrawled`, `robots_unavailable`,
`discovered_not_crawled`, `duration_s`, `stopped`, `out_dir`.
"""
from __future__ import annotations

import csv
import io
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from . import __version__
from .crawl_static import SKIP_EXT, detect_position, is_nofollow

# Jeton annoncé en tête de l'agent : c'est lui que les éditeurs peuvent viser dans robots.txt
# (`RobotFileParser` ne retient de l'agent que le texte avant la première barre).
ROBOTS_TOKEN = "pagerank-graph"
UA = f"{ROBOTS_TOKEN}/{__version__} (Mozilla/5.0 compatible; audit SEO local)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
MIN_DELAY = 0.1
RETRY_STATUSES = (429, 503)
ROBOTS_STATUS = "robots"        # statut littéral écrit dans urls.csv pour une URL refusée par robots.txt
UNCRAWLED_STATUS = "uncrawled"  # statut littéral pour une URL du sitemap non crawlée (budget épuisé)
NETWORK_ERROR_STATUS = 599      # erreur réseau : aucun code HTTP reçu


@dataclass
class CrawlConfig:
    start_url: str
    out_dir: str
    max_pages: int = 500
    delay: float = 0.3               # secondes entre deux requêtes, toutes threads confondues
    workers: int = 4
    respect_robots: bool = True
    timeout: float = 15.0
    use_sitemap: bool = True
    strip_query: bool = True
    include_subdomains: bool = False
    max_body_bytes: int = 5 * 1024 * 1024   # corps HTML lu au maximum
    retries: int = 3                 # nouveaux essais sur 429 et 503 (et au chargement de robots.txt)
    backoff_base: float = 2.0        # repli exponentiel : 2 s, 4 s, 8 s sans Retry-After
    max_retry_wait: float = 60.0     # plafond d'un Retry-After annoncé par le site

    def __post_init__(self):
        # Plancher : un délai nul ou trop court est relevé à MIN_DELAY ; un délai négatif, forcément
        # explicite, supprime l'attente (sites locaux, tests).
        if self.delay < 0:
            self.delay = 0.0
        elif self.delay < MIN_DELAY:
            self.delay = MIN_DELAY
        self.workers = max(1, int(self.workers))
        self.retries = max(0, int(self.retries))


@dataclass
class PageResult:
    url: str
    status: int | None
    redirect: str | None = None
    canonical: str | None = None
    title: str = ""
    links: list = field(default_factory=list)   # (target, position, nofollow, anchor)
    error: str = ""


def _norm(url: str, strip_query: bool) -> str:
    p = urlsplit(url.strip())
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    path = p.path or "/"
    return urlunsplit((scheme, netloc, path, "" if strip_query else p.query, ""))


def _same_site(host: str, base_host: str, subdomains: bool) -> bool:
    if host == base_host:
        return True
    root = base_host[4:] if base_host.startswith("www.") else base_host
    if host == root or host == "www." + root:
        return True
    return subdomains and host.endswith("." + root)


def _retry_after(headers, fallback: float, cap: float) -> float:
    """Durée d'attente demandée par `Retry-After` (secondes entières ou date HTTP), sinon `fallback`."""
    raw = (headers.get("Retry-After") or "").strip()
    if not raw:
        return fallback
    if raw.isdigit():
        return min(float(raw), cap)
    try:
        from email.utils import parsedate_to_datetime
        wait = parsedate_to_datetime(raw).timestamp() - time.time()
        return min(max(wait, 0.0), cap)
    except (TypeError, ValueError):
        return fallback


def _charset(ctype: str) -> str | None:
    m = re.search(r"charset=([\w.-]+)", ctype, re.I)
    return m.group(1) if m else None


class LiveCrawler:
    def __init__(self, cfg: CrawlConfig, progress=None, stop_event: threading.Event | None = None):
        self.cfg = cfg
        self.progress = progress or (lambda msg, **kw: None)
        self.stop_event = stop_event or threading.Event()
        self.start = _norm(cfg.start_url, cfg.strip_query)
        self.base_host = urlsplit(self.start).netloc
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "fr,en;q=0.8"})
        self.robots: RobotFileParser | None = None
        self.robots_unavailable = False
        self.seen: set[str] = set()
        self.results: dict[str, PageResult] = {}
        self.sitemap_urls: list[str] = []
        self.sitemap_set: set[str] = set()   # URL du sitemap (normalisées, même site) pour l'inventaire
        self._lock = threading.Lock()
        self._next_slot = 0.0                # horodatage du prochain créneau de requête, partagé entre threads
        self._n_fetched = 0                  # réponses HTTP reçues (le budget max_pages porte sur elles)

    # ── débit ────────────────────────────────────────────────────────────────
    def _throttle(self) -> None:
        """Réserve un créneau : au plus une requête toutes les `delay` secondes, toutes threads confondues."""
        if self.cfg.delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.cfg.delay
        wait = slot - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _get(self, url: str, **kw):
        """GET soumis au débit global. Lève requests.RequestException."""
        self._throttle()
        return self.session.get(url, timeout=self.cfg.timeout, **kw)

    def _wait(self, seconds: float) -> bool:
        """Attend `seconds` sauf arrêt demandé ; renvoie False si l'arrêt a été demandé."""
        if seconds <= 0:
            return not self.stop_event.is_set()
        return not self.stop_event.wait(seconds)

    # ── robots et sitemap ────────────────────────────────────────────────────
    def _load_robots(self) -> None:
        """Lit robots.txt. 2xx : règles chargées ; 4xx : absent ; 5xx ou erreur réseau après
        `retries` nouveaux essais : `robots_unavailable`, le crawl est refusé si robots est respecté."""
        robots_url = urlunsplit((urlsplit(self.start).scheme, self.base_host, "/robots.txt", "", ""))
        attempts = 1 + self.cfg.retries
        last = ""
        for i in range(attempts):
            try:
                r = self._get(robots_url)
            except requests.RequestException as exc:
                last = f"erreur réseau ({str(exc)[:80]})"
            else:
                if 200 <= r.status_code < 300:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
                    self.robots = rp
                    for line in r.text.splitlines():
                        if line.lower().startswith("sitemap:"):
                            self.sitemap_urls.append(line.split(":", 1)[1].strip())
                    break
                if 400 <= r.status_code < 500:
                    break   # absent : tout est permis
                last = f"HTTP {r.status_code}"
            if i + 1 < attempts:
                self.progress(f"robots.txt indisponible ({last}), nouvel essai {i + 2}/{attempts}")
                if not self._wait(1.0 * (i + 1)):
                    break
        else:
            self.robots_unavailable = True
            self.progress(f"robots.txt indisponible après {attempts} essais ({last})")
        if not self.sitemap_urls:
            self.sitemap_urls.append(urlunsplit((urlsplit(self.start).scheme, self.base_host, "/sitemap.xml", "", "")))

    def _allowed(self, url: str) -> bool:
        if not self.cfg.respect_robots or self.robots is None:
            return True
        try:
            # jeton nommé ET groupe générique : une règle visant l'outil s'applique même si `*` est permis
            return self.robots.can_fetch(ROBOTS_TOKEN, url) and self.robots.can_fetch("*", url)
        except Exception:  # noqa: BLE001
            return True

    def _read_sitemaps(self, limit: int = 5000) -> list[str]:
        urls, queue, seen = [], deque(self.sitemap_urls), set()
        while queue and len(urls) < limit and len(seen) < 50 and not self.stop_event.is_set():
            sm = queue.popleft()
            if sm in seen:
                continue
            seen.add(sm)
            try:
                r = self._get(sm)
                if r.status_code != 200:
                    continue
                text = r.text
            except requests.RequestException:
                continue
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)
            if "<sitemapindex" in text[:2000]:
                queue.extend(locs)
            else:
                urls.extend(locs)
        return urls

    def _sitemap_same_site(self) -> list[str]:
        """URL du sitemap normalisées et restreintes au site ; mémorisées pour l'inventaire final."""
        out = []
        for u in self._read_sitemaps():
            n = _norm(u, self.cfg.strip_query)
            if _same_site(urlsplit(n).netloc, self.base_host, self.cfg.include_subdomains):
                out.append(n)
                self.sitemap_set.add(n)
        return out

    # ── récupération d'une page ──────────────────────────────────────────────
    def _request(self, url: str):
        """GET sans suivre les redirections, retenté sur 429/503 (Retry-After, sinon repli exponentiel).
        Renvoie la réponse (corps non lu, `stream=True`) ou lève requests.RequestException."""
        r = self._get(url, allow_redirects=False, stream=True)
        for attempt in range(self.cfg.retries):
            if r.status_code not in RETRY_STATUSES or self.stop_event.is_set():
                break
            wait = _retry_after(r.headers, self.cfg.backoff_base * (2 ** attempt), self.cfg.max_retry_wait)
            r.close()
            self.progress(f"HTTP {r.status_code} sur {url}, attente {wait:.1f} s avant l'essai {attempt + 2}/{self.cfg.retries + 1}")
            if not self._wait(wait):
                break
            r = self._get(url, allow_redirects=False, stream=True)
        return r

    def _read_body(self, r) -> bytes | None:
        """Lit le corps en flux, plafonné à `max_body_bytes` ; None si le plafond est dépassé."""
        declared = r.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > self.cfg.max_body_bytes:
            return None
        chunks, total = [], 0
        for chunk in r.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > self.cfg.max_body_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    def fetch(self, url: str) -> PageResult:
        try:
            r = self._request(url)
        except requests.RequestException as exc:
            return PageResult(url=url, status=None, error=str(exc)[:120])
        try:
            res = PageResult(url=url, status=r.status_code)
            if 300 <= r.status_code < 400:
                loc = r.headers.get("Location")
                res.redirect = _norm(urljoin(url, loc), self.cfg.strip_query) if loc else None
                return res
            ctype = r.headers.get("Content-Type", "")
            if r.status_code != 200 or not any(t in ctype.lower() for t in HTML_TYPES):
                return res   # non HTML (PDF, image, flux) : statut conservé, corps jamais lu ni parsé
            try:
                body = self._read_body(r)
            except requests.RequestException as exc:
                res.error = f"lecture interrompue ({str(exc)[:80]})"
                return res
            if body is None:
                res.error = f"corps > {self.cfg.max_body_bytes} octets, liens non extraits"
                return res
        finally:
            r.close()
        self._parse(res, body, ctype)
        return res

    def _parse(self, res: PageResult, body: bytes, ctype: str) -> None:
        url = res.url
        soup = BeautifulSoup(body, "lxml", from_encoding=_charset(ctype))
        t = soup.find("title")
        res.title = t.get_text(strip=True)[:200] if t else ""
        can = soup.find("link", rel=lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])])
        if can and can.get("href"):
            res.canonical = _norm(urljoin(url, can["href"]), self.cfg.strip_query)
        robots_meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
        page_nofollow = bool(robots_meta and "nofollow" in (robots_meta.get("content") or "").lower())
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:", "data:")):
                continue
            absu = urljoin(url, href)
            p = urlsplit(absu)
            if p.scheme not in ("http", "https") or not _same_site(p.netloc.lower(), self.base_host, self.cfg.include_subdomains):
                continue
            if p.path.lower().endswith(SKIP_EXT):
                continue
            target = _norm(absu, self.cfg.strip_query)
            nofollow = page_nofollow or is_nofollow(a.get("rel"))
            anchor = a.get_text(" ", strip=True)[:200]
            if not anchor:
                img = a.find("img")
                anchor = (img.get("alt") or "")[:200] if img else ""
            res.links.append((target, detect_position(a), int(nofollow), anchor))

    # ── boucle principale ────────────────────────────────────────────────────
    def run(self) -> dict:
        cfg = self.cfg
        t0 = time.time()
        self._load_robots()
        if self.robots_unavailable and cfg.respect_robots:
            # RFC 9309 : un robots.txt injoignable (5xx) vaut interdiction complète. Rien n'est récupéré ;
            # les fichiers sont écrits vides pour que la chaîne en aval trouve ses entrées.
            self.progress(f"crawl refusé : robots.txt de {self.base_host} indisponible (5xx ou erreur réseau), "
                          "réessayer plus tard ou passer --no-robots en connaissance de cause")
            stats = self._write()
            stats.update({"duration_s": round(time.time() - t0, 1), "sitemap_urls": 0, "sitemap_uncrawled": 0,
                          "discovered_not_crawled": 0, "stopped": self.stop_event.is_set()})
            return stats
        self.progress(f"robots.txt {'chargé' if self.robots else 'absent'}, hôte {self.base_host}, "
                      f"délai {cfg.delay:g} s par requête, {cfg.workers} workers")
        queue: deque[str] = deque([self.start])
        self.seen.add(self.start)
        sitemap_added = False
        sitemap_total = 0
        with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
            while (queue or not sitemap_added) and self._fetched() < cfg.max_pages and not self.stop_event.is_set():
                if not queue and not sitemap_added and cfg.use_sitemap:
                    sitemap_added = True
                    sm = self._sitemap_same_site()
                    sitemap_total = len(sm)
                    added = 0
                    for u in sm:
                        if u not in self.seen:
                            self.seen.add(u)
                            queue.append(u)
                            added += 1
                    self.progress(f"sitemap : {sitemap_total} URL, {added} nouvelles ajoutées à la file")
                    continue
                if not queue:
                    break
                batch = []
                while queue and len(batch) < cfg.workers and self._fetched() + len(batch) < cfg.max_pages:
                    u = queue.popleft()
                    if not self._allowed(u):
                        self.results[u] = PageResult(url=u, status=None, error=ROBOTS_STATUS)
                        continue
                    batch.append(u)
                if not batch:
                    continue
                # le débit est tenu dans fetch (_throttle) : les workers se partagent les créneaux
                futures = {pool.submit(self.fetch, u): u for u in batch}
                for fut in as_completed(futures):
                    res = fut.result()
                    self.results[res.url] = res
                    if res.status is not None:
                        self._n_fetched += 1
                    if res.redirect and res.redirect not in self.seen and _same_site(urlsplit(res.redirect).netloc, self.base_host, cfg.include_subdomains):
                        self.seen.add(res.redirect)
                        queue.append(res.redirect)
                    for target, _, _, _ in res.links:
                        if target not in self.seen:
                            self.seen.add(target)
                            queue.append(target)
                n = self._fetched()
                if n % 20 < len(batch) or n >= cfg.max_pages:
                    self.progress(f"{n} pages récupérées, {len(queue)} en file", pages=n, queued=len(queue))
        if not sitemap_added and cfg.use_sitemap and not self.stop_event.is_set():
            # budget épuisé avant lecture du sitemap : lu quand même, ses URL non crawlées sont inventoriées
            sitemap_total = len(self._sitemap_same_site())
        not_crawled = [u for u in self.seen if u not in self.results]
        uncrawled = sorted(u for u in self.sitemap_set if u not in self.results)
        stats = self._write(uncrawled)
        stats.update({"duration_s": round(time.time() - t0, 1), "sitemap_urls": sitemap_total,
                      "sitemap_uncrawled": len(uncrawled),
                      "discovered_not_crawled": len(not_crawled), "stopped": self.stop_event.is_set()})
        self.progress(f"crawl terminé : {stats['fetched']} pages récupérées, {stats['blocked']} refusées par robots, "
                      f"{stats['errors']} erreurs réseau, {stats['edges']} liens, {stats['duration_s']} s", **stats)
        return stats

    def _fetched(self) -> int:
        """Réponses HTTP réellement reçues (le budget `max_pages` porte sur elles, pas sur les refus robots)."""
        return self._n_fetched

    def _write(self, uncrawled: list[str] | None = None) -> dict:
        os.makedirs(self.cfg.out_dir, exist_ok=True)
        n_edges = 0
        with io.open(os.path.join(self.cfg.out_dir, "edges.csv"), "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["source", "target", "position", "nofollow", "anchor"])
            for res in self.results.values():
                for target, pos, nofollow, anchor in res.links:
                    # cibles découvertes mais non crawlées : statut inconnu, le nettoyage les suppose en 200
                    w.writerow([res.url, target, pos, nofollow, anchor])
                    n_edges += 1
        statuses: dict[str, int] = {}
        fetched = blocked = errors = 0
        with io.open(os.path.join(self.cfg.out_dir, "urls.csv"), "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["url", "status", "redirect_target", "canonical", "title"])
            for res in self.results.values():
                if res.status is not None:
                    st = res.status
                    fetched += 1
                elif res.error == ROBOTS_STATUS:
                    st = ROBOTS_STATUS
                    blocked += 1
                else:
                    st = NETWORK_ERROR_STATUS
                    errors += 1
                w.writerow([res.url, st, res.redirect or "", res.canonical or "", res.title])
                statuses[str(st)] = statuses.get(str(st), 0) + 1
            for u in uncrawled or []:
                w.writerow([u, UNCRAWLED_STATUS, "", "", ""])
                statuses[UNCRAWLED_STATUS] = statuses.get(UNCRAWLED_STATUS, 0) + 1
        return {"pages": fetched, "fetched": fetched, "blocked": blocked, "errors": errors,
                "robots_unavailable": self.robots_unavailable, "edges": n_edges, "statuses": statuses,
                "out_dir": self.cfg.out_dir}


def crawl(cfg: CrawlConfig, progress=None, stop_event=None) -> dict:
    return LiveCrawler(cfg, progress, stop_event).run()
