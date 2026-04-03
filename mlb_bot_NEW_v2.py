import sys
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
from datetime import datetime, timezone
import os
import json

print("RUN CHECK")


# =========================
# ENV + SETUP
# =========================
def get_env():
    webhook = os.environ.get("DISCORD_WEBHOOK")
    api_key = os.environ.get("ODDS_API_KEY")
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")

    if not webhook:
        print("Missing DISCORD_WEBHOOK")
    if not api_key:
        print("Missing ODDS_API_KEY")
    if not creds_json:
        print("Missing GOOGLE_CREDENTIALS")

    sheet = None

    if creds_json:
        creds_dict = json.loads(creds_json)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open("MLB Betting Tracker").sheet1

    return webhook, api_key, sheet


# =========================
# CORE HELPERS
# =========================
def calculate_edge(model_prob, implied_prob):
    return round(model_prob - implied_prob, 2)


def odds_to_implied_prob(odds):
    odds = int(odds)
    if odds < 0:
        return round(abs(odds) / (abs(odds) + 100) * 100, 2)
    else:
        return round(100 / (odds + 100) * 100, 2)


# =========================
# API
# =========================
def get_mlb_games(api_key):
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={api_key}&regions=us&markets=h2h&oddsFormat=american"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


# =========================
# MAIN BOT RUNNER
# =========================
def run_bot(mode="picks"):
    webhook, api_key, sheet = get_env()

    if not api_key:
        print("No API key — exiting safely")
        return

    print(f"MODE: {mode}")

    if mode == "grade":
        print("RUNNING GRADING MODE (placeholder)")
        return

    print("RUNNING PICKS MODE")

    raw_games = get_mlb_games(api_key)

    for game in raw_games[:2]:  # limit for testing
        message = f"{game.get('away_team')} vs {game.get('home_team')}"

        if webhook:
            res = requests.post(webhook, json={"content": message})
            print("Discord status:", res.status_code)

        if sheet:
            sheet.append_row([message])
            print("Wrote to sheet")

    print("DONE")


# =========================
# ENTRYPOINT
# =========================
def run_picks():
    run_bot("picks")


if __name__ == "__main__":
    mode = "picks"

    if len(sys.argv) > 1:
        mode = sys.argv[1]

    run_bot(mode)
