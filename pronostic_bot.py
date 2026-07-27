import json
import os
import sys
from datetime import datetime, timezone

import requests

# ==========================
# CONFIGURATION
# ==========================
# Utilise the-odds-api.com (plan gratuit : 500 requêtes/mois, aucune carte requise).
# Clé à créer sur https://the-odds-api.com/ puis à stocker dans le secret GitHub ODDS_API_KEY.

API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"
OUTPUT_FILE = "pronostics.json"

# Ligues suivies (clés "sport_key" de the-odds-api.com). On en garde peu pour
# rester confortablement sous le quota gratuit de 500 requêtes/mois :
# 6 ligues x 2 marchés x 1 region = 12 crédits/jour ~= 360/mois.
LEAGUES = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
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

def get_league_odds(sport_key):
    """Fetch upcoming events + odds for one league. Returns [] on any
    problem instead of raising, and logs the reason so the workflow
    log tells us exactly what happened."""
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
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
    all_picks = []
    reason_counts = {}
    total_events = 0

    for sport_key in LEAGUES:
        events = get_league_odds(sport_key)
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
