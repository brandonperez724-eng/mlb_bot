from flask import Flask, request
from mlb_bot_NEW_v2 import run_bot

app = Flask(__name__)

@app.route("/")
def home():
    mode = request.args.get("mode", "picks")
    print(f"[TRIGGER] mode={mode}")
    run_bot(mode)
    return f"OK — ran in {mode} mode", 200

@app.route("/health")
def health():
    return "healthy", 200

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
