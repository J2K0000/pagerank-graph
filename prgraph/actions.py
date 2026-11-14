# -*- coding: utf-8 -*-
"""Plan d'action prescriptif : chaque action est simulée sur le graphe (jumeau numérique) et
classée par le gain qu'elle produit sur le PageRank des classes productives (money + acquisition).

Les gains sont des sorties du modèle, pas des prédictions de positionnement : ils servent à
ordonner les chantiers, pas à promettre un résultat.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

import numpy as np

from . import __version__
from .analysis import FILE_RX, Analysis
from .graph import Graph
from .loaders import POSITIONS
from .pagerank import log_score, power_iteration
from .simulate import Rules, apply_rules

def _fmt(x: float) -> str:
    x = float(x)
    return f"{int(round(x)):,}".replace(",", " ") if abs(x) >= 100 or x.is_integer() else f"{x:.3g}"


def _pt(x: float) -> str:
    """Écart en points, format français (« +0,48 », signe moins typographique), pour les textes lus par le client."""
    return f"{float(x):+.2f}".replace(".", ",").replace("-", "−")


TEMPLATE_POSITIONS = ["header", "nav", "footer", "sidebar", "boilerplate"]
MENU_CAP = 50


def _lang_prefix(url: str) -> str:
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return parts[0].lower() if parts and re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", parts[0], re.I) else ""


class ActionPlanner:
    def __init__(self, g: Graph, pr: np.ndarray, score: np.ndarray, an: Analysis, weights: dict[str, float],
                 alpha: float = 0.85, dangling: str = "uniform", max_sims: int = 120, titles: list[str] | None = None,
                 teleport: np.ndarray | None = None):
        self.g, self.pr, self.score, self.an, self.weights = g, pr, score, an, weights
        self.titles = titles or []
        self.alpha, self.dangling, self.max_sims = alpha, dangling, max_sims
        self.teleport = teleport      # même vecteur de téléportation que le calcul de référence (--restart)
        self.sims = 0
        cls = np.array(an.classes)
        self.productive = np.isin(cls, ["money", "acquisition"])
        self.money = cls == "money"
        self.legal = cls == "legal_support"
        self.base_prod = float(pr[self.productive].sum())
        self.base_money = float(pr[self.money].sum())
        self.rank = np.empty(g.n, dtype=np.int64)
        self.rank[np.argsort(-pr)] = np.arange(1, g.n + 1)
        self.urls = g.urls
        self._upgrades: dict[int, set] = {}

    # ── simulation ────────────────────────────────────────────────────────────
    def simulate(self, rules: Rules) -> dict | None:
        if self.sims >= self.max_sims:
            return None
        self.sims += 1
        g2, st = apply_rules(self.g, rules, self.weights)
        if st["removed"] == 0 and st["added"] == 0 and not st.get("upgraded") and not st.get("moved"):
            return None
        res = power_iteration(g2, alpha=self.alpha, teleport=self.teleport, dangling=self.dangling, home=self.an.home)
        pr2 = res.pr
        score2 = log_score(pr2)
        rank2 = np.empty(self.g.n, dtype=np.int64)
        rank2[np.argsort(-pr2)] = np.arange(1, self.g.n + 1)
        return {"pr": pr2, "score": score2, "rank": rank2, "removed": st["removed"], "added": st["added"] + int(st.get("upgraded", 0)),
                "moved": int(st.get("moved", 0)),
                "d_prod": float(pr2[self.productive].sum() - self.base_prod),
                "d_money": float(pr2[self.money].sum() - self.base_money)}

    def _priority(self, gain_pts: float, score_delta: float = 0.0) -> str:
        if gain_pts >= 0.5 or score_delta >= 15:
            return "critique"
        if gain_pts >= 0.1 or score_delta >= 5:
            return "moyen"
        return "optimisation"

    def _donors(self, target: int, k: int = 3) -> list[int]:
        """Pages émettrices pour un lien contextuel, par cohérence thématique décroissante :
        1. même segment (silo) et même langue, 2. même langue et classe productive.
        Toujours : PageRank élevé, pas déjà liées à la cible, pas la cible, pas un cul-de-sac,
        jamais l'accueil ni une page légale (un lien éditorial n'a de sens que dans son sujet)."""
        lang = _lang_prefix(self.urls[target])
        seg = self.an.segment_index[target]
        linked = set(self.g.dst[self.g.src == target].tolist()) | set(self.g.src[self.g.dst == target].tolist())
        order = np.argsort(-self.pr)
        ok = lambda i: (i != target and i not in linked and not self.an.dangling[i] and not self.legal[i]
                        and i != self.an.home and _lang_prefix(self.urls[i]) == lang)
        same_seg = [int(i) for i in order if ok(i) and self.an.segment_index[i] == seg][:k]
        if len(same_seg) >= k:
            return same_seg
        rest = [int(i) for i in order if ok(i) and self.productive[i] and int(i) not in same_seg][: k - len(same_seg)]
        found = same_seg + rest
        if len(found) < k:
            # Repli : pages qui lient déjà la cible mais seulement depuis le gabarit (menu, pied de page,
            # bandeau). Un lien dans le texte double le lien de menu : poids 0,2 → 1,0 après fusion.
            tmpl = self._template_only_linkers(target)
            ok2 = lambda i: (i != target and not self.an.dangling[i] and not self.legal[i] and i != self.an.home
                             and _lang_prefix(self.urls[i]) == lang and int(i) not in found)
            upg = [int(i) for i in order if int(i) in tmpl and ok2(i) and self.an.segment_index[i] == seg][: k - len(found)]
            if len(found) + len(upg) < k:
                upg += [int(i) for i in order if int(i) in tmpl and ok2(i) and self.productive[i] and int(i) not in upg][: k - len(found) - len(upg)]
            self._upgrades[target] = set(upg)
            found += upg
        return found

    def _template_only_linkers(self, target: int) -> set:
        pos_idx = {p: i for i, p in enumerate(POSITIONS)}
        editorial = {pos_idx[p] for p in ("content", "breadcrumb", "sidebar") if p in pos_idx}
        mask = self.g.dst == target
        srcs = self.g.src[mask]
        poss = self.g.pos[mask]
        has_editorial = {int(sv) for sv, pv in zip(srcs, poss) if int(pv) in editorial}
        return {int(sv) for sv in srcs} - has_editorial

    @staticmethod
    def _effort(family: str, scope: str, links: int = 0) -> dict:
        """Effort de mise en œuvre : un changement de gabarit touche toutes les pages d'un coup
        (levier ×N, coût dev faible), un lien éditorial se pose page par page (levier unitaire)."""
        # Les durées sont des ordres de grandeur par défaut, pas des mesures : à confirmer par l'équipe.
        if family in ("gouffre", "menu", "nofollow"):
            return {"type": "template", "label": "Gabarit (composant global)", "cost": "faible", "units": 1,
                    "who": "dev", "estimate": "ordre de grandeur par défaut : 30 min à 2 h de dev, à confirmer"}
        units = max(1, links)
        return {"type": "editorial", "label": "Éditorial (page par page)", "cost": "moyen" if units <= 3 else "élevé",
                "units": units, "who": "contenu",
                "estimate": f"{units} lien(s) à poser, ordre de grandeur par défaut : {max(15, 20 * units)} min, à confirmer"}

    # ── familles d'actions ────────────────────────────────────────────────────
    MOVE_FROM = ["header", "nav", "sidebar", "boilerplate"]   # positions de gabarit à reléguer en pied de page

    def gouffres(self, limit: int = 8) -> list[dict]:
        """Le gain affiché est celui de l'action réellement prescrite : déplacer les liens de gabarit vers un
        sous-pied de page (le lien reste, sa position et donc son poids changent). Le retrait pur des liens est
        simulé à titre de comparaison et nommé comme tel, jamais prescrit pour des mentions légales."""
        out = []
        cands = [i for i in np.argsort(-self.pr) if self.legal[i]][:limit]
        all_targets = [i for i in np.flatnonzero(self.legal)]
        if all_targets:
            rx = "^(" + "|".join(re.escape(self.urls[i]) for i in all_targets[:400]) + ")$"
            sim = self.simulate(Rules(move_positions=self.MOVE_FROM, move_positions_target=rx, move_to="footer"))
            sim_rm = self.simulate(Rules(remove_positions=TEMPLATE_POSITIONS, remove_positions_target=rx))
            if sim and sim["moved"]:
                gain = 100 * sim["d_prod"]
                gain_rm = 100 * sim_rm["d_prod"] if sim_rm else None
                out.append({
                    "family": "gouffre", "scope": "global", "priority": self._priority(gain),
                    "targets": [int(i) for i in cands], "target_urls": [self.urls[i] for i in cands],
                    "title": "Reléguer les liens vers les pages légales et support dans un sous-pied de page",
                    "problem": f"{len(all_targets)} pages légales / support reçoivent {100 * float(self.pr[self.legal].sum()):.1f} % du PageRank "
                               f"par {sim['moved']} liens de menu, de bandeau ou de barre latérale répétés sur tout le site.",
                    "action": "Regrouper ces liens dans un sous-pied de page distinct de la navigation, un seul lien par cible et "
                              "par page, sans doublon dans le menu ni les bandeaux. Ne pas masquer ces liens en JavaScript : "
                              "les mentions légales doivent rester accessibles et l'obfuscation de liens relève des pratiques "
                              "de manipulation. Le levier est la position, pas la dissimulation.",
                    "gain_pts": round(gain, 2),
                    "gain_text": f"{_pt(gain)} pt de PageRank global vers les pages money et acquisition (liens déplacés en pied de page)"
                                 + (f" ; retrait pur des liens, non prescrit : {_pt(gain_rm)} pt" if gain_rm is not None else ""),
                    "removed": 0, "moved": sim["moved"], "added": 0,
                    "rule": {"kind": "legal_move"},
                })
        if len(all_targets) >= 2:
            # Deuxième levier, même quand les liens sont déjà en pied de page : ne garder dans le gabarit qu'un lien
            # vers une page « centre légal » (la mieux alimentée), qui lie elle-même les autres dans son texte.
            hub = max(all_targets, key=lambda i: self.pr[i])
            others = [i for i in all_targets if i != hub][:400]
            rx_o = "^(" + "|".join(re.escape(self.urls[i]) for i in others) + ")$"
            sim = self.simulate(Rules(remove_positions=TEMPLATE_POSITIONS, remove_positions_target=rx_o,
                                      add_edges=[(self.urls[hub], self.urls[i], "content") for i in others]))
            if sim and sim["removed"]:
                gain = 100 * sim["d_prod"]
                out.append({
                    "family": "gouffre", "scope": "global", "priority": self._priority(gain),
                    "targets": [int(hub)] + [int(i) for i in others[:7]], "target_urls": [self.urls[hub]] + [self.urls[i] for i in others[:7]],
                    "title": f"Regrouper les liens légaux derrière une seule page centre légal ({urlsplit(self.urls[hub]).path})",
                    "problem": f"{len(all_targets)} pages légales / support sont chacune liées depuis tout le gabarit ; "
                               f"ensemble elles captent {100 * float(self.pr[self.legal].sum()):.1f} % du PageRank.",
                    "action": f"Garder dans le gabarit un seul lien, vers {urlsplit(self.urls[hub]).path}, et lier les {len(others)} autres "
                              "pages légales depuis le texte de cette page centre. Chaque page reste accessible en deux clics ; "
                              "ne rien masquer en JavaScript (l'obfuscation de liens relève des pratiques de manipulation).",
                    "gain_pts": round(gain, 2),
                    "gain_text": f"{_pt(gain)} pt vers money et acquisition ({sim['removed']} liens de gabarit retirés, "
                                 f"{sim['added']} liens éditoriaux ajoutés depuis la page centre)",
                    "removed": sim["removed"], "added": sim["added"],
                    "rule": {"kind": "legal_hub", "hub": self.urls[hub]},
                })
        for i in cands:
            rx_i = "^" + re.escape(self.urls[i]) + "$"
            sim = self.simulate(Rules(move_positions=self.MOVE_FROM, move_positions_target=rx_i, move_to="footer"))
            if not sim or not sim["moved"]:
                continue
            gain = 100 * sim["d_prod"]
            out.append({
                "family": "gouffre", "scope": "page", "priority": self._priority(gain),
                "targets": [int(i)], "target_urls": [self.urls[i]],
                "title": f"Reléguer en pied de page les liens de gabarit vers {urlsplit(self.urls[i]).path}",
                "problem": f"Rang {self.rank[i]} / {self.g.n} en PageRank (score {self.score[i]:.0f}), {int(self.an.in_deg[i])} liens entrants "
                           f"dont {sim['moved']} en menu, bandeau ou barre latérale.",
                "action": "Conserver un seul lien par page vers cette URL, dans le sous-pied de page ; le retirer du menu, "
                          "des bandeaux et du pied de page principal. Le lien reste accessible.",
                "gain_pts": round(gain, 2),
                "gain_text": f"{_pt(gain)} pt vers money et acquisition ; la page passerait du rang {self.rank[i]} au rang {sim['rank'][i]} "
                             "(simulation du déplacement en pied de page, pas d'une suppression)",
                "removed": 0, "moved": sim["moved"], "added": 0, "rank_before": int(self.rank[i]), "rank_after": int(sim["rank"][i]),
            })
        return out


    # ── prescription : combien, d'où, où, quelle ancre ─────────────────────────
    WHERE = ("dans le corps du texte, dans la première moitié de la page, au sein d'un paragraphe qui traite "
             "du sujet de la cible ; pas dans un bloc « articles liés » de bas de page ni dans la barre latérale")

    def anchors(self, target: int) -> list[str]:
        """Ancre canonique + variantes déclarées (2 à 4), descriptives, jamais génériques."""
        title = (self.titles[target] if target < len(self.titles) else "") or ""
        title = re.split(r"\s+[|·–\u2014-]\s+|\s*:\s*|\s*\(", title)[0].strip() if title else ""
        slug = urlsplit(self.urls[target]).path.rstrip("/").rsplit("/", 1)[-1]
        slug_words = " ".join(w for w in re.split(r"[-_]+", slug) if w and not w.isdigit())
        no_year = re.sub(r"\s+(en|in|pour|for)?\s*20\d\d\b", "", title).strip()
        out: list[str] = []
        for cand in (title[:80], slug_words, no_year if no_year != title else ""):
            c = cand.strip()
            if len(c) >= 4 and c.lower() not in [o.lower() for o in out]:
                out.append(c)
        return out or [slug or "cette page"]

    def objective(self, target: int, ambitious: bool = False) -> tuple[float, str]:
        """Niveau à atteindre. Famine détectée : quartile supérieur de son silo, au moins la médiane du
        site. Cible choisie (à pousser) : les 10 % les plus fortes de son silo, au moins le quartile
        supérieur du site : on veut un avantage net, pas un rattrapage."""
        seg = self.an.segment_index[target]
        mask = self.an.segment_index == seg
        name = self.an.segment_names[seg]
        if ambitious:
            q90 = float(np.percentile(self.score[mask], 90)) if mask.sum() >= 10 else float(np.percentile(self.score, 75))
            p75 = float(np.percentile(self.score, 75))
            return (q90, f"10 % les plus fortes de son silo « {name} »") if q90 >= p75 else (p75, "quartile supérieur du site")
        q75 = float(np.percentile(self.score[mask], 75)) if mask.sum() >= 4 else float(np.median(self.score))
        med = float(np.median(self.score))
        if q75 >= med:
            return q75, f"quartile supérieur de son silo « {name} »"
        return med, "médiane du site"

    def prescribe(self, target: int, max_links: int = 6, min_gain: float = 1.0, ambitious: bool = False,
                  min_links: int = 1) -> dict:
        """Ajoute les émetteurs un à un (même silo d'abord) et simule à chaque pas jusqu'à atteindre
        l'objectif ou jusqu'à ce que le lien suivant rapporte moins de `min_gain` point. `min_links`
        impose un plancher (deux liens pour une cible choisie : un seul lien est un point de défaillance)."""
        obj, obj_label = self.objective(target, ambitious)
        base = float(self.score[target])
        res = {"target": int(target), "objective": round(obj, 1), "objective_label": obj_label, "score_before": round(base, 1),
               "rank_before": int(self.rank[target]), "steps": [], "reached": base >= obj, "where": self.WHERE,
               "anchors": self.anchors(target), "sim": None}
        if base >= obj:
            return res
        donors = self._donors(target, max_links)
        adds = []
        last = base
        anchors = res["anchors"]
        for k, d in enumerate(donors):
            adds.append((self.urls[d], self.urls[target], "content"))
            sim = self.simulate(Rules(add_edges=list(adds)))
            if not sim:
                adds.pop()
                break
            sc = float(sim["score"][target])
            gain = sc - last
            if gain < min_gain and k >= min_links:
                adds.pop()
                break
            res["steps"].append({"donor": int(d), "donor_url": self.urls[d], "position": "content",
                                 "anchor": anchors[min(k, len(anchors) - 1)] if k == 0 else anchors[1 + (k - 1) % max(1, len(anchors) - 1)] if len(anchors) > 1 else anchors[0],
                                 "upgrade": d in self._upgrades.get(target, set()),
                                 "same_segment": bool(self.an.segment_index[d] == self.an.segment_index[target]),
                                 "donor_score": round(float(self.score[d]), 1),
                                 "score_after": round(sc, 1), "rank_after": int(sim["rank"][target]), "gain": round(gain, 1)})
            res["sim"] = sim
            last = sc
            if sc >= obj and len(res["steps"]) >= min_links:
                res["reached"] = True
                break
        if last >= obj:
            res["reached"] = True
        return res

    def famines(self, limit: int = 12, links: int = 3) -> list[dict]:
        out = []
        an = self.an
        # Les cibles déclarées (--strategic) passent en premier, quel que soit leur score : c'est le
        # pilotage par URL ; viennent ensuite les famines détectées puis la demande non servie.
        chosen = [int(i) for i in np.flatnonzero(an.strategic)]
        chosen.sort(key=lambda i: (-an.value[i], self.score[i]))
        cands = chosen[:limit]
        cands += [i for i in an.starving_list if i not in cands][: max(0, limit - len(cands))]
        if an.demand_list:
            cands += [i for i in an.demand_list if i not in cands][: max(0, limit - len(cands))]
        for k, i in enumerate(cands):
            chosen_target = bool(an.strategic[i])
            pres = self.prescribe(i, ambitious=chosen_target, min_links=2 if chosen_target else 1)
            steps = pres["steps"]
            why = []
            if an.value[i] > 0:
                why.append(f"{an.value_col} = {_fmt(an.value[i])}")
            if an.demand is not None and an.demand[i] > 0:
                why.append(f"{an.demand_col} = {_fmt(an.demand[i])}")
            if chosen_target:
                why.append("cible choisie")
            problem = (f"Score {self.score[i]:.0f} / 100, rang {self.rank[i]} / {self.g.n}, {int(an.in_deg[i])} liens entrants, "
                       f"profondeur {'inatteignable' if an.depth[i] < 0 else int(an.depth[i])}"
                       + (f" ; {' ; '.join(why)}" if why else "") + f". Objectif : {pres['objective']:.0f} ({pres['objective_label']}).")
            if not steps:
                if pres["reached"]:
                    if not chosen_target:
                        continue
                    action = ("Déjà au niveau de l'objectif : aucun lien à ajouter. La page peut servir d'émettrice pour d'autres "
                              "cibles de son silo ; surveiller qu'elle ne recule pas (diff entre analyses).")
                    gain_text = f"score {self.score[i]:.0f} ≥ objectif {pres['objective']:.0f}, rien à faire"
                else:
                    action = "Aucune page émettrice disponible dans sa langue (toutes la lient déjà dans le texte ou sont des culs-de-sac)."
                    gain_text = "non simulable"
                out.append({
                    "family": "cible" if chosen_target else "famine", "scope": "page", "priority": "optimisation",
                    "targets": [int(i)], "target_urls": [self.urls[i]], "donors": [], "donor_urls": [],
                    "title": f"Renforcer la cible choisie {urlsplit(self.urls[i]).path}", "problem": problem, "action": action,
                    "gain_pts": 0.0, "gain_text": gain_text, "removed": 0, "added": 0, "prescription": pres_public(pres),
                })
                continue
            sim = pres["sim"]
            d_score = float(sim["score"][i] - self.score[i])
            gain = 100 * sim["d_prod"]
            prio = "critique" if k < 4 else ("moyen" if k < 10 else "optimisation")
            same_seg = sum(1 for st in steps if st["same_segment"])
            lines = []
            for n_, st in enumerate(steps, 1):
                lines.append(f"{n_}. depuis {urlsplit(st['donor_url']).path} (score {st['donor_score']:.0f}"
                             + (", déjà liée par le menu : doubler d'un lien dans le texte" if st["upgrade"] else "")
                             + f"), ancre « {st['anchor']} » → score {st['score_after']:.0f}")
            action = (f"Ajouter {len(steps)} lien(s) {self.WHERE}. "
                      + " ; ".join(lines) + f". {same_seg}/{len(steps)} émetteur(s) dans le même silo. "
                      + ("Objectif atteint." if pres["reached"] else "Objectif non atteint avec les émetteurs disponibles : le lien suivant rapporterait moins d'un point.")
                      + " Ancre canonique sur le premier lien, variantes déclarées ensuite, jamais la même ancre exacte partout.")
            out.append({
                "family": "cible" if chosen_target else "famine", "scope": "page", "priority": "critique" if chosen_target and k < 8 else prio,
                "targets": [int(i)], "target_urls": [self.urls[i]], "donors": [st["donor"] for st in steps],
                "donor_urls": [st["donor_url"] for st in steps], "donors_same_segment": same_seg,
                "title": (f"Renforcer la cible choisie {urlsplit(self.urls[i]).path}" if chosen_target
                          else f"Sauver de la famine {urlsplit(self.urls[i]).path}"),
                "problem": problem, "action": action,
                "gain_pts": round(gain, 2),
                "gain_text": f"{len(steps)} lien(s) : score {self.score[i]:.0f} → {sim['score'][i]:.0f} ({d_score:+.0f} pt), rang {self.rank[i]} → {sim['rank'][i]}",
                "removed": 0, "added": len(steps), "score_before": round(float(self.score[i]), 1),
                "score_after": round(float(sim["score"][i]), 1), "rank_before": int(self.rank[i]), "rank_after": int(sim["rank"][i]),
                "prescription": pres_public(pres),
            })
        return out

    def trous_noirs(self, limit: int = 5) -> list[dict]:
        out = []
        money_targets = [int(i) for i in np.argsort(-self.an.value if self.an.value.sum() > 0 else -self.pr) if self.money[i]][:20]
        if not money_targets:
            return out
        for i in self.an.black_holes[:limit]:
            if self.an.uncrawled is not None and self.an.uncrawled[i]:
                continue
            if FILE_RX.search(urlsplit(self.urls[i]).path):
                continue        # un PDF ou une image ne peut pas recevoir de bloc de liens
            lang = _lang_prefix(self.urls[i])
            tg = [t for t in money_targets if _lang_prefix(self.urls[t]) == lang and t != i][:2] or money_targets[:2]
            sim = self.simulate(Rules(add_edges=[(self.urls[i], self.urls[t], "content") for t in tg]))
            if not sim:
                continue
            gain = 100 * sim["d_prod"]
            out.append({
                "family": "trou_noir", "scope": "page", "priority": self._priority(gain),
                "targets": [int(i)], "target_urls": [self.urls[i]], "donors": tg, "donor_urls": [self.urls[t] for t in tg],
                "title": f"Faire rebondir le jus de {urlsplit(self.urls[i]).path}",
                "problem": f"Cul-de-sac : score {self.score[i]:.0f}, {int(self.an.in_deg[i])} liens entrants, aucun lien sortant. "
                           "La masse reçue est réinjectée au hasard au lieu d'irriguer les pages voisines.",
                "action": "Ajouter un bloc de liens sortants vers " + ", ".join(urlsplit(self.urls[t]).path for t in tg)
                          + " (offre liée, démonstration, cas client).",
                "gain_pts": round(gain, 2), "gain_text": f"{_pt(gain)} pt vers money et acquisition",
                "removed": 0, "added": sim["added"],
            })
        return out

    def menu_cap(self, cap: int = MENU_CAP) -> list[dict]:
        pos_idx = {p: i for i, p in enumerate(POSITIONS)}
        tmpl = np.isin(self.g.pos, [pos_idx[p] for p in ("header", "nav", "boilerplate") if p in pos_idx])
        per_source = np.bincount(self.g.src[tmpl], minlength=self.g.n)
        if per_source.max(initial=0) <= cap:
            return []
        out = []
        for p in ("header", "nav", "boilerplate"):
            if p not in pos_idx or not (self.g.pos == pos_idx[p]).any():
                continue
            sim = self.simulate(Rules(cap_position=(p, cap)))
            if not sim or not sim["removed"]:
                continue
            gain = 100 * sim["d_prod"]
            out.append({
                "family": "menu", "scope": "global", "priority": self._priority(gain),
                "targets": [], "target_urls": [],
                "title": f"Plafonner les liens « {p} » à {cap} par page",
                "problem": f"Jusqu'à {int(per_source.max())} liens de gabarit par page : chaque lien de menu dilue les liens éditoriaux "
                           "de la page qui le porte.",
                "action": f"Réduire le méga-menu / les blocs répétés à {cap} liens par page au plus ; déplacer le reste vers des pages "
                          "hub intermédiaires (catégories, pages piliers).",
                "gain_pts": round(gain, 2), "gain_text": f"{_pt(gain)} pt vers money et acquisition ({sim['removed']} liens retirés)",
                "removed": sim["removed"], "added": 0,
                "rule": {"kind": "menu_cap", "position": p, "cap": cap},
            })
        return out

    def orphans_with_value(self, limit: int = 10) -> list[dict]:
        an = self.an
        cands = [i for i in an.unreachable if an.value[i] > 0 or an.strategic[i]]
        cands.sort(key=lambda i: -an.value[i])
        cands = cands[:limit]
        if not cands:
            return []
        adds = []
        for i in cands:
            for d in self._donors(i, 2):
                adds.append((self.urls[d], self.urls[i], "content"))
        sim = self.simulate(Rules(add_edges=adds))
        if not sim:
            return []
        gain = 100 * sim["d_prod"]
        return [{
            "family": "orphelines", "scope": "global", "priority": self._priority(gain, 10),
            "targets": [int(i) for i in cands], "target_urls": [self.urls[i] for i in cands],
            "title": f"Raccrocher {len(cands)} pages à valeur inatteignables depuis l'accueil",
            "problem": "Ces pages existent dans le sitemap et captent de la valeur, mais aucun chemin de liens ne mène à elles : "
                       "Google les voit par le sitemap seulement.",
            "action": "Ajouter deux liens contextuels vers chacune depuis les pages piliers de leur langue et leur silo "
                      "(donneurs proposés dans le brief), ou les lister dans la page hub de leur section.",
            "gain_pts": round(gain, 2), "gain_text": f"{_pt(gain)} pt vers money et acquisition, {sim['added']} liens ajoutés",
            "removed": 0, "added": sim["added"],
        }]

    def nofollow(self, clean_stats: dict) -> list[dict]:
        n_nf = int(clean_stats.get("nofollow", 0))
        if not n_nf:
            return []
        leak = float(self.an.zone_flows.get("fuites_et_culs_de_sac", 0.0))
        return [{
            "family": "nofollow", "scope": "global", "priority": "moyen" if n_nf >= 100 else "optimisation",
            "targets": [], "target_urls": [],
            "title": f"Retirer le nofollow des {n_nf} liens internes",
            "problem": "Un lien interne en nofollow ne concentre pas le jus sur les autres liens : la page divise toujours par le "
                       "nombre total de liens et la part du nofollow est perdue (sculpting inopérant depuis 2009).",
            "action": "Supprimer rel=nofollow sur les liens internes ; si la cible ne doit pas être indexée, la passer en noindex "
                      "ou retirer le lien, mais ne pas le laisser en nofollow.",
            "gain_pts": 0.0, "gain_text": f"masse perdue par fuites et culs-de-sac aujourd'hui : {100 * leak:.1f} % (non simulé : les liens "
                                         "nofollow sont retirés du graphe au nettoyage)",
            "removed": 0, "added": 0,
        }]

    def plan(self, clean_stats: dict) -> list[dict]:
        actions = []
        actions += self.gouffres()
        actions += self.famines()
        actions += self.orphans_with_value()
        actions += self.menu_cap()
        actions += self.trous_noirs()
        actions += self.nofollow(clean_stats)
        for a in actions:
            a["effort"] = self._effort(a["family"], a["scope"], a.get("added", 0))
            # Gain par unité d'effort : gain global (points de PR productif) ou, pour une page,
            # progression de score ramenée à 100, divisé par le nombre de liens à poser.
            local = (a.get("score_after", 0) - a.get("score_before", 0)) / 100.0
            a["gain_per_effort"] = round((abs(a["gain_pts"]) + local) / a["effort"]["units"], 4)
        order = {"critique": 0, "moyen": 1, "optimisation": 2}
        actions.sort(key=lambda a: (order[a["priority"]], -a["gain_per_effort"], -abs(a["gain_pts"])))
        for k, a in enumerate(actions, 1):
            a["id"] = f"SEO-{k:02d}"
        # Victoires rapides : meilleur rapport gain / effort, toutes priorités confondues
        quick = sorted(actions, key=lambda a: -a["gain_per_effort"])[:5]
        for a in actions:
            a["quick_win"] = a in quick
        return actions


def pres_public(p: dict) -> dict:
    return {k: v for k, v in p.items() if k != "sim"}


def build_scenario(g: Graph, pr: np.ndarray, an: Analysis, weights: dict[str, float], alpha: float = 0.85,
                   dangling: str = "uniform", teleport: np.ndarray | None = None, menu_cap: int = MENU_CAP,
                   actions: list[dict] | None = None) -> dict | None:
    """Scénario « chantiers de gabarit » pour l'avant / après du rapport : exactement les chantiers de gabarit
    globaux du plan d'action (clé `rule` des actions), appliqués ensemble et dans cet ordre : regrouper les liens
    légaux derrière une page centre (liens de gabarit vers les autres pages légales retirés, liens ajoutés dans le
    texte de la page centre), reléguer en pied de page les liens de gabarit restants vers les pages légales,
    plafonner les menus. Chaque étape est décrite avec ses volumes. Sans chantier de gabarit dans le plan, None.
    Retourne les parts avant et après ; ce sont des sorties du modèle, pas une prédiction de trafic."""
    from .analysis import alignment_score
    rules = [a for a in (actions or []) if a.get("scope") == "global" and isinstance(a.get("rule"), dict)]
    order = {"legal_hub": 0, "legal_move": 1, "menu_cap": 2}
    rules.sort(key=lambda a: order.get(a["rule"].get("kind"), 9))
    if not rules:
        return None
    cls = np.array(an.classes)
    legal = np.flatnonzero(cls == "legal_support")
    legal_rx = "^(" + "|".join(re.escape(g.urls[i]) for i in legal[:400]) + ")$" if legal.size else None
    g2, removed, moved, added, changes, tickets = g, 0, 0, 0, [], []
    for a in rules:
        r = a["rule"]
        kind = r.get("kind")
        if kind == "legal_hub" and legal.size >= 2 and r.get("hub") in g.urls:
            others = [g.urls[i] for i in legal if g.urls[i] != r["hub"]][:400]
            rx = "^(" + "|".join(re.escape(u) for u in others) + ")$"
            rule = Rules(remove_positions=TEMPLATE_POSITIONS, remove_positions_target=rx,
                         add_edges=[(r["hub"], u, "content") for u in others])
            g2, st = apply_rules(g2, rule, weights)
            if st["removed"] or st["added"]:
                changes.append(f"{st['removed']} liens de gabarit vers {len(others)} pages légales retirés, "
                               f"{st['added']} liens ajoutés dans le texte de la page centre {urlsplit(r['hub']).path}")
        elif kind == "legal_move" and legal_rx:
            rule = Rules(move_positions=list(ActionPlanner.MOVE_FROM), move_positions_target=legal_rx, move_to="footer")
            g2, st = apply_rules(g2, rule, weights)
            if st.get("moved"):
                changes.append(f"{st['moved']} liens de menu ou de bandeau vers les pages légales relégués en pied de page")
        elif kind == "menu_cap" and r.get("position"):
            rule = Rules(cap_position=(r["position"], int(r.get("cap", menu_cap))))
            g2, st = apply_rules(g2, rule, weights)
            if st["removed"]:
                changes.append(f"liens « {r['position']} » plafonnés à {int(r.get('cap', menu_cap))} par page "
                               f"({st['removed']} liens retirés)")
        else:
            continue
        removed += int(st["removed"])
        moved += int(st.get("moved", 0))
        added += int(st.get("added", 0))
        if a.get("id"):
            tickets.append(a["id"])
    if not changes:
        return None
    pr2 = power_iteration(g2, alpha=alpha, teleport=teleport, dangling=dangling, home=an.home).pr
    value_share = an.value_share if float(an.value_share.sum()) > 0 else None

    def state(p: np.ndarray) -> dict:
        al = alignment_score(p, an.classes, value_share)
        seg_pr = np.bincount(an.segment_index, weights=p, minlength=len(an.segment_names))
        out = {"alignment": al["score"], "productive_share": al["productive_share"], "waste_share": al["waste_share"],
               "class_share": {k: float(f"{v:.6g}") for k, v in al["pr_share"].items()},
               # Part par segment : le rapport la regroupe par rubrique (familles), comme ses autres diapositives.
               "segment_share": {str(nm): float(f"{seg_pr[k]:.6g}") for k, nm in enumerate(an.segment_names)}}
        if value_share is not None and an.money.any():
            # Pages qui portent la valeur mesurée (les mêmes que alignment.value_pages_pr_share)
            out["value_pages_share"] = float(f"{p[an.money].sum():.6g}")
        ws = (an.alignment or {}).get("worst_segment")
        if ws:
            k = an.segment_names.index(ws["segment"]) if ws["segment"] in an.segment_names else None
            if k is not None:
                out["worst_segment"] = {"segment": ws["segment"], "pr_share": float(f"{seg_pr[k]:.6g}"),
                                        "value_share": float(f"{ws['value_share']:.6g}")}
        if an.starving.any():
            out["starving_pr_share"] = float(f"{p[an.starving].sum():.6g}")
        return out

    return {"label": "Chantiers de gabarit du plan d'action appliqués ensemble", "changes": changes, "tickets": tickets,
            "removed": removed, "moved": moved, "added": added,
            "before": state(pr), "after": state(pr2),
            "note": "Sortie du modèle de PageRank interne (surfeur raisonnable) : un ordre de grandeur pour décider, "
                    "pas une prédiction de positions ni de trafic."}


def build_plan(g, pr, score, an, weights, clean_stats, alpha=0.85, dangling="uniform", max_sims=120, titles=None,
               teleport=None) -> list[dict]:
    return ActionPlanner(g, pr, score, an, weights, alpha=alpha, dangling=dangling, max_sims=max_sims, titles=titles,
                         teleport=teleport).plan(clean_stats)


# ── Brief développeur (markdown) ───────────────────────────────────────────────

FAMILY_LABEL = {"gouffre": "Colmater un gouffre", "famine": "Sauvetage de famine", "cible": "Renforcer une cible choisie",
                "trou_noir": "Élaguer un trou noir", "menu": "Alléger le gabarit", "orphelines": "Raccrocher des orphelines",
                "nofollow": "Nofollow interne"}
PRIORITY_ICON = {"critique": "🚨 Critique", "moyen": "⚠️ Moyen", "optimisation": "💡 Optimisation"}
EFFORT_ICON = {"template": "🧩 Gabarit", "editorial": "✍️ Éditorial"}


def write_dev_brief(path: str, site: str, an: Analysis, actions: list[dict], g: Graph) -> None:
    al = an.alignment
    L = [f"Outil : prgraph {__version__} · {datetime.now():%Y-%m-%d}\n",
         f"# Brief développeur : maillage interne de {site}\n",
         f"Score d'alignement business : **{al.get('score', 0):.0f} / 100** (base : {al.get('basis', '')}). "
         f"Part du PageRank sur les pages money et acquisition : {100 * al.get('productive_share', 0):.1f} % ; "
         f"sur les pages légales et support : {100 * al.get('waste_share', 0):.1f} %.\n",
         "Les gains indiqués sont des sorties du modèle de PageRank interne pondéré (surfeur raisonnable), à lire comme un "
         "ordre de priorité, pas comme une prédiction de positionnement. Règle transverse : aucun lien n'est masqué ni obfusqué ; "
         "on dédoublonne, on déplace, on ajoute.\n",
         "| Priorité | Ticket | Type | Effort | Cible | Gain estimé |\n|---|---|---|---|---|---|"]
    for a in actions:
        tgt = a["target_urls"][0] if a["target_urls"] else "site entier"
        if len(a["target_urls"]) > 1:
            tgt += f" (+{len(a['target_urls']) - 1})"
        eff = a.get("effort", {})
        L.append(f"| {PRIORITY_ICON[a['priority']]}{' ⚡' if a.get('quick_win') else ''} | {a['id']} | {FAMILY_LABEL[a['family']]} | "
                 f"{EFFORT_ICON.get(eff.get('type'), '')} {eff.get('estimate', '')} | {tgt} | {a['gain_text']} |")
    L.append("\n⚡ = victoire rapide (meilleur rapport gain / effort). Gabarit = un composant modifié touche toutes les pages ; "
             "éditorial = liens posés page par page.\n")
    L.append("## Gouvernance : éviter le sculptage en boucle\n")
    L.append("- Le PageRank interne est un jeu à somme nulle : chaque lien ajouté dilue les autres liens de la page émettrice, "
             "chaque lien retiré renvoie sa part aux liens restants. Une page ne monte que si d'autres descendent.")
    L.append("- Les cibles se décident en portefeuille (`--strategic`), jamais une à la fois : la simulation porte sur l'ensemble et "
             "la liste des pages qui perdent est contrôlée avant mise en production (section Baisses du rapport).")
    L.append("- Les leviers de gabarit (dédoublonner, sortir le légal, plafonner les menus) relèvent toutes les pages productives sans "
             "arbitrage entre elles ; les leviers éditoriaux sont des transferts ciblés et doivent rester dans le silo de la cible.")
    L.append("- Chaque lien posé est consigné (source, cible, ancre, raison, date) ; `prgraph diff --before <analyse N> --after <analyse N+1>` "
             "liste les liens disparus vers les cibles protégées et les pages stratégiques qui ont reculé.\n")
    for a in actions:
        L.append(f"### Ticket {a['id']} : {a['title']}\n")
        L.append(f"- **Priorité :** {PRIORITY_ICON[a['priority']]} ({FAMILY_LABEL[a['family']]})"
                 + (" ⚡ victoire rapide" if a.get("quick_win") else ""))
        eff = a.get("effort", {})
        if eff:
            L.append(f"- **Effort :** {EFFORT_ICON.get(eff.get('type'), '')}, {eff.get('estimate', '')} ({eff.get('who', '')})")
        L.append(f"- **Problème :** {a['problem']}")
        L.append(f"- **Consigne :** {a['action']}")
        pres = a.get("prescription")
        if pres and pres.get("steps"):
            L.append(f"- **Objectif :** score {pres['objective']:.0f} ({pres['objective_label']}), depuis {pres['score_before']:.0f}. "
                     f"Où : {pres['where']}.")
            L.append("- **Liens à poser :**\n")
            L.append("  | # | Depuis (score) | Ancre | Score après | Rang après |\n  |---|---|---|---|---|")
            for n_, st in enumerate(pres["steps"], 1):
                L.append(f"  | {n_} | {st['donor_url']} ({st['donor_score']:.0f}){' · doubler le lien de menu' if st['upgrade'] else ''} | "
                         f"{st['anchor']} | {st['score_after']:.0f} | {st['rank_after']} |")
            L.append("")
            L.append(f"- **Ancres déclarées :** canonique « {pres['anchors'][0]} »"
                     + (" ; variantes : " + " ; ".join(f"« {v} »" for v in pres["anchors"][1:]) if len(pres["anchors"]) > 1 else "")
                     + ". Ne pas inventer d'autre variante en rédigeant.")
        elif a.get("donor_urls"):
            L.append("- **Pages émettrices proposées :** " + ", ".join(a["donor_urls"]))
        if len(a["target_urls"]) > 1:
            L.append("- **URL concernées :** " + ", ".join(a["target_urls"][:15]) + (" …" if len(a["target_urls"]) > 15 else ""))
        L.append(f"- **Impact attendu (modèle) :** {a['gain_text']}.")
        L.append("- **Recette :** recrawler après mise en production, relancer le calcul, vérifier le rang et le score de la cible "
                 "et le score d'alignement global.\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
