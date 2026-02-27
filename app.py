#!/usr/bin/env python3
"""URL Parameter Checker — Flask web app."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

TIMEOUT = 8


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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/check", methods=["POST"])
def check():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()

    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400

    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    if not params:
        return jsonify({"error": "No query parameters found in the URL."}), 400

    baseline_url = build_url(parsed, params)
    baseline = fetch(baseline_url)
    if not baseline["ok"]:
        return jsonify({"error": f"Baseline request failed: {baseline.get('error')}"}), 502

    def check_param(param):
        reduced = {k: v for k, v in params.items() if k != param}
        test_url = build_url(parsed, reduced)
        candidate = fetch(test_url)
        verdict = classify(baseline, candidate)
        return {
            "param": param,
            "verdict": verdict,
            "status": candidate.get("status"),
            "size": candidate.get("size"),
            "error": candidate.get("error"),
        }

    with ThreadPoolExecutor(max_workers=min(len(params), 10)) as executor:
        futures = {executor.submit(check_param, p): p for p in params}
        raw = {f.result()["param"]: f.result() for f in as_completed(futures)}

    # preserve original param order
    results = [raw[p] for p in params]

    required = [r["param"] for r in results if r["verdict"] == "required"]
    optional = [r["param"] for r in results if r["verdict"] == "optional"]

    return jsonify({
        "baseline": {"status": baseline["status"], "size": baseline["size"]},
        "results": results,
        "required": required,
        "optional": optional,
    })


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
