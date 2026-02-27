#!/usr/bin/env python3
"""URL Parameter Checker — Flask web app."""

import hashlib
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from flask import Flask, jsonify, render_template, request
from playwright.sync_api import sync_playwright

app = Flask(__name__)

TIMEOUT_MS = 20_000   # ms — time for page + JS to finish loading
CHROME_PATH = "/root/.cache/ms-playwright/chromium-1194/chrome-linux/chrome"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# One shared browser instance; protected by a lock during (re)launch only.
_pw = None
_browser = None
_browser_lock = threading.Lock()


def _get_browser():
    global _pw, _browser
    with _browser_lock:
        if _browser is None or not _browser.is_connected():
            if _pw is not None:
                try:
                    _pw.stop()
                except Exception:
                    pass
            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch(
                headless=True,
                executable_path=CHROME_PATH,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
    return _browser


def build_url(parsed, params: dict) -> str:
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def visible_text_length(html: str) -> int:
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", " ", text).strip()
    return len(text)


def _first(pattern: str, text: str, flags=re.IGNORECASE | re.DOTALL) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def extract_content_fingerprint(html: str) -> dict:
    title = _first(r"<title[^>]*>(.*?)</title>", html)
    meta_desc = _first(r'<meta\s[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']', html)
    og_title = _first(r'<meta\s[^>]*property=["\']og:title["\'][^>]*content=["\'](.*?)["\']', html)
    og_url = _first(r'<meta\s[^>]*property=["\']og:url["\'][^>]*content=["\'](.*?)["\']', html)
    canonical = _first(r'<link\s[^>]*rel=["\']canonical["\'][^>]*href=["\'](.*?)["\']', html)
    jsonld = re.findall(
        r'<script\s[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.IGNORECASE | re.DOTALL,
    )
    next_data = _first(r'<script\s[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html)
    nuxt_data = _first(r'window\.__NUXT__\s*=\s*(\{.*?\})\s*;', html)
    return {
        "title": title,
        "meta_desc": meta_desc,
        "og_title": og_title,
        "og_url": og_url,
        "canonical": canonical,
        "jsonld_hash": hashlib.md5("".join(jsonld).encode()).hexdigest(),
        "next_data_len": len(next_data),
        "nuxt_data_len": len(nuxt_data),
    }


def fetch(url: str) -> dict:
    try:
        browser = _get_browser()
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            resp = page.goto(url, timeout=TIMEOUT_MS, wait_until="networkidle")
            html = page.content()   # fully rendered DOM after JS execution
            status = resp.status if resp else 0
            final_url = page.url
        finally:
            ctx.close()

        return {
            "ok": True,
            "status": status,
            "size": len(html.encode("utf-8")),
            "text_len": visible_text_length(html),
            "final_url": final_url,
            "fingerprint": extract_content_fingerprint(html),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _fingerprints_differ(fp1: dict, fp2: dict) -> bool:
    for key in ("title", "meta_desc", "og_title", "og_url", "canonical", "jsonld_hash"):
        v1, v2 = fp1.get(key, ""), fp2.get(key, "")
        if v1 and v2 and v1 != v2:
            return True
        if bool(v1) != bool(v2):
            return True
    for key in ("next_data_len", "nuxt_data_len"):
        v1, v2 = fp1.get(key, 0), fp2.get(key, 0)
        if v1 > 100 and abs(v1 - v2) / v1 > 0.05:
            return True
    return False


def classify(baseline: dict, candidate: dict) -> str:
    if not candidate["ok"]:
        return "required"
    if baseline["status"] != candidate["status"]:
        return "required"
    if baseline.get("final_url") != candidate.get("final_url"):
        return "required"
    if _fingerprints_differ(baseline.get("fingerprint", {}), candidate.get("fingerprint", {})):
        return "required"
    if baseline["size"] > 0:
        ratio = abs(baseline["size"] - candidate["size"]) / baseline["size"]
        if ratio > 0.05:
            return "required"
    baseline_tl = baseline.get("text_len", 0)
    candidate_tl = candidate.get("text_len", 0)
    if baseline_tl > 200 and abs(baseline_tl - candidate_tl) / baseline_tl > 0.20:
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

    with ThreadPoolExecutor(max_workers=min(len(params), 5)) as executor:
        futures = {executor.submit(check_param, p): p for p in params}
        raw = {f.result()["param"]: f.result() for f in as_completed(futures)}

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
