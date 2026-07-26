"""
Bot de sélection automatique de pronostics.

Ce script :
1. Interroge une API de cotes sportives réelles (The Odds API — the-odds-api.com,
   qui propose un plan gratuit suffisant pour démarrer).
2. Récupère les cotes sur plusieurs marchés : vainqueur du match (1X2),
   over/under buts, et BTTS (les deux équipes marquent).
3. Filtre toutes les sélections dont la cote est >= ODDS_THRESHOLD.
4. Choisit 3 matchs distincts et écrit le résultat dans pronostics.json,
   dans le format attendu par le site (home, away, date, odds, pick).

Prérequis :
    pip install requests
    export ODDS_API_KEY="ta_cle_api"   (clé gratuite sur https://the-odds-api.com)

Limite honnête : je n'ai pas pu exécuter ce script contre l'API réelle dans cet
environnement (accès réseau restreint côté sandbox). Teste-le avec ta propre clé
avant de l'automatiser. Les marchés combinés type "BTTS + Over 1.5" ne sont pas
toujours proposés comme cote unique par les bookmakers : quand ce n'est pas
disponible, le bot combine deux marchés distincts (BTTS oui + Over 1.5) et
l'indique clairement dans le pronostic, sans inventer de cote combinée.
"""

import json
import os
import sys
from datetime import datetime, timezone
import requests

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4/sports"

# Championnats suivis (clés officielles the-odds-api). Ajoute/retire librement.
LEAGUES = [
    "soccer_france_ligue_one",
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_uefa_champs_league",
]

ODDS_THRESHOLD = 1.5
REGION = "eu"
ODDS_FORMAT = "decimal"
OUTPUT_FILE = "pronostics.json"
NB_PICKS = 3


def fetch_odds(league):
    """Récupère les cotes 1X2, totals (over/under) et btts pour un championnat."""
    url = f"{BASE_URL}/{league}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": REGION,
        "markets": "h2h,totals,btts",
        "oddsFormat": ODDS_FORMAT,
    }
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code != 200:
        print(f"[warn] {league}: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return []
    return resp.json()


def best_outcome(market, min_odds):
    """Retourne l'outcome (nom, cote) le plus proche de min_odds, sans jamais
    descendre en dessous — on privilégie la cote la plus basse qui respecte
    encore le seuil, pour rester sur des sélections plus "sûres" tout en
    respectant la contrainte cote >= 1.5."""
    candidates = [o for o in market.get("outcomes", []) if o.get("price", 0) >= min_odds]
    if not candidates:
        return None
    return min(candidates, key=lambda o: o["price"])


def evaluate_match(match):
    """Analyse un match et retourne la meilleure sélection valide trouvée,
    en essayant plusieurs types de marché dans un ordre de préférence."""
    if not match.get("bookmakers"):
        return None

    # On regarde tous les books disponibles et on garde le meilleur candidat trouvé
    best_pick = None

    for bookmaker in match["bookmakers"]:
        markets_by_key = {m["key"]: m for m in bookmaker.get("markets", [])}

        h2h = markets_by_key.get("h2h")
        totals = markets_by_key.get("totals")
        btts = markets_by_key.get("btts")

        # 1) Vainqueur du match (home ou away, jamais le nul pour rester sur un pari simple)
        if h2h:
            for outcome in h2h.get("outcomes", []):
                if outcome["name"] in (match["home_team"], match["away_team"]) and outcome["price"] >= ODDS_THRESHOLD:
                    pick = {
                        "type": "1X2",
                        "label": f"Victoire {outcome['name']}",
                        "odds": outcome["price"],
                    }
                    if not best_pick or pick["odds"] < best_pick["odds"]:
                        best_pick = pick

        # 2) Over 1.5 / Over 2.5 buts
        if totals:
            for outcome in totals.get("outcomes", []):
                if outcome["name"] == "Over" and outcome["price"] >= ODDS_THRESHOLD:
                    line = outcome.get("point")
                    pick = {
                        "type": f"Over {line}",
                        "label": f"Plus de {line} buts",
                        "odds": outcome["price"],
                    }
                    if not best_pick or pick["odds"] < best_pick["odds"]:
                        best_pick = pick

        # 3) BTTS combiné à Over — combo manuel si le book ne propose pas
        #    la cote combinée directement (rare hors combo-markets spécifiques)
        if btts and totals:
            btts_yes = next((o for o in btts.get("outcomes", []) if o["name"] == "Yes"), None)
            over_15 = next((o for o in totals.get("outcomes", []) if o["name"] == "Over" and o.get("point") == 1.5), None)
            if btts_yes and over_15 and btts_yes["price"] >= ODDS_THRESHOLD:
                pick = {
                    "type": "BTTS+Over1.5",
                    "label": "BTTS (oui) + Plus de 1.5 buts — deux paris distincts à combiner",
                    "odds": btts_yes["price"],  # on affiche la cote BTTS, la plus contraignante des deux
                }
                if not best_pick or pick["odds"] < best_pick["odds"]:
                    best_pick = pick

    return best_pick


def build_picks():
    all_candidates = []

    for league in LEAGUES:
        matches = fetch_odds(league)
        for match in matches:
            pick = evaluate_match(match)
            if not pick:
                continue
            all_candidates.append({
                "home": match["home_team"],
                "away": match["away_team"],
                "date": format_date(match.get("commence_time")),
                "odds": f"{pick['odds']:.2f}",
                "pick": pick["label"],
                "league": league,
            })

    # Trie par cote décroissante et garde 3 matchs DIFFÉRENTS (pas 2 picks sur le même match)
    all_candidates.sort(key=lambda c: float(c["odds"]), reverse=True)
    seen_matches = set()
    selection = []
    for c in all_candidates:
        key = (c["home"], c["away"])
        if key in seen_matches:
            continue
        seen_matches.add(key)
        selection.append({k: v for k, v in c.items() if k != "league"})
        if len(selection) == NB_PICKS:
            break

    return selection


def format_date(iso_str):
    if not iso_str:
        return "Date à confirmer"
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone()
    jours = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
    return f"{jours[dt.weekday()]} {dt.day:02d}/{dt.month:02d} — {dt.hour:02d}h{dt.minute:02d}"


def main():
    if not API_KEY:
        print("[erreur] Variable d'environnement ODDS_API_KEY manquante.", file=sys.stderr)
        sys.exit(1)

    picks = build_picks()

    if len(picks) < NB_PICKS:
        print(f"[warn] Seulement {len(picks)}/{NB_PICKS} picks trouvés respectant cote >= {ODDS_THRESHOLD}.", file=sys.stderr)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matches": picks,
        "disclaimer": "Sélections générées automatiquement à partir des cotes du marché. "
                       "Ne constitue pas un conseil financier ni une garantie de gain. "
                       "Le pari comporte des risques, jouez de façon responsable.",
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[ok] {len(picks)} pronostics écrits dans {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
