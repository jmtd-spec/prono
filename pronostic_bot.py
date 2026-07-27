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
    export API_FOOTBALL_KEY="ta_cle_api"   (clé gratuite sur https://the-odds-api.com)

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
from datetime import datetime, timedelta, timezone
import requests

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4/sports"

# Championnats suivis : découverts automatiquement (voir get_active_soccer_leagues),
# pour ne jamais dépendre d'une liste figée qui tomberait hors-saison
# (ex: les grands championnats européens sont en pause l'été).
SPORTS_ENDPOINT = "https://api.the-odds-api.com/v4/sports"

ODDS_THRESHOLD = 1.5
ODDS_MAX = 3.0          # au-delà, ce n'est plus un "pronostic sûr" mais un pari risqué
REGION = "eu"
ODDS_FORMAT = "decimal"
OUTPUT_FILE = "pronostics.json"
NB_PICKS = 3


def get_active_soccer_leagues():
    """Interroge l'API pour la liste de tous les sports/championnats, et ne garde
    que les championnats de foot actuellement EN SAISON (active=true). Ça évite
    de dépendre d'une liste figée qui tomberait hors-saison (ex: championnats
    européens en pause l'été alors que la MLS, le Brésil, le Mexique, etc.
    jouent toute l'année ou à des périodes différentes)."""
    resp = requests.get(SPORTS_ENDPOINT, params={"apiKey": API_KEY}, timeout=15)
    if resp.status_code != 200:
        print(f"[erreur] Impossible de récupérer la liste des championnats: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return []

    all_sports = resp.json()
    active_leagues = [
        s["key"] for s in all_sports
        if s.get("key", "").startswith("soccer_") and s.get("active")
    ]
    print(f"[info] {len(active_leagues)} championnats de foot actifs trouvés: {active_leagues}")
    return active_leagues


def fetch_odds(league):
    """Récupère les cotes pour un championnat. h2h/totals sont demandés ensemble
    (largement supportés). btts est demandé séparément et fusionné : s'il n'est
    pas disponible pour ce championnat/plan API, on continue quand même avec
    les autres marchés au lieu de tout faire échouer."""
    core = _fetch_markets(league, "h2h,totals")
    if core is None:
        return []

    btts_data = _fetch_markets(league, "btts")
    if btts_data:
        btts_by_id = {m["id"]: m for m in btts_data}
        for match in core:
            btts_match = btts_by_id.get(match["id"])
            if not btts_match:
                continue
            btts_books = {b["key"]: b for b in btts_match.get("bookmakers", [])}
            for bookmaker in match.get("bookmakers", []):
                btts_book = btts_books.get(bookmaker["key"])
                if btts_book:
                    for m in btts_book.get("markets", []):
                        if m["key"] == "btts":
                            bookmaker["markets"].append(m)

    return core


def _fetch_markets(league, markets):
    url = f"{BASE_URL}/{league}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": REGION,
        "markets": markets,
        "oddsFormat": ODDS_FORMAT,
    }
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code != 200:
        print(f"[warn] {league} ({markets}): {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return None if markets != "btts" else []
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


def in_range(price):
    """Une cote n'est un 'pronostic sûr' que si elle est ni trop basse (sous le
    seuil demandé) ni trop haute (pari trop risqué pour être présenté comme
    une sélection safe)."""
    return ODDS_THRESHOLD <= price <= ODDS_MAX


def evaluate_match(match):
    """Analyse un match et retourne la meilleure sélection valide, en
    priorisant les marchés Over/BTTS (statistiquement plus stables) et en
    n'utilisant le 1X2 qu'en dernier recours — sinon on finit par recommander
    l'outsider juste parce que le favori est sous la cote minimum, ce qui est
    un pari risqué déguisé en 'valeur sûre'."""
    if not match.get("bookmakers"):
        return None

    over_15_candidates, over_25_candidates, btts_candidates, h2h_candidates = [], [], [], []

    for bookmaker in match["bookmakers"]:
        markets_by_key = {m["key"]: m for m in bookmaker.get("markets", [])}

        totals = markets_by_key.get("totals")
        btts = markets_by_key.get("btts")
        h2h = markets_by_key.get("h2h")

        if totals:
            for outcome in totals.get("outcomes", []):
                if outcome["name"] != "Over" or not in_range(outcome["price"]):
                    continue
                line = outcome.get("point")
                target = over_15_candidates if line == 1.5 else (over_25_candidates if line == 2.5 else None)
                if target is not None:
                    target.append({"type": f"Over {line}", "label": f"Plus de {line} buts", "odds": outcome["price"]})

        if btts:
            for outcome in btts.get("outcomes", []):
                if outcome["name"] == "Yes" and in_range(outcome["price"]):
                    btts_candidates.append({"type": "BTTS", "label": "Les deux équipes marquent", "odds": outcome["price"]})

        if h2h:
            for outcome in h2h.get("outcomes", []):
                if outcome["name"] in (match["home_team"], match["away_team"]) and in_range(outcome["price"]):
                    h2h_candidates.append({"type": "1X2", "label": f"Victoire {outcome['name']}", "odds": outcome["price"]})

    # Ordre de priorité: marchés plus stables d'abord, 1X2 en dernier recours.
    # Dans chaque marché, on garde la cote la plus basse (donc la plus "sûre"
    # parmi celles qui respectent quand même le minimum demandé).
    for pool in (over_15_candidates, over_25_candidates, btts_candidates, h2h_candidates):
        if pool:
            return min(pool, key=lambda p: p["odds"])

    return None


def build_picks():
    all_candidates = []
    leagues = get_active_soccer_leagues()

    if not leagues:
        print("[erreur] Aucun championnat de foot actif trouvé — vérifie ta clé API.", file=sys.stderr)
        return []

    window_end = end_of_upcoming_weekend()

    for league in leagues:
        matches = fetch_odds(league)
        for match in matches:
            if not within_window(match.get("commence_time"), window_end):
                continue
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
                "_commence": match.get("commence_time"),
            })

    if not all_candidates:
        print("[warn] Aucun match aujourd'hui ou ce week-end respectant les critères.", file=sys.stderr)

    # Matchs les plus proches en premier (des pronostics "du moment", pas dans un mois)
    all_candidates.sort(key=lambda c: c["_commence"] or "")
    seen_matches = set()
    selection = []
    for c in all_candidates:
        key = (c["home"], c["away"])
        if key in seen_matches:
            continue
        seen_matches.add(key)
        selection.append({k: v for k, v in c.items() if k not in ("league", "_commence")})
        if len(selection) == NB_PICKS:
            break

    return selection


def end_of_upcoming_weekend():
    """Calcule la fin du week-end en cours ou à venir : minuit le dimanche
    (heure UTC — si tu veux un fuseau précis, ajuste ici). Si on est déjà
    dimanche, ça couvre jusqu'à la fin de la journée en cours."""
    now = datetime.now(timezone.utc)
    days_until_sunday = (6 - now.weekday()) % 7  # lundi=0 ... dimanche=6
    sunday = now + timedelta(days=days_until_sunday)
    return sunday.replace(hour=23, minute=59, second=59, microsecond=0)


def within_window(iso_str, window_end):
    if not iso_str:
        return False
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    return now <= dt <= window_end


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
