import json
import os
import sys
from datetime import datetime, timezone
import requests


# ==========================
# CONFIGURATION
# ==========================

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")

BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY
}

OUTPUT_FILE = "pronostics.json"

SINGLE_PICKS = 3
PREMIUM_PICKS = 6
VIP_PICKS = 12

MIN_ODDS = 1.50
MAX_ODDS = 2.50


# ==========================
# API REQUESTS
# ==========================

def api_request(endpoint, params=None):

    url = f"{BASE_URL}/{endpoint}"

    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=20
    )

    if response.status_code != 200:
        print(
            f"[ERROR] API {response.status_code}: {response.text}",
            file=sys.stderr
        )
        return []

    data = response.json()

    return data.get("response", [])



# ==========================
# FIXTURES
# ==========================

def get_fixtures():

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fixtures = api_request(
        "fixtures",
        {
            "date": today
        }
    )

    print(
        f"[INFO] {len(fixtures)} fixtures found today"
    )

    return fixtures



# ==========================
# ODDS
# ==========================

def get_fixture_odds(fixture_id):

    return api_request(
        "odds",
        {
            "fixture": fixture_id
        }
    )



# ==========================
# PICK ANALYSIS
# ==========================

def extract_best_pick(odds_data):

    if not odds_data:
        return None


    try:
        bookmakers = odds_data[0]["bookmakers"]

    except Exception:
        return None


    candidates = []


    for bookmaker in bookmakers:

        for bet in bookmaker.get("bets", []):

            market_name = bet.get("name", "")

            # Keep only useful football markets
            allowed_markets = [
                "Goals Over/Under",
                "Both Teams Score",
                "Double Chance",
                "Match Winner"
            ]

            if not any(m in market_name for m in allowed_markets):
                continue


            for value in bet.get("values", []):

                odd = value.get("odd")

                if odd is None:
                    continue


                try:
                    odd = float(odd)

                except:
                    continue


                if MIN_ODDS <= odd <= MAX_ODDS:

                    candidates.append(
                        {
                            "bookmaker": bookmaker["name"],
                            "market": market_name,
                            "pick": value["value"],
                            "odds": odd
                        }
                    )


    if not candidates:
        return None


    # Select safest valid odd
    best = sorted(
        candidates,
        key=lambda x: x["odds"]
    )[0]


    # Confidence score
    if best["odds"] <= 1.70:
        confidence = 85

    elif best["odds"] <= 2.00:
        confidence = 78

    else:
        confidence = 72


    best["confidence"] = confidence


    return best



# ==========================
# BUILD PICKS
# ==========================

def build_picks():

    fixtures = get_fixtures()

    picks = []


    for fixture in fixtures:

        fixture_id = fixture["fixture"]["id"]

        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]


        odds = get_fixture_odds(
            fixture_id
        )


        best = extract_best_pick(
            odds
        )


        if not best:
            continue


        picks.append(
            {
                "home": home,
                "away": away,
                "date": fixture["fixture"]["date"],
                "pick": best["pick"],
                "market": best["market"],
                "bookmaker": best["bookmaker"],
                "odds": f'{best["odds"]:.2f}',
                "confidence": f'{best["confidence"]}%'
            }
        )


        print(
            f"[PICK] {home} vs {away} "
            f"{best['pick']} "
            f"{best['odds']} "
            f"{best['confidence']}%"
        )


    # Rank by confidence
    picks = sorted(
        picks,
        key=lambda x: int(x["confidence"].replace("%","")),
        reverse=True
    )


    return {
        "single": picks[:SINGLE_PICKS],
        "premium": picks[:PREMIUM_PICKS],
        "vip": picks[:VIP_PICKS]
    }



# ==========================
# MAIN
# ==========================

def main():

    if not API_KEY:

        print(
            "[ERROR] API_FOOTBALL_KEY missing",
            file=sys.stderr
        )

        sys.exit(1)


    products = build_picks()


    output = {

        "generated_at":
            datetime.now(timezone.utc).isoformat(),

        "products":
            products,

        "disclaimer":
            "Automatic football selections generated from market odds. Responsible betting only."

    }


    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            indent=2,
            ensure_ascii=False
        )


    total = sum(
        len(v) for v in products.values()
    )


    print(
        f"[OK] {total} picks saved"
    )



if __name__ == "__main__":
    main()
