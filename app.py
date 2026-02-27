#!/usr/bin/env python3
"""URL Parameter Checker — Flask web app."""

import json
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from flask import Flask, Response, render_template, request, stream_with_context

app = Flask(__name__)

TIMEOUT = 10
DELAY = 0.4


def build_url(parsed, params: dict) -> str:
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def fetch(url: str) -> dict:
    try:
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
        return {"ok": True, "status": r.status_code, "size": len(r.content)}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


def classify(baseline: dict, candidate: dict) -> str:
    if not candidate["ok"]:
        return "required"
    if baseline["status"] != candidate["status"]:
        return "required"
    if baseline["size"] > 0:
        ratio = abs(baseline["size"] - candidate["size"]) / baseline["size"]
        if ratio > 0.10:
            return "required"
    return "optional"


def event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/check")
def check():
    url = request.args.get("url", "").strip()

    def generate():
        if not url.startswith(("http://", "https://")):
            yield event({"type": "error", "message": "URL must start with http:// or https://"})
            return

        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)

        if not params:
            yield event({"type": "error", "message": "No query parameters found in the URL."})
            return

        yield event({"type": "start", "param_count": len(params), "params": list(params.keys())})

        # Baseline
        baseline_url = build_url(parsed, params)
        baseline = fetch(baseline_url)
        if not baseline["ok"]:
            yield event({"type": "error", "message": f"Baseline request failed: {baseline.get('error')}"})
            return

        yield event({"type": "baseline", "status": baseline["status"], "size": baseline["size"]})

        results = {}
        for param in params:
            reduced = {k: v for k, v in params.items() if k != param}
            test_url = build_url(parsed, reduced)
            candidate = fetch(test_url)
            verdict = classify(baseline, candidate)
            results[param] = verdict

            yield event({
                "type": "result",
                "param": param,
                "verdict": verdict,
                "status": candidate.get("status"),
                "size": candidate.get("size"),
                "error": candidate.get("error"),
            })

            time.sleep(DELAY)

        required = [p for p, v in results.items() if v == "required"]
        optional = [p for p, v in results.items() if v == "optional"]
        yield event({"type": "done", "required": required, "optional": optional})

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
