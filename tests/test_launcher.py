# -*- coding: utf-8 -*-
"""Lanceur `prgraph serve` : liste des analyses précédentes (list_runs), messages d'erreur d'une tâche sans page
récupérée, libellés et contrôles du formulaire. Aucun appel réseau, aucune écriture hors du dossier de pytest."""
import json
import os
import re

import pytest

from prgraph import server as srv
from prgraph.server import Job, State, crawl_failure, list_runs

LAUNCHER = os.path.join(srv.VIEWER_DIR, "launcher.html")


def html():
    with open(LAUNCHER, encoding="utf-8") as fh:
        return fh.read()


# ── list_runs ──────────────────────────────────────────────────────────────────

def test_list_runs_ne_montre_que_les_analyses_avec_resultat(tmp_path):
    runs = tmp_path / "runs"
    (runs / "a.fr-20260101-000000").mkdir(parents=True)
    (runs / "a.fr-20260101-000000" / "job.json").write_text(
        json.dumps({"params": {"url": "https://a.fr/"}, "crawl": {"pages": 12, "edges": 40}, "finished": "2026-01-01T00:10:00"}),
        encoding="utf-8")
    (runs / "b.fr-20260102-000000").mkdir()
    (runs / "b.fr-20260102-000000" / "pages.csv").write_text("url,score\nhttps://b.fr/,100\n", encoding="utf-8")
    (runs / "vide-20260103-000000").mkdir()                                   # dossier vide : omis
    (runs / "crawl-seul-20260104-000000").mkdir()                             # crawl interrompu : omis
    (runs / "crawl-seul-20260104-000000" / "edges.csv").write_text("source,target\n", encoding="utf-8")
    (runs / "notes.txt").write_text("pas une analyse", encoding="utf-8")      # fichier : omis
    out = list_runs(str(runs))
    assert [r["id"] for r in out] == ["b.fr-20260102-000000", "a.fr-20260101-000000"]
    a = out[1]
    assert a["url"] == "https://a.fr/" and a["pages"] == 12 and a["edges"] == 40 and a["report"] is False
    assert State(str(runs)).runs() == out


def test_list_runs_dossier_absent_et_job_json_illisible(tmp_path):
    assert list_runs(str(tmp_path / "absent")) == []
    run = tmp_path / "runs" / "c.fr-20260105-000000"
    run.mkdir(parents=True)
    (run / "job.json").write_text("{pas du json", encoding="utf-8")
    out = list_runs(str(tmp_path / "runs"))
    assert [r["id"] for r in out] == ["c.fr-20260105-000000"] and "url" not in out[0]


# ── Messages d'erreur d'une tâche ───────────────────────────────────────────────

@pytest.mark.parametrize("stats, respect, expected", [
    ({"fetched": 0, "blocked": 0, "errors": 0, "robots_unavailable": True}, True, "robots.txt injoignable, crawl refusé"),
    ({"fetched": 0, "blocked": 1, "errors": 0, "robots_unavailable": False}, True,
     "Aucune page récupérée : robots.txt interdit le site"),
    ({"fetched": 0, "blocked": 0, "errors": 1, "robots_unavailable": False}, True, "Aucune page récupérée : le site ne répond pas"),
    ({"fetched": 0, "blocked": 0, "errors": 0, "stopped": True}, True, "Aucune page récupérée : crawl arrêté"),
    ({"pages": 0}, True, "Aucune page récupérée"),
])
def test_crawl_failure_nomme_la_cause(stats, respect, expected):
    assert crawl_failure(stats, respect).startswith(expected)


def test_crawl_failure_rien_a_signaler():
    assert crawl_failure({"fetched": 3, "blocked": 10, "robots_unavailable": False}) is None
    # robots.txt injoignable mais respect désactivé : le crawl a pu se faire
    assert crawl_failure({"fetched": 5, "robots_unavailable": True}, respect_robots=False) is None


@pytest.mark.parametrize("stats, expected", [
    ({"pages": 0, "fetched": 0, "blocked": 1, "errors": 0, "robots_unavailable": False, "edges": 0},
     "Aucune page récupérée : robots.txt interdit le site"),
    ({"pages": 0, "fetched": 0, "blocked": 0, "errors": 0, "robots_unavailable": True, "edges": 0},
     "robots.txt injoignable, crawl refusé"),
])
def test_job_sans_page_recuperee_termine_en_erreur_claire(tmp_path, monkeypatch, stats, expected):
    monkeypatch.setattr(srv, "crawl", lambda cfg, progress, stop_event: dict(stats))
    job = Job({"url": "https://exemple.fr/"}, str(tmp_path))
    job.run()
    assert job.done and job.phase == "erreur"
    assert job.error.startswith(expected)
    assert not os.path.exists(os.path.join(job.dir, "job.json"))     # analyse vide : absente de la liste


# ── Formulaire du lanceur ──────────────────────────────────────────────────────

def test_libelles_et_aide_du_formulaire():
    h = html()
    assert "Délai entre requêtes (s)" in h and "Délai entre lots" not in h
    assert re.search(r'id="delay"[^>]*min="0\.1"', h)
    assert "Au-delà, le rapport est partiel : les pages non crawlées sont comptées comme des fuites" in h
    assert 'aria-describedby="max_pages_hint"' in h


def test_adresse_verifiee_avant_tout_appel_serveur():
    """Adresse vide ou invalide : message sous le champ, retour avant postJson('/api/analyze')."""
    h = html()
    assert re.search(r'<form id="form"[^>]*novalidate', h)
    assert 'id="url_err"' in h and 'aria-describedby="url_err"' in h
    handler = h[h.index("$('form').addEventListener('submit'"):]
    check, post = handler.index("urlError("), handler.index("postJson('/api/analyze'")
    assert check < handler.index("if(bad) return;") < post
    assert "alert('Indiquez" not in h.split("function urlError")[1].split("$('form')")[0]


def test_erreur_de_tache_affichee_en_texte_brut():
    h = html()
    assert 'id="jobErr"' in h
    line = next(l for l in h.splitlines() if "jobErr.textContent" in l)
    assert "c.error" in line and "innerHTML" not in line
    # garde-fous existants intacts : échappement et jeton anti-CSRF
    assert "const esc=" in h and "'X-Prgraph-Token':TOKEN" in h
