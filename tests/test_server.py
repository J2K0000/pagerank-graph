# -*- coding: utf-8 -*-
"""Serveur local `prgraph serve` : traversée de chemin, HEAD, CSRF (jeton, Origin, Host, Content-Type),
bornes et erreurs des POST, en-têtes de sécurité, lanceur. Aucun appel réseau hors de 127.0.0.1,
aucune écriture hors du dossier temporaire de pytest."""
import http.client
import json
import os
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

from prgraph import server as srv
from prgraph.server import MAX_BODY, TOKEN_HEADER, State, UnsafeRunPath, inject_token, make_handler, resolve_run_path

RUN_ID = "exemple.fr-20260101-000000"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """Serveur démarré sur un port libre, dans un thread, avec une analyse factice dans un dossier temporaire."""
    root = tmp_path_factory.mktemp("srv")
    runs = root / "runs"
    run = runs / RUN_ID
    run.mkdir(parents=True)
    (run / "pages.csv").write_text("url,score\nhttps://exemple.fr/,100\n", encoding="utf-8")
    (run / "report.html").write_text("<!doctype html><html><head><title>r</title></head><body>rapport</body></html>", encoding="utf-8")
    (run / "job.json").write_text(json.dumps({"id": RUN_ID, "params": {"url": "https://exemple.fr/"}, "crawl": {"pages": 1, "edges": 0}}),
                                  encoding="utf-8")
    # Fichier hors du dossier des analyses : ne doit jamais être servi.
    (root / "secret.txt").write_text("hors des analyses", encoding="utf-8")
    state = State(str(runs))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield {"port": port, "state": state, "root": root, "runs": runs}
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def call(site, method, path, body=None, headers=None, host=None):
    """Requête brute : le chemin est envoyé tel quel (pas de normalisation côté client)."""
    conn = http.client.HTTPConnection("127.0.0.1", site["port"], timeout=10)
    hdrs = dict(headers or {})
    if host is not None:
        hdrs["Host"] = host
    try:
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, dict(resp.getheaders()), data
    finally:
        conn.close()


def post(site, path, body=b"{}", token=True, origin=None, ctype="application/json", extra=None, host=None):
    headers = {"Content-Type": ctype}
    if token:
        headers[TOKEN_HEADER] = site["state"].token
    if origin is not None:
        headers["Origin"] = origin
    headers.update(extra or {})
    return call(site, "POST", path, body=body, headers=headers, host=host)


def as_json(data):
    return json.loads(data.decode("utf-8"))


# ── Résolution des chemins ─────────────────────────────────────────────────────

def test_resolve_run_path_accepte_un_enfant(site):
    full = resolve_run_path(str(site["runs"]), RUN_ID + "/pages.csv")
    assert os.path.isfile(full)


@pytest.mark.parametrize("rel", ["", "../secret.txt", "..\\secret.txt", "C:..\\..\\Windows\\win.ini", "C:\\Windows\\win.ini",
                                 "/etc/passwd", "\\\\serveur\\partage\\x", RUN_ID + "/../../secret.txt", "a\0b", "."])
def test_resolve_run_path_refuse_les_sorties(site, rel):
    with pytest.raises(UnsafeRunPath):
        resolve_run_path(str(site["runs"]), rel)


def test_run_dir_exige_un_enfant_direct(site):
    state = site["state"]
    assert state.run_dir(RUN_ID) and os.path.isdir(state.run_dir(RUN_ID))
    assert state.run_dir("inexistante-20260101-000000") is None
    with pytest.raises(UnsafeRunPath):
        state.run_dir(RUN_ID + "/sous-dossier")
    with pytest.raises(UnsafeRunPath):
        state.run_dir("..")


# ── GET /runs/ ─────────────────────────────────────────────────────────────────

def test_lecture_legitime_d_un_fichier_de_run(site):
    status, headers, data = call(site, "GET", f"/runs/{RUN_ID}/pages.csv")
    assert status == 200
    assert data.startswith(b"url,score")
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("Content-Type", "").startswith("text/csv")


@pytest.mark.parametrize("path", [
    "/runs/..%2Fsecret.txt",                       # ../ encodé
    "/runs/..%5Csecret.txt",                       # ..\ encodé
    "/runs/C:..%5C..%5C..%5CWindows%5Cwin.ini",    # chemin « relatif au lecteur » Windows (faille B1 de l'audit)
    "/runs/C:%5CWindows%5Cwin.ini",                # chemin absolu Windows
    "/runs//etc/passwd",                           # chemin absolu POSIX
    f"/runs/{RUN_ID}/..%2F..%2Fsecret.txt",        # remontée depuis une analyse réelle
])
def test_traversee_refusee(site, path):
    status, headers, data = call(site, "GET", path)
    assert status == 403, (path, status, data[:200])
    assert "error" in as_json(data)
    assert b"hors des analyses" not in data


def test_fichier_inexistant_sous_runs(site):
    status, _, data = call(site, "GET", f"/runs/{RUN_ID}/absent.csv")
    assert status == 404 and "error" in as_json(data)


def test_rapport_html_recoit_le_jeton_et_une_csp(site):
    status, headers, data = call(site, "GET", f"/runs/{RUN_ID}/report.html")
    assert status == 200
    html = data.decode("utf-8")
    assert site["state"].token in html
    assert html.count("<head>") == 1 and TOKEN_HEADER in html
    csp = headers.get("Content-Security-Policy", "")
    # 0.5.0 : rapport aux bibliothèques embarquées, aucune origine tierce ; le CDN n'est autorisé que pour un
    # rapport qui le charge (--cdn ou version antérieure).
    assert "cdn.jsdelivr.net" not in csp and "googleapis" not in csp and "gstatic" not in csp
    assert "script-src 'self' 'unsafe-inline';" in csp
    assert "frame-ancestors 'none'" in csp
    # Le fichier sur disque n'a pas été modifié.
    assert site["state"].token not in (site["runs"] / RUN_ID / "report.html").read_text(encoding="utf-8")


def test_inject_token_sans_head_laisse_le_contenu_intact():
    assert inject_token(b"texte sans head", "abc") == b"texte sans head"


# ── HEAD, Host ─────────────────────────────────────────────────────────────────

def test_head_refuse(site):
    status, headers, data = call(site, "HEAD", "/")
    assert status == 405
    assert headers.get("Allow") == "GET, POST"
    assert data == b""
    status, _, _ = call(site, "HEAD", f"/runs/{RUN_ID}/pages.csv")
    assert status == 405


def test_host_tiers_refuse(site):
    status, _, data = call(site, "GET", "/api/status", host="evil.example:80")
    assert status == 403 and "error" in as_json(data)
    status, _, _ = post(site, "/api/stop", host=f"attacker.localhost.example:{site['port']}")
    assert status == 403


def test_host_localhost_accepte(site):
    status, _, _ = call(site, "GET", "/api/status", host=f"localhost:{site['port']}")
    assert status == 200


# ── POST : CSRF, Content-Type, bornes, erreurs ─────────────────────────────────

def test_post_sans_jeton_refuse(site):
    status, _, data = post(site, "/api/stop", token=False)
    assert status == 403 and "jeton" in as_json(data)["error"]


def test_post_jeton_faux_refuse(site):
    status, _, _ = post(site, "/api/stop", token=False, extra={TOKEN_HEADER: "x" * 43})
    assert status == 403


def test_post_origine_tierce_refusee(site):
    status, _, data = post(site, "/api/stop", origin="https://site-malveillant.example")
    assert status == 403 and "origine" in as_json(data)["error"]
    status, _, _ = post(site, "/api/stop", origin="null")
    assert status == 403


def test_post_origine_locale_acceptee(site):
    status, _, data = post(site, "/api/stop", origin=f"http://localhost:{site['port']}")
    assert status == 200 and as_json(data) == {"ok": False}
    status, _, _ = post(site, "/api/stop", origin=f"http://127.0.0.1:{site['port']}")
    assert status == 200


def test_post_text_plain_refuse(site):
    status, _, data = post(site, "/api/stop", ctype="text/plain")
    assert status == 415 and "application/json" in as_json(data)["error"]


def test_post_json_invalide_400_en_json(site):
    status, headers, data = post(site, "/api/analyze", body=b"{pas du json")
    assert status == 400
    assert headers.get("Content-Type", "").startswith("application/json")
    assert as_json(data)["error"] == "JSON invalide"


def test_post_json_non_objet_400(site):
    status, _, data = post(site, "/api/analyze", body=b"[1,2,3]")
    assert status == 400 and "objet" in as_json(data)["error"]


def test_post_types_inattendus_400_sans_fermeture(site):
    # url entier : l'ancien code levait AttributeError et fermait la connexion sans réponse.
    status, _, data = post(site, "/api/analyze", body=json.dumps({"url": 12}).encode())
    assert status == 400 and "error" in as_json(data)
    status, _, data = post(site, "/api/recompute", body=json.dumps({"id": RUN_ID, "strategic_urls": "pas une liste"}).encode())
    assert status == 400
    status, _, data = post(site, "/api/recompute", body=json.dumps({"id": RUN_ID, "strategic_urls": [], "labels": [1]}).encode())
    assert status == 400 and "labels" in as_json(data)["error"]
    status, _, data = post(site, "/api/diff", body=json.dumps({"before": 1, "after": RUN_ID}).encode())
    assert status == 400


def test_post_content_length_trop_grand_413(site):
    status, _, data = post(site, "/api/stop", body=b"x" * (MAX_BODY + 1))
    assert status == 413 and "error" in as_json(data)


def test_post_content_length_invalide_400(site):
    conn = http.client.HTTPConnection("127.0.0.1", site["port"], timeout=10)
    try:
        conn.putrequest("POST", "/api/stop")
        conn.putheader("Content-Type", "application/json")
        conn.putheader(TOKEN_HEADER, site["state"].token)
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 400 and "error" in as_json(resp.read())
    finally:
        conn.close()


def test_post_route_inconnue_404_json(site):
    status, _, data = post(site, "/api/inconnue")
    assert status == 404 and "error" in as_json(data)


def test_recompute_et_diff_refusent_la_traversee(site):
    for run_id in ("../secret.txt", "C:..\\secret.txt", "/etc", RUN_ID + "/.."):
        status, _, data = post(site, "/api/recompute", body=json.dumps({"id": run_id, "strategic_urls": []}).encode())
        assert status == 403, (run_id, status, data)
        status, _, data = post(site, "/api/diff", body=json.dumps({"before": run_id, "after": RUN_ID}).encode())
        assert status == 403, (run_id, status, data)
    status, _, data = post(site, "/api/recompute", body=json.dumps({"id": "absente-20260101-000000", "strategic_urls": []}).encode())
    assert status == 409 and "inconnue" in as_json(data)["error"]
    status, _, data = post(site, "/api/diff", body=json.dumps({"before": "absente-20260101-000000", "after": RUN_ID}).encode())
    assert status == 400


# ── Lanceur, réponses JSON ─────────────────────────────────────────────────────

def test_lanceur_contient_le_jeton_et_une_csp_a_nonce(site):
    status, headers, data = call(site, "GET", "/")
    assert status == 200
    html = data.decode("utf-8")
    assert f'<meta name="prgraph-token" content="{site["state"].token}">' in html
    assert srv.TOKEN_PLACEHOLDER not in html and srv.NONCE_PLACEHOLDER not in html
    nonce = re.search(r'<script nonce="([^"]+)">', html).group(1)
    csp = headers.get("Content-Security-Policy", "")
    assert f"'nonce-{nonce}'" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("Cache-Control") == "no-store"
    assert TOKEN_HEADER in html   # le lanceur envoie le jeton sur ses POST


def _interpolations(text):
    """Toutes les expressions `${...}` d'un texte, à toutes les profondeurs (accolades appariées)."""
    out, i = [], 0
    while True:
        start = text.find("${", i)
        if start < 0:
            return out
        depth, j = 0, start + 1
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        expr = text[start + 2:j]
        out.append(expr.strip())
        out.extend(_interpolations(expr))
        i = j + 1


def test_lanceur_echappe_tout_ce_qui_entre_dans_le_dom():
    """Garde contre les régressions : dans les lignes qui affectent innerHTML, chaque interpolation passe par esc()
    (ou un gabarit interne lui-même vérifié, ou un ternaire entre deux littéraux). Les aides ok() et bizLabel()
    sont elles-mêmes écrites avec esc()."""
    html = open(os.path.join(srv.VIEWER_DIR, "launcher.html"), encoding="utf-8").read()
    assert "const esc=" in html
    helpers = ("esc(", "ok(", "bizLabel(")
    checked = 0
    for line in html.splitlines():
        if "innerHTML" not in line:
            continue
        for e in _interpolations(line):
            checked += 1
            ok = (e.startswith(helpers) or "`" in e or "esc(" in e
                  or re.match(r"^[\w.!]+\?'[^']*':'[^']*'$", e) is not None     # ternaire entre deux littéraux
                  or re.match(r"^[\w.]+\.length$", e) is not None)               # compteur (textContent sur la même ligne)
            assert ok, (line[:120], e)
    assert checked >= 10
    # Les lignes écrites avec esc() dans les aides : aucune donnée serveur brute (r.url, j.error, c.credentials_path).
    for raw in ("${r.url", "${j.error}", "${c.credentials_path}", "${s.site_url}", "${s.permission}", "${r.id}"):
        assert raw not in html, raw


def test_reponses_json_portent_nosniff_et_csp(site):
    status, headers, data = call(site, "GET", "/api/status")
    assert status == 200
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("Content-Security-Policy") == srv.CSP_NONE
    j = as_json(data)
    assert j["current"] is None and [r["id"] for r in j["runs"]] == [RUN_ID]


def test_route_get_inconnue_404_json(site):
    status, _, data = call(site, "GET", "/nimporte")
    assert status == 404 and "error" in as_json(data)
