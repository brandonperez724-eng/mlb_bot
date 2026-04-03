from flask import Flask, request
from mlb_bot_NEW_v2 import run_bot

app = Flask(__name__)

@app.route("/")
def home():
    mode = request.args.get("mode", "picks")

    run_bot(mode)

    return "OK"
