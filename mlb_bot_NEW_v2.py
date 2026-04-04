def run_bot(mode="picks"):
    webhook, api_key, sheet = get_env()

    if not api_key:
        print("No API key — exiting safely")
        return

    print(f"MODE: {mode}")

    raw_games = get_mlb_games(api_key)

    picks = []

    for game in raw_games:
        home = game.get("home_team")
        away = game.get("away_team")

        for book in game.get("bookmakers", []):
            for market in book.get("markets", []):

                # ======================
                # MONEYLINE
                # ======================
                if market["key"] == "h2h":
                    outcomes = market["outcomes"]

                    for o in outcomes:
                        team = o["name"]
                        odds = o["price"]

                        implied = odds_to_implied_prob(odds)

                        # simple model = slight edge assumption
                        model_prob = implied + 2  

                        edge = calculate_edge(model_prob, implied)

                        picks.append({
                            "team": team,
                            "bet": "ML",
                            "odds": odds,
                            "prob": round(model_prob, 1),
                            "edge": edge
                        })

                # ======================
                # SPREADS
                # ======================
                elif market["key"] == "spreads":
                    for o in market["outcomes"]:
                        team = o["name"]
                        odds = o["price"]
                        point = o["point"]

                        implied = odds_to_implied_prob(odds)
                        model_prob = implied + 2
                        edge = calculate_edge(model_prob, implied)

                        picks.append({
                            "team": team,
                            "bet": f"{point}",
                            "odds": odds,
                            "prob": round(model_prob, 1),
                            "edge": edge
                        })

                # ======================
                # TOTALS
                # ======================
                elif market["key"] == "totals":
                    for o in market["outcomes"]:
                        side = o["name"]  # Over / Under
                        odds = o["price"]
                        point = o["point"]

                        implied = odds_to_implied_prob(odds)
                        model_prob = implied + 2
                        edge = calculate_edge(model_prob, implied)

                        picks.append({
                            "team": f"{side} {point}",
                            "bet": "",
                            "odds": odds,
                            "prob": round(model_prob, 1),
                            "edge": edge
                        })

    # =========================
    # FILTER TOP 5
    # =========================
    picks = sorted(picks, key=lambda x: x["edge"], reverse=True)[:5]

    # =========================
    # UNIT LOGIC
    # =========================
    def get_units(prob):
        if prob >= 65:
            return 2
        elif prob >= 60:
            return 1.5
        else:
            return 1

    # =========================
    # FORMAT MESSAGE
    # =========================
    today = datetime.now().strftime("%B %d")

    message = f"MLB PICKS — {today}\n\n━━━━━━━━━━━━\n\n"

    for p in picks:
        units = get_units(p["prob"])

        message += (
            f"{p['team']} {p['bet']} ({p['odds']})\n"
            f"Win Prob: {p['prob']}% | {units}u\n\n"
        )

    message += "━━━━━━━━━━━━"

    # =========================
    # SEND
    # =========================
    if webhook:
        res = requests.post(webhook, json={"content": message})
        print("Discord status:", res.status_code)

    if sheet:
        sheet.append_row([message])
        print("Wrote to sheet")

    print("DONE")
