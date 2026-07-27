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

MIN_ODDS = 1.60
MAX_ODDS = 2.20


# Products
SINGLE_LIMIT = 3
PREMIUM_LIMIT = 6
VIP_LIMIT = 3


# ==========================
# API REQUEST
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
        f"[INFO] Fixtures found: {len(fixtures)}"
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

def analyse_odds(odds_data):

    if not odds_data:
        return None


    try:
        bookmakers = odds_data[0]["bookmakers"]

    except Exception:
        return None


    candidates = []


    allowed_markets = [
    "Goals Over/Under",
    "Both Teams Score",
    "Double Chance",
    "Match Winner"
]


blocked_markets = [
    "First Half",
    "Second Half",
    "Asian Handicap"
]


    for bookmaker in bookmakers:

        for bet in bookmaker.get("bets", []):

            market = bet.get("name", "")


            # remove first half markets
            if "First Half" in market:
                continue


          if any(
    x in market
    for x in blocked_markets
):
    continue


if not any(
    x in market
    for x in allowed_markets
):
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
                            "market": market,
                            "pick": value.get("value"),
                            "odds": odd,
                            "bookmaker": bookmaker.get("name")
                        }
                    )


    if not candidates:
        return None


    # Prefer value around 1.80
    best = sorted(
        candidates,
        key=lambda x: abs(x["odds"] - 1.80)
    )[0]


    return best

# ==========================
# CONFIDENCE SCORE
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
# BUILD DAILY PICKS
# ==========================

def build_picks():

    fixtures = get_fixtures()

    all_picks = []


    for fixture in fixtures:

        fixture_id = fixture["fixture"]["id"]

        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]


        odds = get_fixture_odds(
            fixture_id
        )


        best = analyse_odds(
            odds
        )


        if not best:
            continue


        confidence = calculate_confidence(
            best
        )


        all_picks.append(
            {
                "home": home,
                "away": away,
                "date": fixture["fixture"]["date"],
                "pick": best["pick"],
                "market": best["market"],
                "bookmaker": best["bookmaker"],
                "odds": f'{best["odds"]:.2f}',
                "confidence": f"{confidence}%"
            }
        )


    # Highest confidence first
    all_picks = sorted(
        all_picks,
        key=lambda x: int(
            x["confidence"].replace("%","")
        ),
        reverse=True
    )


    return {

        "single": all_picks[:SINGLE_LIMIT],

        "premium": all_picks[:PREMIUM_LIMIT],

        "vip": all_picks[:VIP_LIMIT]

    }



# ==========================
# SAVE JSON
# ==========================

def save_predictions(products):

    output = {

        "generated_at":
            datetime.now(timezone.utc).isoformat(),


        "products": products,


        "vip_membership": {

            "monthly_unlocks": 12,

            "picks_per_unlock": 3

        },


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


    save_predictions(
        products
    )


    total = sum(
        len(x)
        for x in products.values()
    )


    print(
        f"[OK] {total} picks generated"
    )



if __name__ == "__main__":

    main()
