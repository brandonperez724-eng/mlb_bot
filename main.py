from flask import Flask, request
import os

app = Flask(__name__)

@app.route("/")
def run():
    mode = request.args.get("mode")

    if mode == "picks":
        print("running picks")

    elif mode == "grading":
        print("running grading")

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
