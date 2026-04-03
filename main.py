from flask import Flask, request

app = Flask(__name__)

@app.route("/")
def run():
    mode = request.args.get("mode")

    if mode == "picks":
        print("running picks")

    elif mode == "grading":
        print("running grading")

    return "OK"
