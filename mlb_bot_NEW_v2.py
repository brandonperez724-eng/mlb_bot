import sys
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
from datetime import datetime, timezone

from urllib3 import response

print("RUN CHECK")

import os

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK"]
API_KEY = os.environ["API_KEY"]

def calculate_edge(model_prob, implied_prob):
    return round(model_prob - implied_prob, 2)

def get_rating(edge):
    if edge >= 10:
        return 5
    elif edge >= 8:
        return 4
    elif edge >= 6:
        return 3
    elif edge >= 4:
        return 2
    else:
        return 1

def odds_to_implied_prob(odds):
    odds = int(odds)
    if odds < 0:
        return round(abs(odds) / (abs(odds) + 100) * 100, 2)
    else:
        return round(100 / (odds + 100) * 100, 2)


def get_mlb_games(api_key):
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={api_key}&regions=us&markets=h2h&oddsFormat=american"

    response = requests.get(url)
    response.raise_for_status()
    data = response.json()

    games = []

    for game in data:
        home = game["home_team"]
        away = game["away_team"]

        try:
            odds = game["bookmakers"][0]["markets"][0]["outcomes"]

            home_odds = None
            away_odds = None

            for outcome in odds:
                if outcome["name"] == home:
                    home_odds = int(outcome["price"])
                elif outcome["name"] == away:
                    away_odds = int(outcome["price"])

            games.append({
                "game": f"{away} vs {home}",
                "home_team": home,
                "away_team": away,
                "home_odds": home_odds,
                "away_odds": away_odds,
                "commence_time": game.get("commence_time")
            })

        except (KeyError, IndexError, TypeError):
            continue

    return games


def calculate_model_prob(home_team, away_team):
    home = TEAM_SPLITS.get(home_team)
    away = TEAM_SPLITS.get(away_team)

    if not home or not away:
        return None

    league_avg_rpg = 4.6

    home_strength = (
        (home["home_win_pct"] / 100) * 0.65 +
        (home["home_rpg"] / league_avg_rpg) * 0.35
    )

    away_strength = (
        (away["away_win_pct"] / 100) * 0.65 +
        (away["away_rpg"] / league_avg_rpg) * 0.35
    )

    raw_prob = home_strength / (home_strength + away_strength)
    adjusted_prob = raw_prob + 0.02

    adjusted_prob = max(0.01, min(adjusted_prob, 0.99))

    return round(adjusted_prob * 100, 2)


def choose_best_side(game_data):
    home_team = game_data["home_team"]
    away_team = game_data["away_team"]

    home_odds = game_data["home_odds"]
    away_odds = game_data["away_odds"]

    home_model_prob = calculate_model_prob(home_team, away_team)
    if home_model_prob is None:
        return None

    away_model_prob = round(100 - home_model_prob, 2)

    home_implied_prob = odds_to_implied_prob(home_odds)
    away_implied_prob = odds_to_implied_prob(away_odds)

    home_edge = calculate_edge(home_model_prob, home_implied_prob)
    away_edge = calculate_edge(away_model_prob, away_implied_prob)

    if home_edge >= away_edge:
        return {
            "team": home_team,
            "odds": home_odds,
            "model_prob": home_model_prob,
            "implied_prob": home_implied_prob,
            "edge": home_edge,
            "side": "home"
        }
    else:
        return {
            "team": away_team,
            "odds": away_odds,
            "model_prob": away_model_prob,
            "implied_prob": away_implied_prob,
            "edge": away_edge,
            "side": "away"
        }

def normalize_name(name):
    return (
        name.lower()
        .replace("d-backs", "diamondbacks")
        .replace("la ", "los angeles ")
        .replace("ny ", "new york ")
        .replace(".", "")
        .strip()
    )


def grade_bets(sheet, api_key=None):
    print("🚨 ESPN VERSION RUNNING")

    url = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()

    scores = {}

    for event in data.get("events", []):
        competitions = event.get("competitions", [])
        if not competitions:
            continue

        comp = competitions[0]

        if comp.get("status", {}).get("type", {}).get("completed") != True:
            continue

        teams = comp.get("competitors", [])

        home = None
        away = None
        home_score = 0
        away_score = 0

        for team in teams:
            name = team["team"]["displayName"]
            score = int(team["score"])

            if team["homeAway"] == "home":
                home = name
                home_score = score
            else:
                away = name
                away_score = score

        if not home or not away:
            continue

        winner = home if home_score > away_score else away

        # normalize BOTH sides
        key = f"{normalize_name(away)} vs {normalize_name(home)}"
        scores[key] = normalize_name(winner)

    print(f"✅ ESPN returned {len(scores)} completed games")

    rows = sheet.get_all_values()

    updated = 0

    for i, row in enumerate(rows[1:], start=2):
        game = row[1]
        bet = row[2]
        headers = rows[0]
        result_index = headers.index("Result")
        result = row[result_index]

        if not game or result != "":
            continue

        match = None

        game_lower = normalize_name(game)

        for api_game, winner in scores.items():
            if (
                normalize_name(game_lower.split(" vs ")[0]) in api_game and
                normalize_name(game_lower.split(" vs ")[1]) in api_game
            ):
                match = winner
                break

            print("TEAMS:", teams)
            print("API GAME:", api_game)

        if not match:
            print("NO MATCH:", game)
            continue

        print(f"MATCHED: {game} → {match}")

        if normalize_name(match) in normalize_name(bet):
            sheet.update_cell(i, result_index + 1, "W")
        else:
            sheet.update_cell(i, result_index + 1, "L")

        updated += 1   

    print(f"✅ UPDATED {updated} ROWS")

TEAM_SPLITS = {
    "Arizona Diamondbacks": {"home_win_pct": 53.1, "away_win_pct": 45.7, "home_rpg": 4.7, "away_rpg": 4.3},
    "Atlanta Braves": {"home_win_pct": 48.8, "away_win_pct": 45.7, "home_rpg": 4.7, "away_rpg": 4.5},
    "Baltimore Orioles": {"home_win_pct": 48.2, "away_win_pct": 44.4, "home_rpg": 4.6, "away_rpg": 4.2},
    "Boston Red Sox": {"home_win_pct": 59.3, "away_win_pct": 50.6, "home_rpg": 4.8, "away_rpg": 4.5},
    "Chicago Cubs": {"home_win_pct": 61.7, "away_win_pct": 51.9, "home_rpg": 5.0, "away_rpg": 4.6},
    "Chicago White Sox": {"home_win_pct": 40.7, "away_win_pct": 33.3, "home_rpg": 3.9, "away_rpg": 3.5},
    "Cincinnati Reds": {"home_win_pct": 55.6, "away_win_pct": 46.9, "home_rpg": 4.9, "away_rpg": 4.3},
    "Cleveland Guardians": {"home_win_pct": 55.6, "away_win_pct": 53.1, "home_rpg": 4.5, "away_rpg": 4.3},
    "Colorado Rockies": {"home_win_pct": 30.9, "away_win_pct": 22.2, "home_rpg": 4.8, "away_rpg": 3.9},
    "Detroit Tigers": {"home_win_pct": 56.8, "away_win_pct": 50.6, "home_rpg": 4.6, "away_rpg": 4.2},
    "Houston Astros": {"home_win_pct": 56.8, "away_win_pct": 50.6, "home_rpg": 4.8, "away_rpg": 4.5},
    "Kansas City Royals": {"home_win_pct": 53.1, "away_win_pct": 48.2, "home_rpg": 4.4, "away_rpg": 4.1},
    "Los Angeles Angels": {"home_win_pct": 48.2, "away_win_pct": 40.7, "home_rpg": 4.3, "away_rpg": 4.0},
    "Los Angeles Dodgers": {"home_win_pct": 64.2, "away_win_pct": 50.6, "home_rpg": 5.4, "away_rpg": 4.8},
    "Miami Marlins": {"home_win_pct": 46.9, "away_win_pct": 50.6, "home_rpg": 4.2, "away_rpg": 4.3},
    "Milwaukee Brewers": {"home_win_pct": 64.2, "away_win_pct": 55.6, "home_rpg": 4.8, "away_rpg": 4.5},
    "Minnesota Twins": {"home_win_pct": 46.9, "away_win_pct": 39.5, "home_rpg": 4.3, "away_rpg": 4.0},
    "New York Mets": {"home_win_pct": 60.5, "away_win_pct": 42.0, "home_rpg": 4.9, "away_rpg": 4.1},
    "New York Yankees": {"home_win_pct": 61.7, "away_win_pct": 54.3, "home_rpg": 5.1, "away_rpg": 4.7},
    "Athletics": {"home_win_pct": 44.4, "away_win_pct": 49.4, "home_rpg": 4.2, "away_rpg": 4.4},
    "Philadelphia Phillies": {"home_win_pct": 67.9, "away_win_pct": 50.6, "home_rpg": 5.3, "away_rpg": 4.6},
    "Pittsburgh Pirates": {"home_win_pct": 54.3, "away_win_pct": 33.3, "home_rpg": 4.4, "away_rpg": 3.8},
    "San Diego Padres": {"home_win_pct": 64.2, "away_win_pct": 46.9, "home_rpg": 4.9, "away_rpg": 4.3},
    "San Francisco Giants": {"home_win_pct": 51.9, "away_win_pct": 48.1, "home_rpg": 4.4, "away_rpg": 4.2},
    "Seattle Mariners": {"home_win_pct": 63.0, "away_win_pct": 48.8, "home_rpg": 4.7, "away_rpg": 4.2},
    "St. Louis Cardinals": {"home_win_pct": 54.3, "away_win_pct": 42.0, "home_rpg": 4.6, "away_rpg": 4.1},
    "Tampa Bay Rays": {"home_win_pct": 50.6, "away_win_pct": 44.4, "home_rpg": 4.4, "away_rpg": 4.1},
    "Texas Rangers": {"home_win_pct": 59.3, "away_win_pct": 40.7, "home_rpg": 5.2, "away_rpg": 4.1},
    "Toronto Blue Jays": {"home_win_pct": 66.7, "away_win_pct": 49.4, "home_rpg": 4.9, "away_rpg": 4.3},
    "Washington Nationals": {"home_win_pct": 39.5, "away_win_pct": 42.0, "home_rpg": 4.1, "away_rpg": 4.2}
}

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

import os
import json
from oauth2client.service_account import ServiceAccountCredentials

print("ENV VAR EXISTS:", "GOOGLE_CREDENTIALS" in os.environ)

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

client = gspread.authorize(creds)
sheet = client.open("MLB Betting Tracker").worksheet("PICKS")

raw_games = get_mlb_games(API_KEY)

today = datetime.now().strftime("%Y-%m-%d")

def send_daily_recap(sheet, webhook_url):
    records = sheet.get_all_records()

    today = datetime.now().strftime("%Y-%m-%d")

    wins = 0
    losses = 0
    pushes = 0
    total_units = 0

    best_win = 0
    worst_loss = 0

    for row in records:
        result = str(row.get("Result", "")).strip().upper()
        if result not in ["W", "L", "P"]:
            continue

        profit = row.get("Profit", 0) if row.get("Profit", "") != "" else 0

        try:
            profit = float(profit)
        except:
            profit = 0

        if result == "W":
            wins += 1
            if profit > best_win:
                best_win = profit
        elif result == "L":
            losses += 1
            if profit < worst_loss:
                worst_loss = profit
        elif result == "P":
            pushes += 1

        total_units += profit

    if wins + losses + pushes == 0:
        return  # no games yet

    message = f"""
DAILY RECAP

{wins}-{losses}{f"-{pushes}" if pushes else ''}
Units: {round(total_units, 2)}

Best: +{round(best_win, 2)}
Worst: {round(worst_loss, 2)}
"""

    requests.post(webhook_url, json={"content": message})

mode = "picks"

if len(sys.argv) > 1:
    mode = sys.argv[1]

if mode == "grade":
    print("🌙 RUNNING GRADING MODE")
    grade_bets(sheet)
    send_daily_recap(sheet, WEBHOOK_URL)

else:
    print("☀️ RUNNING PICKS MODE")

    games = []
    for g in raw_games:
        if not g.get("commence_time"):
            continue

        game_time = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        if game_time < datetime.now(timezone.utc):
            continue

        if g["home_odds"] is None or g["away_odds"] is None:
            continue

        best_side = choose_best_side(g)

        if best_side is None or best_side["edge"] < 4:
            continue

        games.append({
            "date": today,
            "game": g["game"],
            "bet": f"{best_side['team']} ML",
            "odds": best_side["odds"],
            "model_prob": best_side["model_prob"],
            "implied_prob": best_side["implied_prob"],
            "edge": best_side["edge"],
            "stake": 1,
            "notes": f"Split-based model ({best_side['side']})"
        })

    picks = []

    for game in games:
        odds = game["odds"]
        display_odds = f"+{odds}" if odds > 0 else str(odds)

        pick = {
            **game,
            "bet": f"{game['bet']} ({display_odds})",
            "odds": display_odds,
            "rating": get_rating(game["edge"])
        }

        picks.append(pick)

    for pick in picks:
        odds = pick["odds"]
        edge = pick["edge"]

        if edge >= 8:
            title = "TOP PLAY"
        elif edge >= 6:
            title = "STRONG PLAY"
        else:
            title = "VALUE PLAY"

        stars = "★" * pick["rating"]
        tag = "DOG" if "+" in odds else "FAV"

        embed = {
            "title": title,
            "description": (
                f"**{pick['bet']}**\n"
                f"{pick['game']}\n\n"
                f"+{edge}% | {odds} | {stars}\n"
                f"{tag}\n\n"
                f"{pick['model_prob']}% vs {pick['implied_prob']}%\n\n"
                f"{pick['notes']}"
            ),
            "color": 15105570,
            "footer": {
                "text": f"{pick['stake']}u • {datetime.now().strftime('%I:%M %p')}"
            }
        }

        discord_response = requests.post(WEBHOOK_URL, json={"embeds": [embed]})

        print("DISCORD STATUS:", discord_response.status_code)
        print("DISCORD RESPONSE:", discord_response.text)

        sheet.append_row([
            pick["date"],
            pick["game"],
            pick["bet"],
            pick["odds"],
            pick["model_prob"],
            pick["implied_prob"],
            pick["edge"],
            pick["rating"],
            pick["stake"],
            "",
            "",
            "",
            ""
        ])

        print("SHEET WRITE SUCCESS")

        print("LOGGED TO SHEET:", pick["game"])

        time.sleep(1)

print("✅ Finished")

def run_picks():
    # your picks logic here
    pass

if __name__ == "__main__":
    run_picks()
