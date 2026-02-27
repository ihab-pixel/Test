#!/usr/bin/env python3
"""URL Parameter Checker — Flask web app."""

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

TIMEOUT = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def build_url(parsed, params: dict) -> str:
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def visible_text_length(content: bytes) -> int:
    """Return the character count of visible text after stripping HTML tags."""
    text = re.sub(rb"<[^>]+>", b"", content)
    text = re.sub(rb"\s+", b" ", text).strip()
    return len(text)


def _first(pattern: str, text: str, flags=re.IGNORECASE | re.DOTALL) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def extract_content_fingerprint(html: str) -> dict:
    """Extract server-side content markers that change with URL parameters."""

    # <title>
    title = _first(r"<title[^>]*>(.*?)</title>", html)

    # <meta name="description"> / og:title / og:description / og:url
    meta_desc = _first(r'<meta\s[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']', html)
    og_title = _first(r'<meta\s[^>]*property=["\']og:title["\'][^>]*content=["\'](.*?)["\']', html)
    og_desc = _first(r'<meta\s[^>]*property=["\']og:description["\'][^>]*content=["\'](.*?)["\']', html)
    og_url = _first(r'<meta\s[^>]*property=["\']og:url["\'][^>]*content=["\'](.*?)["\']', html)

    # <link rel="canonical">
    canonical = _first(r'<link\s[^>]*rel=["\']canonical["\'][^>]*href=["\'](.*?)["\']', html)

    # JSON-LD blocks (often contain page-specific structured data)
    jsonld_blocks = re.findall(
        r'<script\s[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.IGNORECASE | re.DOTALL
    )
    jsonld_hash = hashlib.md5("".join(jsonld_blocks).encode()).hexdigest()

    # Next.js / Nuxt / other SPA initial state blobs
    next_data = _first(r'<script\s[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html)
    nuxt_data = _first(r'window\.__NUXT__\s*=\s*(\{.*?\})\s*;', html)
    remix_data = _first(r'window\.__remixContext\s*=\s*(\{.*?\})', html)

    # Apollo / Relay / TanStack initial cache
    apollo = _first(r'window\.__APOLLO_STATE__\s*=\s*(\{.*?\})', html)

    # Generic __INITIAL_STATE__ pattern used by many frameworks
    initial_state = _first(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})', html)

    return {
        "title": title,
        "meta_desc": meta_desc,
        "og_title": og_title,
        "og_desc": og_desc,
        "og_url": og_url,
        "canonical": canonical,
        "jsonld_hash": jsonld_hash,
        "next_data_len": len(next_data),
        "nuxt_data_len": len(nuxt_data),
        "remix_data_len": len(remix_data),
        "apollo_len": len(apollo),
        "initial_state_len": len(initial_state),
    }


def fetch(url: str) -> dict:
    try:
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=True, headers=HEADERS)
        content = r.content
        html = content.decode("utf-8", errors="replace")
        return {
            "ok": True,
            "status": r.status_code,
            "size": len(content),
            "text_len": visible_text_length(content),
            "final_url": r.url,
            "fingerprint": extract_content_fingerprint(html),
        }
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


def _fingerprints_differ(fp1: dict, fp2: dict) -> bool:
    """Return True if any meaningful content marker changed."""
    # String fields: any non-empty difference counts
    for key in ("title", "meta_desc", "og_title", "og_desc", "og_url", "canonical", "jsonld_hash"):
        v1, v2 = fp1.get(key, ""), fp2.get(key, "")
        if v1 and v2 and v1 != v2:
            return True
        # If baseline had content but candidate is empty (or vice versa)
        if bool(v1) != bool(v2):
            return True
    # Numeric length fields: flag if the blob shrinks/grows significantly
    for key in ("next_data_len", "nuxt_data_len", "remix_data_len", "apollo_len", "initial_state_len"):
        v1, v2 = fp1.get(key, 0), fp2.get(key, 0)
        if v1 > 100:  # only care if there was real data
            ratio = abs(v1 - v2) / v1
            if ratio > 0.05:
                return True
    return False


def classify(baseline: dict, candidate: dict) -> str:
    if not candidate["ok"]:
        return "required"
    if baseline["status"] != candidate["status"]:
        return "required"
    # Redirect to a different URL means the param influenced routing
    if baseline.get("final_url") != candidate.get("final_url"):
        return "required"
    # Content fingerprint check (catches JS-heavy SPAs with server-injected data)
    if _fingerprints_differ(
        baseline.get("fingerprint", {}),
        candidate.get("fingerprint", {}),
    ):
        return "required"
    if baseline["size"] > 0:
        ratio = abs(baseline["size"] - candidate["size"]) / baseline["size"]
        if ratio > 0.05:
            return "required"
    # Visible text drops sharply on "200 but empty page" responses
    baseline_tl = baseline.get("text_len", 0)
    candidate_tl = candidate.get("text_len", 0)
    if baseline_tl > 200:
        text_ratio = abs(baseline_tl - candidate_tl) / baseline_tl
        if text_ratio > 0.20:
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
            "test_url": test_url,
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
