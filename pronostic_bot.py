import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

# ==========================
# CONFIGURATION
# ==========================
# Utilise the-odds-api.com (plan gratuit : 500 requêtes/mois, aucune carte requise).
# Clé à créer sur https://the-odds-api.com/ puis à stocker dans le secret GitHub ODDS_API_KEY.

API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"
OUTPUT_FILE = "pronostics.json"

# Ligues suivies (clés "sport_key" de the-odds-api.com). On couvre plusieurs
# continents pour éviter de tomber à sec pendant la trêve estivale européenne
# (juillet-août) : les requêtes qui ne renvoient aucun événement ne comptent
# PAS dans le quota gratuit, donc élargir la liste ne coûte quasi rien les
# jours où une ligue ne joue pas. On reste prudent quand même : si beaucoup
# de ces ligues jouent le même jour, la conso peut monter (chaque appel avec
# résultat coûte 2 crédits : 2 marchés x 1 région). Surveille les lignes
# [DEBUG] Quota after ... dans les logs pour ajuster si besoin.
LEAGUES = [
    # Europe (grands championnats)
    "soccer_epl",
    "soccer_efl_champ",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    # Europe (en saison l'été : Scandinavie)
    "soccer_sweden_allsvenskan",
    "soccer_norway_eliteserien",
    "soccer_denmark_superliga",
    # Amériques (en saison l'été)
    "soccer_usa_mls",
    "soccer_mexico_ligamx",
    "soccer_brazil_campeonato",
    "soccer_argentina_primera_division",
    "soccer_conmebol_copa_libertadores",
    # Asie (en saison l'été)
    "soccer_japan_j_league",
    "soccer_korea_kleague1",
    "soccer_china_superleague",
]

REGIONS = "eu"
MARKETS = "h2h,totals"

# Odds range
MIN_ODDS = 1.60
MAX_ODDS = 2.20

# Product sizes
SINGLE_LIMIT = 3
PREMIUM_LIMIT = 6
VIP_LIMIT = 3

MARKET_LABELS = {
    "h2h": "Match Winner",
    "totals": "Goals Over/Under",
}


# ==========================
# API REQUEST
# ==========================

def get_active_leagues():
    """Ask the (free, quota-exempt) /sports endpoint which competitions are
    currently in season, and only keep the ones we're tracking. Avoids
    spending calls/log noise on leagues that are on their off-season break."""
    url = f"{BASE_URL}/sports"
    try:
        response = requests.get(url, params={"apiKey": API_KEY}, timeout=20)
    except requests.RequestException as e:
        print(f"[ERROR] Network error on /sports: {e}", file=sys.stderr)
        return list(LEAGUES)  # repli : on tente toutes les ligues suivies

    if response.status_code != 200:
        print(f"[ERROR] HTTP {response.status_code} on /sports: {response.text}", file=sys.stderr)
        return list(LEAGUES)

    in_season = {s["key"] for s in response.json() if s.get("key")}
    active = [key for key in LEAGUES if key in in_season]
    skipped = [key for key in LEAGUES if key not in in_season]

    print(f"[INFO] {len(active)}/{len(LEAGUES)} ligue(s) suivie(s) en saison")
    if skipped:
        print(f"[INFO] Ligues ignorées (hors saison) : {', '.join(skipped)}")

    return active


def get_league_odds(sport_key, time_from, time_to):
    """Fetch today's events + odds for one league (window is UTC
    [time_from, time_to)). Returns [] on any problem instead of
    raising, and logs the reason so the workflow log tells us exactly
    what happened."""
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
        "commenceTimeFrom": time_from,
        "commenceTimeTo": time_to,
    }

    try:
        response = requests.get(url, params=params, timeout=20)
    except requests.RequestException as e:
        print(f"[ERROR] Network error on {sport_key}: {e}", file=sys.stderr)
        return []

    remaining = response.headers.get("x-requests-remaining")
    used = response.headers.get("x-requests-used")
    if remaining is not None:
        print(f"[DEBUG] Quota after {sport_key}: used={used}, remaining={remaining}")

    if response.status_code != 200:
        print(f"[ERROR] HTTP {response.status_code} on {sport_key}: {response.text}", file=sys.stderr)
        return []

    events = response.json()
    print(f"[INFO] {sport_key}: {len(events)} event(s) returned")
    return events


# ==========================
# ANALYSE ODDS
# ==========================

def analyse_event(event):
    """Return the best (odds closest to 1.80) candidate pick within
    MIN_ODDS-MAX_ODDS across h2h/totals markets, or None with a reason."""
    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return None, "no_bookmakers"

    candidates = []

    for bookmaker in bookmakers:
        for market in bookmaker.get("markets", []):
            market_key = market.get("key")
            market_label = MARKET_LABELS.get(market_key, market_key)

            for outcome in market.get("outcomes", []):
                odd = outcome.get("price")
                if odd is None:
                    continue
                try:
                    odd = float(odd)
                except (TypeError, ValueError):
                    continue

                if not (MIN_ODDS <= odd <= MAX_ODDS):
                    continue

                if market_key == "totals":
                    point = outcome.get("point")
                    pick_label = f"{outcome.get('name')} {point}" if point is not None else outcome.get("name")
                else:
                    pick_label = outcome.get("name")

                candidates.append({
                    "market": market_label,
                    "pick": pick_label,
                    "odds": odd,
                    "bookmaker": bookmaker.get("title"),
                })

    if not candidates:
        return None, "no_odds_in_range"

    best = sorted(candidates, key=lambda x: abs(x["odds"] - 1.80))[0]
    return best, "ok"


# ==========================
# CONFIDENCE
# ==========================

def calculate_confidence(pick):
    odds = pick["odds"]
    if odds <= 1.70:
        return 82
    elif odds <= 1.90:
        return 78
    else:
        return 74


# ==========================
# BUILD PICKS
# ==========================

def build_picks():
    now = datetime.now(timezone.utc)
    window_start = now.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=48)
    time_from = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    time_to = window_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[INFO] Fenêtre (48h, UTC) : {time_from} -> {time_to}")

    all_picks = []
    reason_counts = {}
    total_events = 0

    active_leagues = get_active_leagues()

    for sport_key in active_leagues:
        events = get_league_odds(sport_key, time_from, time_to)
        total_events += len(events)

        for event in events:
            best, reason = analyse_event(event)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

            if not best:
                continue

            confidence = calculate_confidence(best)

            all_picks.append({
                "home": event.get("home_team", ""),
                "away": event.get("away_team", ""),
                "date": event.get("commence_time", ""),
                "pick": best["pick"],
                "market": best["market"],
                "bookmaker": best["bookmaker"],
                "odds": f'{best["odds"]:.2f}',
                "confidence": f"{confidence}%",
            })

    print(f"[INFO] Odds analysis breakdown across {total_events} event(s): {reason_counts}")
    print(f"[INFO] Total usable picks found: {len(all_picks)}")

    all_picks = sorted(all_picks, key=lambda x: int(x["confidence"].replace("%", "")), reverse=True)

    return {
        "single": all_picks[:SINGLE_LIMIT],
        "premium": all_picks[:PREMIUM_LIMIT],
        "vip": all_picks[:VIP_LIMIT],
    }


# ==========================
# SAVE JSON
# ==========================

def save_predictions(products):
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "products": products,
        "vip_membership": {
            "monthly_unlocks": 12,
            "picks_per_unlock": 3,
        },
        "disclaimer": "Automatic football selections generated from market odds. Responsible betting only.",
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)


# ==========================
# MAIN
# ==========================

def main():
    if not API_KEY:
        print("[ERROR] ODDS_API_KEY missing", file=sys.stderr)
        sys.exit(1)

    products = build_picks()
    save_predictions(products)

    total = sum(len(x) for x in products.values())
    print(f"[OK] {total} picks generated")


if __name__ == "__main__":
    main()
