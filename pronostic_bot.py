import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

# ==========================
# CONFIGURATION
# ==========================

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
OUTPUT_FILE = "pronostics.json"

# Odds range
MIN_ODDS = 1.60
MAX_ODDS = 2.20

# How many days ahead to look for fixtures (today + N-1 more days)
DAYS_AHEAD = 2

# Product sizes
SINGLE_LIMIT = 3
PREMIUM_LIMIT = 6
VIP_LIMIT = 3

ALLOWED_MARKETS = ["Goals Over/Under", "Both Teams Score", "Double Chance", "Match Winner"]
BLOCKED_MARKETS = ["First Half", "Second Half", "Asian Handicap"]


# ==========================
# API REQUEST
# ==========================

def api_request(endpoint, params=None):
    """Call the API-Football endpoint and surface any API-level errors,
    not just HTTP-level ones. A 200 response can still carry an
    'errors' payload (e.g. plan restrictions, rate limits)."""
    url = f"{BASE_URL}/{endpoint}"
    response = requests.get(url, headers=HEADERS, params=params, timeout=20)

    if response.status_code != 200:
        print(f"[ERROR] HTTP {response.status_code} on /{endpoint}: {response.text}", file=sys.stderr)
        return []

    data = response.json()

    # api-football returns 200 even when access is denied for a plan/endpoint;
    # the actual reason lives in "errors" (dict or list depending on the error type).
    errors = data.get("errors")
    if errors:
        print(f"[WARN] API errors on /{endpoint}: {errors}", file=sys.stderr)

    results = data.get("response", [])
    print(f"[DEBUG] /{endpoint} -> {data.get('results', len(results))} result(s) "
          f"(quota used today: {data.get('paging', {})})", file=sys.stderr)

    return results


# ==========================
# FIXTURES
# ==========================

def get_fixtures():
    """Fetch fixtures for today plus the next DAYS_AHEAD-1 days, keeping
    only matches that haven't started yet (no point pulling odds for
    matches already live or finished)."""
    all_fixtures = []

    for offset in range(DAYS_AHEAD):
        day = (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")
        fixtures = api_request("fixtures", {"date": day})
        print(f"[INFO] {len(fixtures)} fixture(s) on {day}")
        all_fixtures.extend(fixtures)

    not_started = [f for f in all_fixtures if f.get("fixture", {}).get("status", {}).get("short") == "NS"]
    print(f"[INFO] Fixtures found: {len(all_fixtures)} total, {len(not_started)} not-yet-started")

    return not_started


# ==========================
# ODDS
# ==========================

def get_fixture_odds(fixture_id):
    return api_request("odds", {"fixture": fixture_id})


# ==========================
# ANALYSE ODDS
# ==========================

def analyse_odds(odds_data):
    """Return the best (odds closest to 1.80) candidate pick within
    MIN_ODDS-MAX_ODDS across the allowed markets, or None with a reason."""
    if not odds_data:
        return None, "no_odds_data"

    try:
        bookmakers = odds_data[0]["bookmakers"]
    except Exception:
        return None, "no_bookmakers_field"

    if not bookmakers:
        return None, "empty_bookmakers"

    candidates = []

    for bookmaker in bookmakers:
        for bet in bookmaker.get("bets", []):
            market = bet.get("name", "")

            if any(x in market for x in BLOCKED_MARKETS):
                continue
            if not any(x in market for x in ALLOWED_MARKETS):
                continue

            for value in bet.get("values", []):
                odd = value.get("odd")
                if odd is None:
                    continue
                try:
                    odd = float(odd)
                except (TypeError, ValueError):
                    continue

                if MIN_ODDS <= odd <= MAX_ODDS:
                    candidates.append({
                        "market": market,
                        "pick": value.get("value"),
                        "odds": odd,
                        "bookmaker": bookmaker.get("name"),
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
    fixtures = get_fixtures()

    all_picks = []
    reason_counts = {}

    for fixture in fixtures:
        fixture_id = fixture["fixture"]["id"]
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]

        odds = get_fixture_odds(fixture_id)
        best, reason = analyse_odds(odds)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if not best:
            continue

        confidence = calculate_confidence(best)

        all_picks.append({
            "home": home,
            "away": away,
            "date": fixture["fixture"]["date"],
            "pick": best["pick"],
            "market": best["market"],
            "bookmaker": best["bookmaker"],
            "odds": f'{best["odds"]:.2f}',
            "confidence": f"{confidence}%",
        })

    print(f"[INFO] Odds analysis breakdown across {len(fixtures)} fixture(s): {reason_counts}")
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
        print("[ERROR] API_FOOTBALL_KEY missing", file=sys.stderr)
        sys.exit(1)

    products = build_picks()
    save_predictions(products)

    total = sum(len(x) for x in products.values())
    print(f"[OK] {total} picks generated")


if __name__ == "__main__":
    main()
