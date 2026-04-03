from flask import Flask, request
from mlb_bot_NEW_v2 import run_picks, run_grading

app = Flask(__name__)

@app.route("/")
def run():
    mode = request.args.get("mode")

    if mode == "picks":
        run_picks()

    elif mode == "grading":
        run_grading()

    return "OK"
