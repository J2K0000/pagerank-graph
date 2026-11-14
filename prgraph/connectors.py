# -*- coding: utf-8 -*-
"""Connecteurs vers les sources de données business : Search Console (API Search Analytics) et
GA4 (API Data). Produisent des lignes `url + métriques` au format attendu par `loaders.read_business`.

Authentification : fichier désigné par la variable d'environnement GOOGLE_APPLICATION_CREDENTIALS
(ou paramètre explicite), jamais stocké dans le projet. Trois formes acceptées :
- compte de service (`"type": "service_account"`), à ajouter en lecteur sur la propriété GSC / GA4 ;
- identifiants OAuth « application installée » (`"installed"` ou `"web"`) : un navigateur s'ouvre une
  fois, le jeton est mis en cache dans le dossier utilisateur (hors projet) ;
- identifiants utilisateur autorisés (`"type": "authorized_user"`, format gcloud ADC).

Les bibliothèques Google sont importées paresseusement : sans elles, l'outil fonctionne avec un CSV
local et renvoie un message clair dès qu'un connecteur est appelé.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import date, timedelta
from importlib import import_module
from urllib.parse import urlsplit

from .clean import normalize_url

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
GSC_ROW_LIMIT = 25000       # maximum de l'API Search Analytics par requête
GA4_ROW_LIMIT = 100000      # maximum de l'API GA4 Data par requête
GA4_DIMENSIONS = ("landingPagePlusQueryString", "landingPage")
NON_ADDITIVE = ("ctr", "position")   # moyennes pondérées par les impressions lors d'une fusion


class MissingDependency(RuntimeError):
    """Bibliothèque optionnelle absente."""


class CredentialsError(RuntimeError):
    """Identifiants Google introuvables ou illisibles."""


def _require(module: str, package: str):
    try:
        return import_module(module)
    except ImportError as exc:
        raise MissingDependency(
            f"le module Python « {module} » est absent : installer la dépendance optionnelle avec "
            f"`pip install {package}` (voir requirements.txt, section connecteurs)."
        ) from exc


# ── Période ───────────────────────────────────────────────────────────────────

def period_from_days(days: int, lag: int = 3, today: date | None = None) -> tuple[str, str]:
    """Fenêtre de `days` jours se terminant `lag` jours avant aujourd'hui (les données Search Console
    et GA4 des deux ou trois derniers jours sont incomplètes). Retourne (start, end) en ISO."""
    if days < 1:
        raise ValueError("--days doit être au moins 1")
    today = today or date.today()
    end = today - timedelta(days=lag)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


# ── Identifiants ──────────────────────────────────────────────────────────────

def credentials_path(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or None


def _token_cache_path(source: str, scopes: list[str]) -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "pagerank-graph")
    os.makedirs(d, exist_ok=True)
    key = hashlib.sha1((os.path.abspath(source) + "|" + " ".join(sorted(scopes))).encode("utf-8")).hexdigest()[:16]
    return os.path.join(d, f"token-{key}.json")


def load_credentials(scopes: list[str], path: str | None = None):
    """Charge des identifiants Google depuis `path` ou GOOGLE_APPLICATION_CREDENTIALS."""
    p = credentials_path(path)
    if not p:
        raise CredentialsError(
            "aucun identifiant Google : définir la variable d'environnement GOOGLE_APPLICATION_CREDENTIALS "
            "(chemin d'un fichier JSON de compte de service ou d'identifiants OAuth, hors du projet) "
            "ou passer --credentials FICHIER."
        )
    if not os.path.isfile(p):
        raise CredentialsError(f"fichier d'identifiants introuvable : {p}")
    try:
        with io.open(p, encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CredentialsError(f"fichier d'identifiants illisible ({p}) : {exc}") from exc

    kind = info.get("type")
    if kind == "service_account":
        sa = _require("google.oauth2.service_account", "google-auth")
        return sa.Credentials.from_service_account_file(p, scopes=scopes)
    if kind == "authorized_user":
        oc = _require("google.oauth2.credentials", "google-auth")
        return oc.Credentials.from_authorized_user_file(p, scopes=scopes)
    if "installed" in info or "web" in info:
        return _oauth_installed_app(p, scopes)
    raise CredentialsError(
        f"format d'identifiants non reconnu dans {p} : attendu un compte de service, des identifiants "
        "OAuth « installed » ou un fichier authorized_user."
    )


def _oauth_installed_app(client_secrets: str, scopes: list[str]):
    oc = _require("google.oauth2.credentials", "google-auth")
    flow_mod = _require("google_auth_oauthlib.flow", "google-auth-oauthlib")
    cache = _token_cache_path(client_secrets, scopes)
    creds = None
    if os.path.isfile(cache):
        try:
            creds = oc.Credentials.from_authorized_user_file(cache, scopes=scopes)
        except (OSError, ValueError):
            creds = None
    if creds and creds.expired and creds.refresh_token:
        req_mod = _require("google.auth.transport.requests", "google-auth")
        try:
            creds.refresh(req_mod.Request())
        except Exception:  # noqa: BLE001
            creds = None
    if not creds or not creds.valid:
        flow = flow_mod.InstalledAppFlow.from_client_secrets_file(client_secrets, scopes=scopes)
        creds = flow.run_local_server(port=0, open_browser=True)
        with io.open(cache, "w", encoding="utf-8") as fh:
            fh.write(creds.to_json())
    return creds


# ── Search Console ────────────────────────────────────────────────────────────

def gsc_service(credentials):
    discovery = _require("googleapiclient.discovery", "google-api-python-client")
    return discovery.build("searchconsole", "v1", credentials=credentials, cache_discovery=False)


def list_gsc_sites(credentials=None, *, service=None) -> list[dict]:
    """Propriétés accessibles (sites.list) : [{site_url, permission}]."""
    svc = service or gsc_service(credentials)
    resp = svc.sites().list().execute() or {}
    out = [{"site_url": e.get("siteUrl", ""), "permission": e.get("permissionLevel", "")}
           for e in resp.get("siteEntry", [])]
    out.sort(key=lambda s: (not s["site_url"].startswith("sc-domain:"), s["site_url"]))
    return out


def fetch_gsc_pages(site_url: str, start: str, end: str, credentials=None, *, service=None,
                    row_limit: int = GSC_ROW_LIMIT, search_type: str = "web") -> list[dict]:
    """Toutes les pages de la propriété sur la période : url, clicks, impressions, ctr, position.
    Pagination par `row_limit` lignes (25 000, maximum de l'API) avec startRow."""
    svc = service or gsc_service(credentials)
    rows: list[dict] = []
    start_row = 0
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": ["page"], "type": search_type,
                "rowLimit": row_limit, "startRow": start_row, "dataState": "final"}
        resp = svc.searchanalytics().query(siteUrl=site_url, body=body).execute() or {}
        chunk = resp.get("rows") or []
        for r in chunk:
            keys = r.get("keys") or [""]
            rows.append({"url": keys[0], "clicks": float(r.get("clicks", 0) or 0),
                         "impressions": float(r.get("impressions", 0) or 0),
                         "ctr": float(r.get("ctr", 0) or 0), "position": float(r.get("position", 0) or 0)})
        if len(chunk) < row_limit:
            break
        start_row += row_limit
    return rows


def default_gsc_site(base_url: str) -> str:
    """Propriété Search Console par défaut pour une URL : `sc-domain:` du domaine sans www."""
    host = urlsplit(base_url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return f"sc-domain:{host}" if host else ""


# ── GA4 ───────────────────────────────────────────────────────────────────────

def ga4_client(credentials):
    data = _require("google.analytics.data_v1beta", "google-analytics-data")
    return data.BetaAnalyticsDataClient(credentials=credentials)


def _site_origin(base_url: str) -> str:
    p = urlsplit(base_url.strip())
    if not p.scheme or not p.netloc:
        raise ValueError(f"--base-url doit être une URL absolue (https://exemple.fr), reçu : {base_url!r}")
    return f"{p.scheme}://{p.netloc}"


def prefix_path(path: str, origin: str) -> str:
    """Chemin GA4 → URL complète. Un chemin déjà absolu est conservé."""
    s = path.strip()
    if s.lower().startswith(("http://", "https://")):
        return s
    if not s.startswith("/"):
        s = "/" + s
    return origin + s


def fetch_ga4_landing_pages(property_id: str, start: str, end: str, credentials=None, *, base_url: str,
                            client=None, dimension: str = "landingPagePlusQueryString",
                            row_limit: int = GA4_ROW_LIMIT) -> list[dict]:
    """Pages d'atterrissage GA4 sur la période : url (chemin préfixé par l'origine du site), sessions,
    conversions (keyEvents), revenue (totalRevenue). Pagination par offset."""
    if not base_url:
        raise ValueError("--base-url est obligatoire pour GA4 : les chemins doivent redevenir des URL complètes")
    origin = _site_origin(base_url)
    if dimension not in GA4_DIMENSIONS:
        raise ValueError(f"dimension GA4 inconnue : {dimension} (choix : {', '.join(GA4_DIMENSIONS)})")
    prop = str(property_id).strip()
    if not prop.startswith("properties/"):
        prop = "properties/" + prop
    cli = client or ga4_client(credentials)
    rows: list[dict] = []
    offset = 0
    while True:
        request = {"property": prop, "dimensions": [{"name": dimension}],
                   "metrics": [{"name": "sessions"}, {"name": "keyEvents"}, {"name": "totalRevenue"}],
                   "date_ranges": [{"start_date": start, "end_date": end}],
                   "limit": row_limit, "offset": offset, "keep_empty_rows": False}
        resp = cli.run_report(request=request)
        chunk = list(getattr(resp, "rows", []) or [])
        for r in chunk:
            path = r.dimension_values[0].value if r.dimension_values else ""
            if not path or path.strip().lower() in ("(not set)", "(other)"):
                continue
            mv = [float(m.value or 0) for m in r.metric_values]
            mv += [0.0] * (3 - len(mv))
            rows.append({"url": prefix_path(path, origin), "sessions": mv[0], "conversions": mv[1], "revenue": mv[2]})
        offset += len(chunk)
        total = int(getattr(resp, "row_count", 0) or 0)
        if not chunk or len(chunk) < row_limit or (total and offset >= total):
            break
    return rows


# ── Table, fusion, écriture ───────────────────────────────────────────────────

def rows_to_table(rows: list[dict]) -> dict[str, dict[str, float]]:
    """Liste de lignes {url, métriques} → {url: {métrique: valeur}} (doublons additionnés)."""
    table: dict[str, dict[str, float]] = {}
    for r in rows:
        url = str(r.get("url", "")).strip()
        if not url:
            continue
        metrics = {k: float(v or 0) for k, v in r.items() if k != "url"}
        if url in table:
            table[url] = _combine(table[url], metrics)
        else:
            table[url] = metrics
    return table


def _combine(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    out = {k: a.get(k, 0.0) + b.get(k, 0.0) for k in set(a) | set(b) if k not in NON_ADDITIVE}
    for k in NON_ADDITIVE:
        if k in a or k in b:
            wa, wb = a.get("impressions", 0.0), b.get("impressions", 0.0)
            va, vb = a.get(k), b.get(k)
            if va is None:
                out[k] = vb
            elif vb is None:
                out[k] = va
            elif wa + wb > 0:
                out[k] = (va * wa + vb * wb) / (wa + wb)
            else:
                out[k] = (va + vb) / 2
    return out


def merge_business(*tables: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Fusion par URL normalisée (casse de l'hôte, port par défaut, fragment). Les métriques
    additives sont sommées, ctr et position moyennés par les impressions ; les colonnes absentes
    d'une source sont complétées à zéro pour que le CSV soit rectangulaire."""
    merged: dict[str, dict[str, float]] = {}
    seen: dict[str, str] = {}
    all_cols: list[str] = []
    for t in tables:
        for url, metrics in t.items():
            key = normalize_url(url)
            for c in metrics:
                if c not in all_cols:
                    all_cols.append(c)
            if key in seen:
                raw = seen[key]
                merged[raw] = _combine(merged[raw], metrics)
            else:
                seen[key] = url
                merged[url] = dict(metrics)
    for m in merged.values():
        for c in all_cols:
            m.setdefault(c, 0.0)
    return merged


def business_columns(table: dict[str, dict[str, float]]) -> list[str]:
    preferred = ["clicks", "impressions", "ctr", "position", "sessions", "conversions", "revenue"]
    present: set[str] = set()
    for m in table.values():
        present.update(m)
    cols = [c for c in preferred if c in present] + sorted(c for c in present if c not in preferred)
    return cols


def write_business_csv(path: str, table: dict[str, dict[str, float]], columns: list[str] | None = None,
                       period: str | None = None) -> list[str]:
    """Écrit `url` + colonnes de métriques. Retourne l'en-tête écrit. `period` (« 2026-08-09 au 2026-11-08 »)
    est écrite en ligne de commentaire `# période : ...` en tête : la lecture l'ignore comme donnée et la
    reprend pour l'afficher avec la valeur (rapport, classeur)."""
    cols = columns or business_columns(table)
    header = ["url"] + cols
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        if period:
            fh.write("# période : " + period + "\n")
        w = csv.writer(fh)
        w.writerow(header)
        for url, m in table.items():
            w.writerow([url] + [_fmt(m.get(c, 0.0)) for c in cols])
    return header


def _fmt(v: float) -> str:
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.6g}"
