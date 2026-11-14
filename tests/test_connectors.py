# -*- coding: utf-8 -*-
"""Connecteurs Search Console / GA4 (réponses simulées, aucun appel réseau), fusion, préfixage,
CLI fetch-gsc / fetch-ga4, préparation business côté serveur, lecture demande / valeur."""
import io
import json
import os
from datetime import date

import pytest

from prgraph import cli, connectors
from prgraph.connectors import (MissingDependency, fetch_ga4_landing_pages, fetch_gsc_pages, list_gsc_sites,
                                merge_business, period_from_days, rows_to_table, write_business_csv)
from prgraph.loaders import read_business


# ── Doubles de test ───────────────────────────────────────────────────────────

class FakeGscService:
    """Imite googleapiclient : service.searchanalytics().query(siteUrl=, body=).execute()."""

    def __init__(self, total_rows: int):
        self.total = total_rows
        self.calls: list[dict] = []

    def searchanalytics(self):
        return self

    def sites(self):
        return self

    def list(self):
        class _Req:
            def execute(self):
                return {"siteEntry": [{"siteUrl": "sc-domain:exemple.fr", "permissionLevel": "siteOwner"},
                                      {"siteUrl": "https://www.exemple.fr/", "permissionLevel": "siteFullUser"}]}
        return _Req()

    def query(self, siteUrl, body):
        self.calls.append({"siteUrl": siteUrl, "body": body})
        start, limit = body.get("startRow", 0), body["rowLimit"]
        rows = []
        for k in range(start, min(start + limit, self.total)):
            rows.append({"keys": [f"https://exemple.fr/p/{k}/"], "clicks": k, "impressions": 10 * k + 1,
                         "ctr": 0.1, "position": 5.5})
        payload = {"rows": rows} if rows else {}

        class _Req:
            def execute(self):
                return payload
        return _Req()


class _Val:
    def __init__(self, v):
        self.value = v


class _Row:
    def __init__(self, dims, mets):
        self.dimension_values = [_Val(d) for d in dims]
        self.metric_values = [_Val(m) for m in mets]


class _Resp:
    def __init__(self, rows, row_count):
        self.rows = rows
        self.row_count = row_count


class FakeGa4Client:
    """Imite BetaAnalyticsDataClient.run_report(request=dict)."""

    def __init__(self, paths):
        self.paths = paths
        self.requests: list[dict] = []

    def run_report(self, request):
        self.requests.append(request)
        offset, limit = request.get("offset", 0), request["limit"]
        chunk = self.paths[offset:offset + limit]
        rows = [_Row([p], [str(10 + i), str(i % 3), f"{i * 1.5:.2f}"]) for i, p in enumerate(chunk, start=offset)]
        return _Resp(rows, len(self.paths))


# ── Période ───────────────────────────────────────────────────────────────────

def test_period_from_days_applies_lag():
    start, end = period_from_days(90, lag=3, today=date(2026, 10, 18))
    assert end == "2026-10-15" and start == "2026-07-18"
    with pytest.raises(ValueError):
        period_from_days(0)


# ── Search Console ────────────────────────────────────────────────────────────

def test_fetch_gsc_pages_paginates_by_25000():
    svc = FakeGscService(25003)
    rows = fetch_gsc_pages("sc-domain:exemple.fr", "2026-07-18", "2026-10-15", service=svc)
    assert len(rows) == 25003
    assert [c["body"]["startRow"] for c in svc.calls] == [0, 25000]
    body = svc.calls[0]["body"]
    assert body["dimensions"] == ["page"] and body["type"] == "web" and body["rowLimit"] == 25000
    assert body["startDate"] == "2026-07-18" and body["endDate"] == "2026-10-15"
    assert svc.calls[0]["siteUrl"] == "sc-domain:exemple.fr"
    r = rows[7]
    assert r == {"url": "https://exemple.fr/p/7/", "clicks": 7.0, "impressions": 71.0, "ctr": 0.1, "position": 5.5}


def test_fetch_gsc_pages_stops_on_short_page():
    svc = FakeGscService(12)
    rows = fetch_gsc_pages("sc-domain:exemple.fr", "2026-07-18", "2026-10-15", service=svc)
    assert len(rows) == 12 and len(svc.calls) == 1


def test_list_gsc_sites():
    sites = list_gsc_sites(service=FakeGscService(0))
    assert [s["site_url"] for s in sites] == ["sc-domain:exemple.fr", "https://www.exemple.fr/"]
    assert sites[0]["permission"] == "siteOwner"


# ── GA4 ───────────────────────────────────────────────────────────────────────

def test_fetch_ga4_prefixes_paths_and_skips_not_set():
    client = FakeGa4Client(["/", "/a/", "/b?x=1", "(not set)", "sans-slash"])
    rows = fetch_ga4_landing_pages("123456", "2026-07-18", "2026-10-15", base_url="https://exemple.fr/", client=client)
    assert [r["url"] for r in rows] == ["https://exemple.fr/", "https://exemple.fr/a/", "https://exemple.fr/b?x=1",
                                        "https://exemple.fr/sans-slash"]
    assert rows[1] == {"url": "https://exemple.fr/a/", "sessions": 11.0, "conversions": 1.0, "revenue": 1.5}
    req = client.requests[0]
    assert req["property"] == "properties/123456"
    assert [d["name"] for d in req["dimensions"]] == ["landingPagePlusQueryString"]
    assert [m["name"] for m in req["metrics"]] == ["sessions", "keyEvents", "totalRevenue"]
    assert req["date_ranges"] == [{"start_date": "2026-07-18", "end_date": "2026-10-15"}]


def test_fetch_ga4_paginates_with_offset():
    paths = [f"/p/{k}/" for k in range(7)]
    client = FakeGa4Client(paths)
    rows = fetch_ga4_landing_pages("properties/42", "2026-01-01", "2026-01-31", base_url="https://e.fr",
                                   client=client, row_limit=3, dimension="landingPage")
    assert len(rows) == 7
    assert [r.get("offset", 0) for r in client.requests] == [0, 3, 6]
    assert client.requests[0]["property"] == "properties/42"
    assert client.requests[0]["dimensions"] == [{"name": "landingPage"}]


def test_fetch_ga4_requires_base_url():
    with pytest.raises(ValueError):
        fetch_ga4_landing_pages("1", "2026-01-01", "2026-01-31", base_url="", client=FakeGa4Client([]))


# ── Fusion et écriture ────────────────────────────────────────────────────────

def test_merge_business_joins_by_normalised_url():
    gsc = rows_to_table([{"url": "https://e.fr/a/", "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 4.0},
                         {"url": "https://E.fr/b/", "clicks": 1, "impressions": 50, "ctr": 0.02, "position": 12.0}])
    ga4 = rows_to_table([{"url": "https://e.fr/a/", "sessions": 30, "conversions": 2, "revenue": 99.5},
                         {"url": "https://e.fr/c/", "sessions": 3, "conversions": 0, "revenue": 0.0}])
    merged = merge_business(gsc, ga4)
    assert set(merged) == {"https://e.fr/a/", "https://E.fr/b/", "https://e.fr/c/"}
    a = merged["https://e.fr/a/"]
    assert a["clicks"] == 10 and a["conversions"] == 2 and a["revenue"] == 99.5 and a["sessions"] == 30
    assert merged["https://e.fr/c/"]["clicks"] == 0.0           # colonnes complétées à zéro
    assert merged["https://E.fr/b/"]["revenue"] == 0.0


def test_merge_business_averages_ctr_and_position_weighted_by_impressions():
    t1 = rows_to_table([{"url": "https://e.fr/a", "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 2.0}])
    t2 = rows_to_table([{"url": "https://e.fr/a/", "clicks": 30, "impressions": 300, "ctr": 0.1, "position": 6.0}])
    t3 = rows_to_table([{"url": "https://e.fr/a", "clicks": 30, "impressions": 300, "ctr": 0.1, "position": 6.0}])
    m = merge_business(t1, t3)
    a = m["https://e.fr/a"]
    assert a["clicks"] == 40 and a["impressions"] == 400
    assert a["position"] == pytest.approx(5.0) and a["ctr"] == pytest.approx(0.1)
    assert len(merge_business(t1, t2)) == 2       # /a et /a/ restent deux URL distinctes


def test_write_business_csv_roundtrip(tmp_path):
    table = rows_to_table([{"url": "https://e.fr/a/", "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 4.0},
                           {"url": "https://e.fr/b/", "clicks": 0, "impressions": 5, "ctr": 0.0, "position": 30.0}])
    p = tmp_path / "business.csv"
    cols = write_business_csv(str(p), table)
    assert cols == ["url", "clicks", "impressions", "ctr", "position"]
    data, metrics = read_business(str(p))
    assert metrics == ["clicks", "impressions"]
    assert data["https://e.fr/a/"]["clicks"] == 10.0


# ── Loader : chemins relatifs, export GA4 « Pages et écrans » ────────────────

def test_read_business_prefixes_relative_paths_and_skips_ga4_preamble(tmp_path):
    p = tmp_path / "ga4.csv"
    io.open(p, "w", encoding="utf-8").write(
        "# ----------------------------------------\n# Toutes les données du compte\n# Pages et écrans\n"
        "# 20260701-20260930\n# ----------------------------------------\n\n"
        "Chemin de la page et classe d'écran,Vues,Utilisateurs actifs,Événements clés,Revenu total\n"
        "/,1 200,800,12,0\n/produits/a/,300,210,4,199.9\n(not set),5,5,0,0\n")
    data, cols = read_business(str(p), base_url="https://exemple.fr")
    assert set(data) == {"https://exemple.fr/", "https://exemple.fr/produits/a/"}
    assert "conversions" in cols and "revenue" in cols and "sessions" in cols
    assert data["https://exemple.fr/produits/a/"]["conversions"] == 4.0
    assert data["https://exemple.fr/produits/a/"]["revenue"] == pytest.approx(199.9)
    # sans base_url les chemins restent tels quels (ils ne seront pas joints)
    data2, _ = read_business(str(p))
    assert "/produits/a/" in data2


def test_compute_accepts_business_base_url(tmp_path):
    out = tmp_path / "demo"
    cli.main(["demo", "--out", str(out), "--products", "200"])
    # business.csv réécrit avec des chemins relatifs
    rows = list(io.open(out / "business.csv", encoding="utf-8"))
    rel = out / "business_rel.csv"
    with io.open(rel, "w", encoding="utf-8") as fh:
        fh.write(rows[0])
        for line in rows[1:]:
            url, rest = line.split(",", 1)
            fh.write(url.replace("https://demo-outdoor.example", "") + "," + rest)
    o = out / "out"
    rc = cli.main(["compute", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"),
                   "--business", str(rel), "--business-base-url", "https://demo-outdoor.example",
                   "--max-nodes", "100", "--out", str(o)])
    assert rc == 0
    data = json.load(io.open(o / "graph.json", encoding="utf-8"))
    assert data["meta"]["value_col"] == "revenue"
    assert sum(1 for n in data["nodes"] if n[9] > 0) > 10


# ── Dépendances optionnelles et identifiants ──────────────────────────────────

def test_missing_dependency_message():
    with pytest.raises(MissingDependency) as exc:
        connectors._require("module_qui_n_existe_pas_xyz", "paquet-fictif")
    assert "pip install paquet-fictif" in str(exc.value)


def test_load_credentials_without_env_is_explicit(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(connectors.CredentialsError) as exc:
        connectors.load_credentials(connectors.GSC_SCOPES)
    assert "GOOGLE_APPLICATION_CREDENTIALS" in str(exc.value)


# ── CLI fetch-gsc / fetch-ga4 / --merge ───────────────────────────────────────

def test_cli_fetch_gsc_and_fetch_ga4_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(connectors, "load_credentials", lambda scopes, path=None: None)
    monkeypatch.setattr(connectors, "fetch_gsc_pages",
                        lambda site, start, end, credentials=None, **kw: fetch_gsc_pages(site, start, end, service=FakeGscService(5)))
    monkeypatch.setattr(connectors, "fetch_ga4_landing_pages",
                        lambda prop, start, end, credentials=None, **kw: fetch_ga4_landing_pages(
                            prop, start, end, base_url=kw["base_url"], client=FakeGa4Client(["/p/1/", "/p/2/", "/z/"]),
                            dimension=kw.get("dimension", "landingPagePlusQueryString")))
    gsc_csv = tmp_path / "gsc.csv"
    rc = cli.main(["fetch-gsc", "--site", "sc-domain:exemple.fr", "--days", "30", "--out", str(gsc_csv)])
    assert rc == 0
    data, cols = read_business(str(gsc_csv))
    assert len(data) == 5 and cols == ["clicks", "impressions"]

    merged_csv = tmp_path / "business.csv"
    rc = cli.main(["fetch-ga4", "--property", "123456", "--base-url", "https://exemple.fr", "--days", "30",
                   "--out", str(merged_csv), "--merge", str(gsc_csv)])
    assert rc == 0
    lines = io.open(merged_csv, encoding="utf-8-sig").read().splitlines()
    # 0.5.0 : la période de l'export est écrite en commentaire de tête et reprise dans le rapport
    from prgraph.loaders import read_business_period
    assert lines[0].startswith("# période : ") and read_business_period(str(merged_csv)) == lines[0].split(" : ", 1)[1]
    header = lines[1].strip().split(",")
    assert header[0] == "url" and {"clicks", "impressions", "sessions", "conversions", "revenue"} <= set(header)
    data, cols = read_business(str(merged_csv))
    assert len(data) == 6                                    # 5 URL GSC + /z/ ; /p/1/ et /p/2/ fusionnées
    assert data["https://exemple.fr/p/1/"]["clicks"] == 1.0 and data["https://exemple.fr/p/2/"]["conversions"] == 1.0
    assert data["https://exemple.fr/p/1/"]["sessions"] == 10.0
    assert data["https://exemple.fr/z/"]["clicks"] == 0.0


# ── Serveur : préparation du CSV business et couverture ───────────────────────

def test_server_prepare_business_gsc_and_coverage(tmp_path, monkeypatch):
    from prgraph import server
    monkeypatch.setattr(connectors, "load_credentials", lambda scopes, path=None: None)
    monkeypatch.setattr(connectors, "fetch_gsc_pages",
                        lambda site, start, end, credentials=None, **kw: fetch_gsc_pages(site, start, end, service=FakeGscService(4)))
    logs = []
    run_dir = tmp_path / "run"
    os.makedirs(run_dir)
    path = server.prepare_business({"business_source": "gsc", "gsc_site": "", "business_days": 28},
                                   str(run_dir), "https://www.exemple.fr/", logs.append)
    assert path == os.path.join(str(run_dir), "business.csv") and os.path.exists(path)
    assert any("sc-domain:exemple.fr" in m for m in logs)      # propriété déduite de l'URL
    assert any("4 URL" in m for m in logs)
    pages_csv = run_dir / "pages.csv"
    io.open(pages_csv, "w", encoding="utf-8-sig").write(
        "url,segment,score,value\nhttps://exemple.fr/p/1/,p,50,1\nhttps://exemple.fr/p/2/,p,40,2\n"
        "https://exemple.fr/p/0/,p,10,0\nhttps://exemple.fr/autre/,a,5,0\n")
    cov = server.business_coverage(str(pages_csv), path)
    assert cov["graph_pages"] == 4 and cov["joined"] == 3 and cov["with_value"] == 2
    assert cov["coverage"] == pytest.approx(0.5)     # part des pages du graphe avec une valeur
    # source csv : le chemin est renvoyé tel quel ; aucune : None
    assert server.prepare_business({"business_source": "csv", "business_path": "x.csv"}, str(run_dir), "https://e.fr", logs.append) == "x.csv"
    assert server.prepare_business({"business_source": "none", "business_path": "x.csv"}, str(run_dir), "https://e.fr", logs.append) is None


# ── Lecture : valeur retenue et demande (impressions) ─────────────────────────

def test_findings_and_graph_expose_value_column_and_demand(tmp_path):
    out = tmp_path / "demo"
    cli.main(["demo", "--out", str(out), "--products", "200"])
    # business GSC seulement : clics + impressions, impressions fortes sur des pages peu cliquées
    src, _ = read_business(str(out / "business.csv"))
    gsc = out / "gsc.csv"
    with io.open(gsc, "w", encoding="utf-8", newline="") as fh:
        fh.write("url,clicks,impressions\n")
        for k, (u, m) in enumerate(src.items()):
            clicks = m.get("clicks", 0.0)
            imps = clicks * 20 if k % 4 else 50000          # une page sur quatre : beaucoup d'impressions
            fh.write(f"{u},{clicks:.0f},{imps:.0f}\n")
    o = out / "out"
    rc = cli.main(["compute", "--edges", str(out / "edges.csv"), "--urls", str(out / "urls.csv"),
                   "--business", str(gsc), "--segments", str(out / "segments.tsv"), "--out", str(o)])
    assert rc == 0
    md = io.open(o / "findings.md", encoding="utf-8").read()
    assert "Colonne de valeur retenue : clicks" in md
    assert "impressions = demande" in md and "clics = valeur réalisée" in md
    assert "Demande non servie" in md
    assert "\u2014" not in md
    data = json.load(io.open(o / "graph.json", encoding="utf-8"))
    assert data["meta"]["value_col"] == "clicks" and data["meta"]["demand_col"] == "impressions"
    assert data["meta"]["business_cols"] == ["clicks", "impressions"]
    assert "demand" in data["meta"]["node_fields"]
    di = data["meta"]["node_fields"].index("demand")
    assert any(n[di] >= 50000 for n in data["nodes"])
    assert isinstance(data["top"]["demand_starving"], list) and data["top"]["demand_starving"]
    html = io.open(o / "report.html", encoding="utf-8").read()
    assert "Demande non servie" in html
