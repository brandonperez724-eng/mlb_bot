"""
mlb_bot_NEW_v2.py
-----------------
Two modes:
  picks   → Morning: fetch odds, +EV picks, send to Discord, log to Sheets
  results → Evening: grade picks vs scores, update Sheets, send recap
"""
# comment
import os, json, requests
from datetime import datetime, timezone
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ── Constants ──────────────────────────────────────────────────────────────
ODDS_API_BASE    = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
SCORES_API_BASE  = "https://api.the-odds-api.com/v4/sports/baseball_mlb/scores"
MARKETS          = "h2h,spreads,totals"
REGIONS          = "us"
ODDS_FORMAT      = "american"
TOP_N            = 5
MIN_EDGE         = 2.0          # minimum edge % to qualify
PICKS_CACHE_FILE = "/tmp/mlb_picks_cache.json"
SHEET_TAB_PICKS  = "Picks"
SHEET_TAB_RECORD = "Record"


# ── Environment / Auth ─────────────────────────────────────────────────────
def get_env():
    webhook   = os.environ.get("DISCORD_WEBHOOK")
    api_key   = os.environ.get("ODDS_API_KEY")
    sheet_id  = os.environ.get("GOOGLE_SHEET_ID")
    creds_raw = os.environ.get("GOOGLE_CREDS_JSON")   # full service-account JSON as a string

    ws_picks = ws_record = None

    if sheet_id and creds_raw:
        try:
            scope  = ["https://spreadsheets.google.com/feeds",
                      "https://www.googleapis.com/auth/drive"]
            creds  = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_raw), scope)
            client = gspread.authorize(creds)
            ss     = client.open_by_key(sheet_id)

            # Picks tab
            try:
                ws_picks = ss.worksheet(SHEET_TAB_PICKS)
            except gspread.exceptions.WorksheetNotFound:
                ws_picks = ss.add_worksheet(title=SHEET_TAB_PICKS, rows=1000, cols=12)
                ws_picks.append_row(["Date","Game","Bet Type","Pick",
                                     "Odds","Implied Prob %","Fair Prob %","Edge %",
                                     "Units","Result","P&L (units)","Notes"])

            # Record tab
            try:
                ws_record = ss.worksheet(SHEET_TAB_RECORD)
            except gspread.exceptions.WorksheetNotFound:
                ws_record = ss.add_worksheet(title=SHEET_TAB_RECORD, rows=500, cols=8)
                ws_record.append_row(["Date","Wins","Losses","Pushes",
                                      "Units Wagered","Day P&L","Running Total P&L","Notes"])
        except Exception as e:
            print(f"[SHEETS ERROR] {e}")

    return webhook, api_key, ws_picks, ws_record


# ── Odds Math ──────────────────────────────────────────────────────────────
def american_to_implied(odds: int) -> float:
    """American odds → raw implied probability % (includes vig)."""
    if odds > 0:
        return 100 / (odds + 100) * 100
    return abs(odds) / (abs(odds) + 100) * 100

def remove_vig(prob_a: float, prob_b: float):
    """Normalize two implied probs to sum to 100 (no-vig fair odds)."""
    total = prob_a + prob_b
    return (prob_a / total) * 100, (prob_b / total) * 100

def calculate_ev(fair_prob: float, odds: int) -> float:
    """Edge = fair_prob − implied_prob (both as %)."""
    return round(fair_prob - american_to_implied(odds), 2)

def get_units(edge: float, fair_prob: float) -> float:
    if edge >= 8 and fair_prob >= 65: return 2.0
    if edge >= 5 and fair_prob >= 60: return 1.5
    return 1.0


# ── Odds API ───────────────────────────────────────────────────────────────
def get_mlb_odds(api_key: str) -> list:
    resp = requests.get(ODDS_API_BASE, params={
        "apiKey": api_key, "regions": REGIONS,
        "markets": MARKETS, "oddsFormat": ODDS_FORMAT, "dateFormat": "iso"
    }, timeout=15)
    resp.raise_for_status()
    print(f"[API] Requests remaining: {resp.headers.get('x-requests-remaining','?')}")
    return resp.json()

def get_mlb_scores(api_key: str, days_back: int = 1) -> list:
    resp = requests.get(SCORES_API_BASE, params={
        "apiKey": api_key, "daysFrom": days_back, "dateFormat": "iso"
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Picks Engine ───────────────────────────────────────────────────────────
def build_picks(games: list) -> list:
    candidates = []

    for game in games:
        home    = game.get("home_team", "")
        away    = game.get("away_team", "")
        game_id = game.get("id", "")

        # Aggregate best line per outcome across all bookmakers
        best_lines = {}   # (market_key, name, point) → best odds
        for book in game.get("bookmakers", []):
            for market in book.get("markets", []):
                mkey = market["key"]
                for o in market.get("outcomes", []):
                    ckey = (mkey, o["name"], o.get("point"))
                    curr = best_lines.get(ckey, -10000)
                    if o["price"] > curr:
                        best_lines[ckey] = o["price"]

        seen = set()
        for (mkey, name, point), odds in best_lines.items():

            # Find opposing outcome
            if mkey == "h2h":
                opp_name = home if name == away else away
                opp_key  = (mkey, opp_name, None)
            elif mkey == "spreads":
                opp_name = home if name == away else away
                opp_key  = next((k for k in best_lines if k[0] == mkey and k[1] == opp_name), None)
            elif mkey == "totals":
                opp_name = "Under" if name == "Over" else "Over"
                opp_key  = next((k for k in best_lines if k[0] == mkey and k[1] == opp_name), None)
            else:
                continue

            if not opp_key or opp_key not in best_lines:
                continue

            pair = tuple(sorted([str((mkey, name, point)), str(opp_key)]))
            if pair in seen:
                continue
            seen.add(pair)

            opp_odds   = best_lines[opp_key]
            raw_prob   = american_to_implied(odds)
            opp_raw    = american_to_implied(opp_odds)
            fair, _    = remove_vig(raw_prob, opp_raw)
            edge       = calculate_ev(fair, odds)

            if edge < MIN_EDGE:
                continue

            # Build readable bet label
            if mkey == "h2h":
                bet_type, bet_label = "ML", f"{name} ML"
            elif mkey == "spreads":
                sign = "+" if (point or 0) > 0 else ""
                bet_type, bet_label = "RL", f"{name} {sign}{point}"
            else:
                bet_type, bet_label = "OU", f"{name} {point}"

            candidates.append({
                "game_id":   game_id,
                "game":      f"{away} @ {home}",
                "team":      name,
                "bet_type":  bet_type,
                "bet_label": bet_label,
                "odds":      odds,
                "implied":   round(raw_prob, 1),
                "fair_prob": round(fair, 1),
                "edge":      edge,
                "units":     get_units(edge, fair),
            })

    return sorted(candidates, key=lambda x: x["edge"], reverse=True)


# ── Picks Cache ────────────────────────────────────────────────────────────
def save_picks_cache(picks: list):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(PICKS_CACHE_FILE, "w") as f:
            json.dump({"date": today, "picks": picks}, f)
        print(f"[CACHE] Saved {len(picks)} picks")
    except Exception as e:
        print(f"[CACHE ERROR] {e}")

def load_picks_cache() -> list:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(PICKS_CACHE_FILE) as f:
            data = json.load(f)
        return data["picks"] if data.get("date") == today else []
    except Exception as e:
        print(f"[CACHE ERROR] {e}")
        return []


# ── Results Grader ─────────────────────────────────────────────────────────
def grade_picks(picks: list, scores: list) -> list:
    scores_map = {}
    for g in scores:
        if not g.get("completed") or not g.get("scores"):
            continue
        sd = {s["name"]: int(s["score"]) for s in g["scores"]}
        scores_map[g["id"]] = {
            "home": g["home_team"], "away": g["away_team"],
            "home_score": sd.get(g["home_team"], 0),
            "away_score": sd.get(g["away_team"], 0),
        }

    graded = []
    for pick in picks:
        result, pnl = "U", 0.0
        gid   = pick.get("game_id")
        units = pick.get("units", 1.0)
        odds  = pick["odds"]

        def win_pnl():
            return units * (odds / 100 if odds > 0 else 100 / abs(odds))

        if gid in scores_map:
            s  = scores_map[gid]
            hs, as_ = s["home_score"], s["away_score"]
            is_home  = pick["team"] == s["home"]
            team_sc  = hs if is_home else as_
            opp_sc   = as_ if is_home else hs

            if pick["bet_type"] == "ML":
                if   team_sc > opp_sc: result, pnl = "W", win_pnl()
                elif team_sc < opp_sc: result, pnl = "L", -units
                else:                  result, pnl = "P", 0.0

            elif pick["bet_type"] == "RL":
                point  = float(pick["bet_label"].split()[-1])
                margin = (team_sc + point) - opp_sc
                if   margin > 0: result, pnl = "W", win_pnl()
                elif margin < 0: result, pnl = "L", -units
                else:            result, pnl = "P", 0.0

            elif pick["bet_type"] == "OU":
                total    = hs + as_
                ou_point = float(pick["bet_label"].split()[-1])
                side     = pick["team"]
                won = (side == "Over" and total > ou_point) or (side == "Under" and total < ou_point)
                if total == ou_point:   result, pnl = "P", 0.0
                elif won:               result, pnl = "W", win_pnl()
                else:                   result, pnl = "L", -units

        graded.append({**pick, "result": result, "pnl": round(pnl, 2)})
    return graded


# ── Google Sheets ──────────────────────────────────────────────────────────
def log_picks_to_sheet(ws, picks: list):
    if not ws: return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows  = [[today, p["game"], p["bet_type"], p["bet_label"],
              p["odds"], f"{p['implied']}%", f"{p['fair_prob']}%",
              f"{p['edge']}%", p["units"], "", "", ""]
             for p in picks]
    try:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"[SHEETS] Logged {len(rows)} picks")
    except Exception as e:
        print(f"[SHEETS ERROR] {e}")

def update_results_in_sheet(ws, graded: list):
    if not ws: return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        all_rows = ws.get_all_values()
        for i, row in enumerate(all_rows):
            if not row or row[0] != today: continue
            label = row[3] if len(row) > 3 else ""
            for p in graded:
                if p["bet_label"] == label:
                    ws.update_cell(i + 1, 10, p["result"])
                    ws.update_cell(i + 1, 11, p["pnl"])
        print("[SHEETS] Results updated in Picks tab")
    except Exception as e:
        print(f"[SHEETS ERROR] {e}")

def log_daily_record(ws, graded: list):
    if not ws: return
    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    wins    = sum(1 for p in graded if p["result"] == "W")
    losses  = sum(1 for p in graded if p["result"] == "L")
    pushes  = sum(1 for p in graded if p["result"] == "P")
    wagered = sum(p["units"] for p in graded if p["result"] in ("W","L"))
    day_pnl = round(sum(p["pnl"] for p in graded), 2)
    try:
        all_rows = ws.get_all_values()
        running  = sum(float(r[5]) for r in all_rows[1:] if len(r) > 5 and r[5])
        running  = round(running + day_pnl, 2)
        ws.append_row([today, wins, losses, pushes, wagered, day_pnl, running, ""],
                      value_input_option="USER_ENTERED")
        print(f"[SHEETS] Record: {wins}W-{losses}L | {day_pnl:+.2f}u | Running: {running:+.2f}u")
    except Exception as e:
        print(f"[SHEETS ERROR] {e}")


# ── Discord ────────────────────────────────────────────────────────────────
def send_discord(webhook: str, msg: str):
    if not webhook:
        print("[DISCORD] No webhook — skipping"); return
    for chunk in [msg[i:i+1900] for i in range(0, len(msg), 1900)]:
        r = requests.post(webhook, json={"content": chunk}, timeout=10)
        print(f"[DISCORD] {'✓' if r.status_code in (200,204) else '✗ ' + str(r.status_code)}")

def format_picks_message(picks: list) -> str:
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    lines = [f"⚾  **MLB TOP PICKS — {today}**", "━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    for i, p in enumerate(picks, 1):
        odds_str = f"+{p['odds']}" if p['odds'] > 0 else str(p['odds'])
        lines += [
            f"**{i}. {p['bet_label']}** ({odds_str})",
            f"🏟  {p['game']}",
            f"📊  Edge: **{p['edge']}%**  |  Fair: {p['fair_prob']}%  |  Implied: {p['implied']}%",
            f"💰  Bet: **{p['units']}u**", ""
        ]
    lines += ["━━━━━━━━━━━━━━━━━━━━━━━━",
              f"*Top {len(picks)} +EV picks | Best available lines across books*"]
    return "\n".join(lines)

def format_results_message(graded: list) -> str:
    today   = datetime.now(timezone.utc).strftime("%B %d, %Y")
    wins    = sum(1 for p in graded if p["result"] == "W")
    losses  = sum(1 for p in graded if p["result"] == "L")
    pushes  = sum(1 for p in graded if p["result"] == "P")
    ungr    = sum(1 for p in graded if p["result"] == "U")
    day_pnl = sum(p["pnl"] for p in graded)
    emoji   = {"W":"✅","L":"❌","P":"🔁","U":"⏳"}

    lines = [
        f"⚾  **MLB RESULTS — {today}**", "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📋  Record: **{wins}W - {losses}L - {pushes}P**" + (f" ({ungr} pending)" if ungr else ""),
        f"💰  Day P&L: **{day_pnl:+.2f} units**", "━━━━━━━━━━━━━━━━━━━━━━━━", ""
    ]
    for p in graded:
        odds_str = f"+{p['odds']}" if p['odds'] > 0 else str(p['odds'])
        pnl_fmt  = f"{p['pnl']:+.2f}u" if p["result"] != "U" else "pending"
        lines.append(f"{emoji.get(p['result'],'❓')}  **{p['bet_label']}** ({odds_str}) — {pnl_fmt}")
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━━", "*Results logged to tracker sheet ✓*"]
    return "\n".join(lines)


# ── Entry Point ────────────────────────────────────────────────────────────
def run_bot(mode: str = "picks"):
    webhook, api_key, ws_picks, ws_record = get_env()

    if not api_key:
        print("[ERROR] ODDS_API_KEY not set — exiting"); return

    print(f"[BOT] Mode: {mode.upper()}")

    if mode == "picks":
        try:
            games = get_mlb_odds(api_key)
        except Exception as e:
            send_discord(webhook, f"⚠️ MLB Bot: Could not fetch odds — {e}"); return

        if not games:
            send_discord(webhook, "⚾ No MLB games found for today."); return

        candidates = build_picks(games)
        if not candidates:
            send_discord(webhook, "⚾ No +EV picks found today (all below threshold)."); return

        top = candidates[:TOP_N]
        save_picks_cache(top)
        log_picks_to_sheet(ws_picks, top)
        send_discord(webhook, format_picks_message(top))

    elif mode == "results":
        cached = load_picks_cache()
        if not cached:
            send_discord(webhook, "⚾ No picks cache found — results skipped."); return

        try:
            scores = get_mlb_scores(api_key)
        except Exception as e:
            send_discord(webhook, f"⚠️ MLB Bot: Could not fetch scores — {e}"); return

        graded = grade_picks(cached, scores)
        update_results_in_sheet(ws_picks, graded)
        log_daily_record(ws_record, graded)
        send_discord(webhook, format_results_message(graded))
    else:
        print(f"[ERROR] Unknown mode '{mode}' — use 'picks' or 'results'")
