# -*- coding: utf-8 -*-
"""Serveur local : page de lancement (URL à analyser), crawl + calcul en arrière-plan, rapports.

    python -m prgraph serve --port 8765 --runs runs/

Routes : GET /            lanceur
         POST /api/analyze {url, max_pages, delay, weights_profile, respect_robots, dangling,
                            business_source: none|csv|gsc|ga4, business_path, gsc_site, ga4_property,
                            ga4_dimension, business_days}
         GET /api/status   état de l'analyse en cours et liste des analyses
         GET /api/business/capabilities  identifiants Google trouvés ? bibliothèques GSC / GA4 présentes ?
         GET /api/gsc/sites              propriétés Search Console accessibles (sites.list)
         POST /api/stop    arrête le crawl en cours (le calcul se fait sur ce qui est récupéré)
         GET /runs/<id>/…  fichiers d'une analyse (report.html, findings.md, CSV)

Données business : pendant la phase « calcul », le serveur appelle les connecteurs (prgraph/connectors.py),
écrit business.csv dans le dossier du run et passe --business à compute. Les identifiants Google sont lus
depuis GOOGLE_APPLICATION_CREDENTIALS, jamais stockés ici.

Protections (le serveur n'écoute que sur 127.0.0.1, mais un site web visité peut lui adresser des requêtes) :
  - chemins sous /runs/ et identifiants d'analyse résolus par resolve_run_path (realpath + commonpath), 403 sinon ;
  - en-tête Host limité à localhost / 127.0.0.1 sur le port d'écoute, Origin (s'il est présent) de même ;
  - jeton anti-CSRF aléatoire (State.token), injecté dans le lanceur et dans les HTML servis sous /runs/,
    exigé dans l'en-tête X-Prgraph-Token de tout POST ; Content-Type: application/json obligatoire ;
  - corps des POST plafonné à MAX_BODY octets (413 au-delà), toute erreur renvoyée en JSON (400 ou 500) ;
  - HEAD refusé (405), Content-Security-Policy et X-Content-Type-Options: nosniff sur les réponses.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .crawl_live import CrawlConfig, crawl

VIEWER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer")

MAX_BODY = 64 * 1024          # taille maximale du corps d'un POST (octets)
DRAIN_MAX = 1024 * 1024       # au-delà, un corps refusé n'est plus lu : la connexion est fermée
TOKEN_HEADER = "X-Prgraph-Token"
TOKEN_PLACEHOLDER = "__PRGRAPH_TOKEN__"
NONCE_PLACEHOLDER = "__PRGRAPH_NONCE__"

# CSP des réponses JSON et des erreurs : rien ne doit s'y exécuter.
CSP_NONE = "default-src 'none'; frame-ancestors 'none'"
# CSP du lanceur : scripts en ligne autorisés par nonce seulement, police système, appels à l'API même origine.
CSP_LAUNCHER = ("default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
                "font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# CSP des rapports (fichiers statiques déjà générés, scripts en ligne sans nonce) : bibliothèques embarquées depuis
# 0.5.0, donc aucune origine tierce ; exports CSV par blob:, aucun appel réseau hors même origine.
CSP_RUNS_HTML = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                 "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
                 "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; "
                 "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# Rapport produit avec --cdn (ou par une version antérieure) : seul cdn.jsdelivr.net est ajouté pour les scripts.
CSP_RUNS_HTML_CDN = CSP_RUNS_HTML.replace("script-src 'self' 'unsafe-inline';",
                                          "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net;")


class UnsafeRunPath(PermissionError):
    """Chemin ou identifiant d'analyse qui sort du dossier des analyses."""


def resolve_run_path(runs_dir: str, rel: str) -> str:
    """Chemin réel de `rel` sous `runs_dir`. Lève UnsafeRunPath si `rel` est vide, absolu, « relatif au lecteur »
    Windows (C:..\\x), s'il contient une barre oblique inverse (séparateur sous Windows, simple caractère sous
    Linux : refusée partout pour que la règle soit la même sur tous les systèmes) ou s'il sort du dossier après
    résolution (.., liens symboliques, jonctions)."""
    if not isinstance(rel, str) or not rel or "\0" in rel or ":" in rel or "\\" in rel or rel.startswith("/"):
        raise UnsafeRunPath(rel)
    real_runs = os.path.realpath(runs_dir)
    full = os.path.realpath(os.path.join(real_runs, rel))
    try:
        common = os.path.commonpath([full, real_runs])
    except ValueError:                      # lecteurs différents sous Windows
        raise UnsafeRunPath(rel) from None
    if os.path.normcase(common) != os.path.normcase(real_runs) or os.path.normcase(full) == os.path.normcase(real_runs):
        raise UnsafeRunPath(rel)
    return full


# ── Données business : connecteurs, couverture ────────────────────────────────

def business_capabilities() -> dict:
    """Ce que le poste permet : identifiants Google trouvés, bibliothèques GSC / GA4 importables."""
    from importlib.util import find_spec
    from . import connectors
    path = connectors.credentials_path()
    return {
        "credentials": bool(path and os.path.isfile(path)),
        "credentials_path": path or "",
        "gsc": find_spec("googleapiclient") is not None,
        "ga4": find_spec("google.analytics.data_v1beta") is not None,
    }


def prepare_business(params: dict, out_dir: str, base_url: str, progress) -> str | None:
    """Selon `business_source`, renvoie le chemin du CSV business à passer à compute :
    csv → chemin saisi ; gsc / ga4 → connecteur appelé, business.csv écrit dans `out_dir` ; none → None."""
    from . import connectors
    source = (params.get("business_source") or ("csv" if params.get("business_path") else "none")).lower()
    if source == "none":
        return None
    if source == "csv":
        return params.get("business_path") or None
    days = int(params.get("business_days") or 90)
    start, end = connectors.period_from_days(days)
    out = os.path.join(out_dir, "business.csv")
    if source == "gsc":
        site = (params.get("gsc_site") or "").strip() or connectors.default_gsc_site(base_url)
        progress(f"Search Console : propriété {site}, du {start} au {end} (dimension page, type web)")
        creds = connectors.load_credentials(connectors.GSC_SCOPES)
        rows = connectors.fetch_gsc_pages(site, start, end, creds)
        label = "Search Console"
    elif source == "ga4":
        prop = str(params.get("ga4_property") or "").strip()
        if not prop:
            raise RuntimeError("identifiant de propriété GA4 manquant")
        dim = params.get("ga4_dimension") or "landingPagePlusQueryString"
        progress(f"GA4 : propriété {prop}, du {start} au {end}, dimension {dim}, chemins préfixés par {base_url}")
        creds = connectors.load_credentials(connectors.GA4_SCOPES)
        rows = connectors.fetch_ga4_landing_pages(prop, start, end, creds, base_url=base_url, dimension=dim)
        label = "GA4"
    else:
        raise RuntimeError(f"source business inconnue : {source}")
    table = connectors.rows_to_table(rows)
    header = connectors.write_business_csv(out, table)
    progress(f"{label} : {len(table)} URL récupérées, colonnes {', '.join(header[1:])} → business.csv")
    return out


def business_coverage(pages_csv: str, business_csv: str) -> dict:
    """Jointure a posteriori : URL business retrouvées dans le graphe, pages du graphe avec une valeur."""
    from .clean import normalize_url
    from .loaders import read_business, read_table
    graph_urls: set[str] = set()
    with_value = 0
    for row in read_table(pages_csv):
        u = row.get("url", "")
        if not u:
            continue
        graph_urls.add(normalize_url(u))
        try:
            if float(row.get("value", "0") or 0) > 0:
                with_value += 1
        except ValueError:
            pass
    business, _ = read_business(business_csv)
    joined = sum(1 for u in business if normalize_url(u) in graph_urls)
    n = len(graph_urls)
    return {"graph_pages": n, "business_urls": len(business), "joined": joined, "with_value": with_value,
            "coverage": (with_value / n) if n else 0.0}


def resolve_strategic(entries: list[str], urls_csv: str, base_url: str) -> list[str]:
    """Entrées du formulaire (URL complète, chemin ou regex) → URL crawlées correspondantes."""
    import re as _re
    from .loaders import read_table, pick
    if not entries or not os.path.isfile(urls_csv):
        return []
    crawled = [pick(r, "url") for r in read_table(urls_csv) if pick(r, "status", default="200").startswith("2") or not pick(r, "status")]
    base = base_url.rstrip("/")
    out: list[str] = []
    for raw in entries:
        q = raw.strip()
        if not q:
            continue
        exact = {q, q.rstrip("/"), base + q if q.startswith("/") else q}
        hits = [u for u in crawled if u in exact or u.rstrip("/") in exact]
        if not hits:
            try:
                rx = _re.compile(q, _re.I)
                hits = [u for u in crawled if rx.search(u)]
            except _re.error:
                hits = []
        for u in hits:
            if u not in out:
                out.append(u)
    return out[:200]


def crawl_failure(stats: dict, respect_robots: bool = True) -> str | None:
    """Message d'erreur affiché par le lanceur quand le crawl n'a récupéré aucune page ; None sinon.
    La cause est lue dans les statistiques du crawl (`fetched`, `blocked`, `errors`, `robots_unavailable`)."""
    fetched = stats.get("fetched", stats.get("pages", 0)) or 0
    if fetched > 0:
        return None
    if stats.get("robots_unavailable") and respect_robots:
        return ("robots.txt injoignable, crawl refusé. Le serveur du site ne répond pas ou répond en erreur sur "
                "robots.txt : réessayer plus tard, ou décocher « Respecter robots.txt » en connaissance de cause.")
    if stats.get("blocked"):
        return "Aucune page récupérée : robots.txt interdit le site."
    if stats.get("stopped"):
        return "Aucune page récupérée : crawl arrêté avant la première page."
    if stats.get("errors"):
        return "Aucune page récupérée : le site ne répond pas (erreur réseau sur l'adresse de départ)."
    return "Aucune page récupérée : adresse injoignable."


def list_runs(runs_dir: str) -> list[dict]:
    """Analyses précédentes : sous-dossiers directs de `runs_dir` qui contiennent job.json ou pages.csv, du plus
    récent au plus ancien (identifiant horodaté). Les dossiers vides ou interrompus avant tout résultat sont omis."""
    out = []
    if not os.path.isdir(runs_dir):
        return out
    for name in sorted(os.listdir(runs_dir), reverse=True):
        d = os.path.join(runs_dir, name)
        if not os.path.isdir(d):
            continue
        jp = os.path.join(d, "job.json")
        if not (os.path.isfile(jp) or os.path.isfile(os.path.join(d, "pages.csv"))):
            continue
        info = {"id": name, "report": os.path.exists(os.path.join(d, "report.html"))}
        if os.path.isfile(jp):
            try:
                j = json.load(io.open(jp, encoding="utf-8"))
                info.update({"url": j["params"].get("url"), "pages": j["crawl"].get("pages"), "edges": j["crawl"].get("edges"),
                             "finished": j.get("finished"), "business_source": j["params"].get("business_source"),
                             "business": j.get("business")})
            except Exception:  # noqa: BLE001
                pass
        out.append(info)
    return out


class Job:
    def __init__(self, params: dict, runs_dir: str):
        host = re.sub(r"[^a-z0-9.-]+", "-", urlsplit(params["url"]).netloc.lower()) or "site"
        self.id = f"{host}-{datetime.now():%Y%m%d-%H%M%S}"
        self.dir = os.path.join(runs_dir, self.id)
        self.params = params
        self.phase = "en attente"
        self.log: list[str] = []
        self.pages = 0
        self.done = False
        self.error = ""
        self.stop_event = threading.Event()
        self.started = time.time()
        self.summary = ""

    def progress(self, msg: str, **kw):
        if "pages" in kw:
            self.pages = kw["pages"]
        self.log.append(f"{datetime.now():%H:%M:%S} {msg}")
        if len(self.log) > 400:
            del self.log[:100]

    def run(self):
        from . import cli
        try:
            os.makedirs(self.dir, exist_ok=True)
            p = self.params
            self.phase = "crawl"
            cfg = CrawlConfig(start_url=p["url"], out_dir=self.dir, max_pages=int(p.get("max_pages", 500)),
                              delay=float(p.get("delay", 0.3)), workers=int(p.get("workers", 4)),
                              respect_robots=bool(p.get("respect_robots", True)),
                              include_subdomains=bool(p.get("include_subdomains", False)))
            stats = crawl(cfg, self.progress, self.stop_event)
            failure = crawl_failure(stats, cfg.respect_robots)
            if failure:
                raise RuntimeError(failure)
            self.phase = "calcul"
            business_path = None
            try:
                business_path = prepare_business(p, self.dir, p["url"], self.progress)
            except Exception as exc:  # noqa: BLE001  (identifiants, bibliothèque, API) : le calcul continue sans business
                self.progress(f"données business indisponibles ({exc}) : calcul sans alignement business")
            self.progress("calcul du PageRank pondéré, du CheiRank et des analyses")
            argv = ["compute", "--edges", os.path.join(self.dir, "edges.csv"), "--urls", os.path.join(self.dir, "urls.csv"),
                    "--home", p["url"], "--site-name", urlsplit(p["url"]).netloc, "--out", self.dir, "--links-source", "crawl",
                    "--weights-profile", p.get("weights_profile", "raisonnable"), "--dangling", p.get("dangling", "uniform"),
                    "--strip-query"]
            if business_path:
                argv += ["--business", business_path]
            strat = resolve_strategic(p.get("strategic_urls") or [], os.path.join(self.dir, "urls.csv"), p["url"])
            if strat:
                strat_path = os.path.join(self.dir, "strategic.csv")
                with io.open(strat_path, "w", encoding="utf-8", newline="") as fh:
                    fh.write("url,poids\n" + "".join(f"{u},3\n" for u in strat))
                argv += ["--strategic", strat_path]
                self.progress(f"pages à pousser : {len(strat)} URL retenues comme cibles stratégiques")
            elif p.get("strategic_urls"):
                self.progress("pages à pousser : aucune des entrées ne correspond à une URL crawlée")
            old_hook = cli.LOG_HOOK
            cli.LOG_HOOK = lambda m: self.progress(m)
            try:
                rc = cli.main(argv)
            finally:
                cli.LOG_HOOK = old_hook
            if rc != 0:
                raise RuntimeError(f"compute a renvoyé {rc}")
            coverage = None
            if business_path and os.path.isfile(business_path):
                try:
                    coverage = business_coverage(os.path.join(self.dir, "pages.csv"), business_path)
                    self.progress(f"jointure business : {coverage['joined']} URL jointes sur {coverage['business_urls']} ; "
                                  f"couverture {coverage['coverage']:.1%} des {coverage['graph_pages']} pages du graphe "
                                  f"({coverage['with_value']} avec une valeur)")
                except Exception as exc:  # noqa: BLE001
                    self.progress(f"couverture business non calculée : {exc}")
            with io.open(os.path.join(self.dir, "job.json"), "w", encoding="utf-8") as fh:
                json.dump({"id": self.id, "params": p, "crawl": stats, "business": coverage,
                           "finished": datetime.now().isoformat(timespec="seconds")}, fh, ensure_ascii=False, indent=1)
            self.phase = "terminé"
            self.progress(f"rapport prêt : /runs/{self.id}/report.html")
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.phase = "erreur"
            self.progress(f"erreur : {exc}")
            self.log.append(traceback.format_exc()[-800:])
        finally:
            self.done = True


class RecomputeJob(Job):
    """Recalcul d'une analyse existante sans recrawl : cibles stratégiques choisies dans le rapport
    (--strategic) et, à terme, classes forcées. Le rapport est régénéré dans le même dossier."""

    def __init__(self, run_id: str, runs_dir: str, strategic_urls: list[str], labels: dict[str, str] | None = None):
        d = os.path.join(runs_dir, run_id)
        jp = os.path.join(d, "job.json")
        job = json.load(io.open(jp, encoding="utf-8")) if os.path.exists(jp) else {"params": {}}
        super().__init__({**job.get("params", {}), "url": job.get("params", {}).get("url", "")}, runs_dir)
        self.id = run_id
        self.dir = d
        self.job_json = job
        self.strategic_urls = [u.strip() for u in strategic_urls if u and u.strip()]
        self.labels = labels or {}
        self.pages = int((job.get("crawl") or {}).get("pages") or 0)

    def run(self):
        from . import cli
        try:
            if not os.path.isfile(os.path.join(self.dir, "edges.csv")):
                raise RuntimeError("edges.csv introuvable dans cette analyse")
            p = self.params
            self.phase = "calcul"
            strat_path = os.path.join(self.dir, "strategic.csv")
            with io.open(strat_path, "w", encoding="utf-8", newline="") as fh:
                fh.write("url,poids\n")
                for u in self.strategic_urls:
                    fh.write(f"{u},3\n")
            self.progress(f"recalcul avec {len(self.strategic_urls)} cible(s) choisie(s) comme stratégiques")
            argv = ["compute", "--edges", os.path.join(self.dir, "edges.csv"), "--out", self.dir, "--strip-query",
                    "--links-source", "crawl",
                    "--weights-profile", p.get("weights_profile", "raisonnable"), "--dangling", p.get("dangling", "uniform"),
                    "--strategic", strat_path]
            if os.path.isfile(os.path.join(self.dir, "urls.csv")):
                argv += ["--urls", os.path.join(self.dir, "urls.csv")]
            if p.get("url"):
                argv += ["--home", p["url"], "--site-name", urlsplit(p["url"]).netloc]
            biz = os.path.join(self.dir, "business.csv")
            if not os.path.isfile(biz):
                biz = os.path.join(self.dir, "gsc_pages.csv") if os.path.isfile(os.path.join(self.dir, "gsc_pages.csv")) else (p.get("business_path") or "")
            if biz and os.path.isfile(biz):
                argv += ["--business", biz]
            if self.labels:
                lp = os.path.join(self.dir, "labels.csv")
                with io.open(lp, "w", encoding="utf-8", newline="") as fh:
                    fh.write("url,label\n")
                    for u, lab in self.labels.items():
                        fh.write(f"{u},{lab}\n")
                argv += ["--labels", lp]
            old_hook = cli.LOG_HOOK
            cli.LOG_HOOK = lambda m: self.progress(m)
            try:
                rc = cli.main(argv)
            finally:
                cli.LOG_HOOK = old_hook
            if rc != 0:
                raise RuntimeError(f"compute a renvoyé {rc}")
            self.job_json["params"]["strategic_urls"] = self.strategic_urls
            self.job_json["strategic_urls"] = self.strategic_urls
            self.job_json["recomputed"] = datetime.now().isoformat(timespec="seconds")
            with io.open(os.path.join(self.dir, "job.json"), "w", encoding="utf-8") as fh:
                json.dump(self.job_json, fh, ensure_ascii=False, indent=1)
            self.phase = "terminé"
            self.progress(f"rapport régénéré : /runs/{self.id}/report.html")
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.phase = "erreur"
            self.progress(f"erreur : {exc}")
            self.log.append(traceback.format_exc()[-800:])
        finally:
            self.done = True


class State:
    def __init__(self, runs_dir: str):
        self.runs_dir = runs_dir
        self.current: Job | None = None
        self.lock = threading.Lock()
        # Jeton anti-CSRF : généré à chaque démarrage, remis au navigateur par le lanceur, exigé sur tout POST.
        self.token = secrets.token_urlsafe(32)

    def run_dir(self, run_id: str) -> str | None:
        """Dossier d'une analyse existante (enfant direct de runs_dir), None si elle n'existe pas.
        Lève UnsafeRunPath si l'identifiant sort du dossier des analyses."""
        full = resolve_run_path(self.runs_dir, run_id)
        if os.path.normcase(os.path.dirname(full)) != os.path.normcase(os.path.realpath(self.runs_dir)):
            raise UnsafeRunPath(run_id)
        return full if os.path.isdir(full) else None

    def start(self, params: dict) -> Job:
        with self.lock:
            if self.current and not self.current.done:
                raise RuntimeError("une analyse est déjà en cours ; arrêtez-la ou attendez la fin")
            job = Job(params, self.runs_dir)
            self.current = job
            threading.Thread(target=job.run, daemon=True).start()
            return job

    def recompute(self, run_id: str, strategic_urls: list[str], labels: dict | None = None) -> Job:
        with self.lock:
            if self.current and not self.current.done:
                raise RuntimeError("une analyse est déjà en cours ; attendez la fin")
            d = self.run_dir(run_id)        # UnsafeRunPath si l'identifiant sort du dossier (403 côté serveur)
            if not d:
                raise RuntimeError("analyse inconnue")
            job = RecomputeJob(os.path.basename(d), os.path.realpath(self.runs_dir), strategic_urls, labels)
            self.current = job
            threading.Thread(target=job.run, daemon=True).start()
            return job

    def runs(self) -> list[dict]:
        return list_runs(self.runs_dir)


def inject_token(html: bytes, token: str) -> bytes:
    """Rapport servi sous /runs/ : ajoute après <head> une balise meta portant le jeton et un script qui le joint
    (en-tête X-Prgraph-Token) aux POST même origine vers /api/. Le fichier sur disque n'est pas modifié."""
    m = re.search(rb"<head[^>]*>", html, re.I)
    if not m:
        return html
    snippet = (
        '<meta name="prgraph-token" content="{t}">'
        '<script>(function(){{var t={tj};var f=window.fetch;if(!f)return;window.fetch=function(u,o){{'
        'var url=(u&&u.url)||String(u);o=o||{{}};var m=String(o.method||(u&&u.method)||"GET").toUpperCase();'
        'if(m==="POST"&&(url.indexOf("/api/")===0||url.indexOf(location.origin+"/api/")===0)){{'
        'var h=new Headers(o.headers||(u&&u.headers)||{{}});if(!h.has("{hn}"))h.set("{hn}",t);o=Object.assign({{}},o,{{headers:h}});}}'
        'return f.call(this,u,o);}};}})();</script>'
    ).format(t=token, tj=json.dumps(token), hn=TOKEN_HEADER).encode("utf-8")
    return html[:m.end()] + snippet + html[m.end():]


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        timeout = 60                      # une connexion qui n'envoie plus rien est fermée (corps annoncé jamais reçu)
        server_version = "prgraph"
        sys_version = ""

        def log_message(self, fmt, *args):  # silence
            pass

        # ── Réponses ──────────────────────────────────────────────────────────
        def _send(self, code: int, body: bytes, ctype: str, csp: str | None, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if csp:
                self.send_header("Content-Security-Policy", csp)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8", CSP_NONE)

        def _error(self, code: int, message: str) -> None:
            """Toute erreur part en JSON : jamais de page HTML générée par send_error, jamais de connexion fermée sans réponse."""
            self._json(code, {"error": message})

        def _launcher(self) -> None:
            path = os.path.join(VIEWER_DIR, "launcher.html")
            if not os.path.isfile(path):
                return self._error(404, "lanceur introuvable")
            nonce = secrets.token_urlsafe(16)
            with io.open(path, encoding="utf-8") as fh:
                html = fh.read().replace(TOKEN_PLACEHOLDER, state.token).replace(NONCE_PLACEHOLDER, nonce)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", CSP_LAUNCHER.format(nonce=nonce))

        def _run_file(self, path: str) -> None:
            if not os.path.isfile(path):
                return self._error(404, "fichier introuvable")
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json", "application/javascript"):
                ctype += "; charset=utf-8"
            with open(path, "rb") as fh:
                data = fh.read()
            csp = None
            if ctype.startswith("text/html"):
                data = inject_token(data, state.token)
                csp = CSP_RUNS_HTML_CDN if b'src="https://cdn.jsdelivr.net/' in data else CSP_RUNS_HTML
            self._send(200, data, ctype, csp)

        # ── Contrôles d'origine ───────────────────────────────────────────────
        def _allowed_hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"localhost:{port}", f"127.0.0.1:{port}"}

        def _host_ok(self) -> bool:
            """L'en-tête Host doit désigner ce serveur : barre un site tiers qui ferait pointer son nom sur 127.0.0.1."""
            host = (self.headers.get("Host") or "").strip().lower()
            return host in self._allowed_hosts()

        def _origin_ok(self) -> bool:
            """Sans Origin (requête directe, curl) : accepté ; avec Origin : seulement le lanceur ou le rapport servis ici."""
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            origin = origin.strip().lower()
            return origin in {f"http://{h}" for h in self._allowed_hosts()}

        # ── Méthodes ──────────────────────────────────────────────────────────
        def do_HEAD(self):
            # SimpleHTTPRequestHandler servait le dossier courant du processus sur HEAD : ici la méthode est refusée.
            self._send(405, b"", "application/json; charset=utf-8", CSP_NONE, {"Allow": "GET, POST"})

        def do_GET(self):
            if not self._host_ok():
                return self._error(403, "hôte non autorisé")
            path = unquote(urlsplit(self.path).path)
            if path in ("/", "/index.html"):
                return self._launcher()
            if path == "/favicon.ico":
                # Pas d'icône : 204 évite une erreur 404 en console à chaque ouverture.
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/api/status":
                job = state.current
                cur = None
                if job:
                    cur = {"id": job.id, "phase": job.phase, "pages": job.pages, "done": job.done, "error": job.error,
                           "log": job.log[-40:], "elapsed": round(time.time() - job.started), "url": job.params.get("url")}
                return self._json(200, {"current": cur, "runs": state.runs()})
            if path == "/api/business/capabilities":
                return self._json(200, business_capabilities())
            if path == "/api/gsc/sites":
                from . import connectors
                try:
                    creds = connectors.load_credentials(connectors.GSC_SCOPES)
                    return self._json(200, {"sites": connectors.list_gsc_sites(creds)})
                except Exception as exc:  # noqa: BLE001
                    return self._json(200, {"sites": [], "error": str(exc)})
            if path.startswith("/runs/"):
                try:
                    full = resolve_run_path(state.runs_dir, path[len("/runs/"):])
                except UnsafeRunPath:
                    return self._error(403, "chemin hors du dossier des analyses")
                return self._run_file(full)
            return self._error(404, "route inconnue")

        def do_POST(self):
            """Enveloppe : toute exception devient une réponse JSON (400 pour une entrée invalide, 500 sinon)."""
            try:
                self._post()
            except UnsafeRunPath:
                self._error(403, "identifiant d'analyse hors du dossier des analyses")
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                self._error(400, f"requête invalide : {exc}")
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._error(500, f"erreur interne : {exc}")

        def _read_body(self) -> bytes | None:
            """Corps d'un POST borné à MAX_BODY ; None si une réponse d'erreur a déjà été envoyée."""
            raw_len = self.headers.get("Content-Length")
            try:
                length = int(raw_len) if raw_len is not None else 0
            except ValueError:
                self._error(400, "Content-Length invalide")
                return None
            if length < 0:
                self._error(400, "Content-Length invalide")
                return None
            if length > MAX_BODY:
                # Vider ce qui a été envoyé (dans une limite) pour que le client lise la réponse plutôt qu'une connexion coupée.
                remaining = min(length, DRAIN_MAX)
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self.close_connection = True
                self._error(413, f"corps trop volumineux (maximum {MAX_BODY} octets)")
                return None
            return self.rfile.read(length) if length else b"{}"

        def _post(self):
            path = urlsplit(self.path).path
            if not self._host_ok():
                return self._error(403, "hôte non autorisé")
            if not self._origin_ok():
                return self._error(403, "origine non autorisée")
            if not secrets.compare_digest(self.headers.get(TOKEN_HEADER) or "", state.token):
                return self._error(403, "jeton anti-CSRF absent ou invalide : rechargez le lanceur")
            if self.headers.get_content_type() != "application/json":
                return self._error(415, "Content-Type: application/json requis")
            raw = self._read_body()
            if raw is None:
                return None
            try:
                params = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return self._error(400, "JSON invalide")
            if not isinstance(params, dict):
                return self._error(400, "corps JSON : objet attendu")
            if path == "/api/analyze":
                url = params.get("url") or ""
                if not isinstance(url, str) or not url.strip():
                    return self._json(400, {"error": "URL manquante"})
                url = url.strip()
                if not re.match(r"^https?://", url, re.I):
                    url = "https://" + url
                params["url"] = url
                try:
                    job = state.start(params)
                except RuntimeError as exc:
                    return self._json(409, {"error": str(exc)})
                return self._json(202, {"id": job.id})
            if path == "/api/stop":
                job = state.current
                if job and not job.done:
                    job.stop_event.set()
                    job.progress("arrêt demandé : le calcul se fera sur les pages déjà récupérées")
                    return self._json(200, {"ok": True})
                return self._json(200, {"ok": False})
            if path == "/api/recompute":
                run_id = params.get("id") or ""
                urls = params.get("strategic_urls") or []
                labels = params.get("labels") or None
                if not isinstance(run_id, str) or not run_id or not isinstance(urls, list):
                    return self._json(400, {"error": "id et strategic_urls requis"})
                if labels is not None and not isinstance(labels, dict):
                    return self._json(400, {"error": "labels : objet url -> classe attendu"})
                try:
                    job = state.recompute(run_id, [str(u) for u in urls], labels)
                except RuntimeError as exc:
                    return self._json(409, {"error": str(exc)})
                return self._json(202, {"id": job.id})
            if path == "/api/diff":
                from .diff import DiffInputError, diff_runs, write_diff_csv, write_diff_md
                before, after = params.get("before") or "", params.get("after") or ""
                if not isinstance(before, str) or not isinstance(after, str):
                    return self._json(400, {"error": "before et after : identifiants d'analyse attendus"})
                db, da = state.run_dir(before), state.run_dir(after)    # UnsafeRunPath -> 403 par l'enveloppe
                if not (db and da):
                    return self._json(400, {"error": "analyses introuvables"})
                protected = params.get("protected") or None
                if protected is not None and not isinstance(protected, list):
                    return self._json(400, {"error": "protected : liste attendue"})
                try:
                    d = diff_runs(db, da, protected)
                except DiffInputError as e:
                    return self._json(400, {"error": str(e)})
                write_diff_md(os.path.join(da, "diff.md"), d)
                write_diff_csv(os.path.join(da, "diff.csv"), d)
                return self._json(200, {"regressions": len(d["regressions"]), "lost_to_protected": len(d["lost_to_protected"]),
                                        "lost_edges": len(d["lost_edges"]), "new_edges": len(d["new_edges"]),
                                        "protected_missing": len(d["protected_missing"]), "incomplete": bool(d["edges_missing"]),
                                        "report": f"/runs/{os.path.basename(da)}/diff.md"})
            return self._error(404, "route inconnue")

    return Handler


def serve(port: int = 8765, runs_dir: str = "runs", open_browser: bool = False) -> None:
    os.makedirs(runs_dir, exist_ok=True)
    state = State(os.path.abspath(runs_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    print(f"pagerank-graph : http://localhost:{port}/  (analyses dans {state.runs_dir})", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://localhost:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
