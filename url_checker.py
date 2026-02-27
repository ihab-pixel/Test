#!/usr/bin/env python3
"""
URL Parameter Checker
Strips URL parameters one at a time to determine which are required vs optional.
"""

import sys
import argparse
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

try:
    import requests
except ImportError:
    print("Error: 'requests' library not found. Run: pip install requests")
    sys.exit(1)


def build_url(parsed, params: dict) -> str:
    """Reconstruct a URL with the given query parameters."""
    query_string = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=query_string))


def fetch(url: str, timeout: int, headers: dict) -> requests.Response | None:
    """Make a GET request, returning None on connection error."""
    try:
        return requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        print(f"  Request error: {e}")
        return None


def classify_response(baseline: requests.Response, candidate: requests.Response | None) -> str:
    """
    Compare a candidate response to the baseline.
    Returns 'required' if the parameter seems necessary, 'optional' otherwise.
    """
    if candidate is None:
        return "required"

    # Status code changed from success to error → required
    baseline_ok = baseline.status_code < 400
    candidate_ok = candidate.status_code < 400
    if baseline_ok and not candidate_ok:
        return "required"

    # Status code changed significantly (e.g. 200 → 404, 200 → 302 chain ending elsewhere)
    if baseline.status_code != candidate.status_code:
        return "required"

    # Content length changed by more than 10% → likely required
    baseline_len = len(baseline.content)
    candidate_len = len(candidate.content)
    if baseline_len > 0:
        change_ratio = abs(baseline_len - candidate_len) / baseline_len
        if change_ratio > 0.10:
            return "required"

    return "optional"


def check_url(url: str, timeout: int = 10, delay: float = 0.5, user_agent: str | None = None) -> None:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    if not params:
        print("No query parameters found in the URL.")
        return

    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent

    print(f"\nURL: {url}")
    print(f"Found {len(params)} parameter(s): {', '.join(params.keys())}\n")

    # Baseline request with all parameters
    print("Making baseline request (all parameters)...")
    baseline_url = build_url(parsed, {k: v for k, v in params.items()})
    baseline = fetch(baseline_url, timeout, headers)
    if baseline is None:
        print("Baseline request failed. Cannot proceed.")
        return
    print(f"  Baseline → HTTP {baseline.status_code}, {len(baseline.content)} bytes\n")

    results = {}

    for param in params:
        # Build URL without this one parameter
        reduced_params = {k: v for k, v in params.items() if k != param}
        test_url = build_url(parsed, reduced_params)

        print(f"Testing without '{param}'...")
        print(f"  {test_url}")
        response = fetch(test_url, timeout, headers)

        if response is not None:
            print(f"  → HTTP {response.status_code}, {len(response.content)} bytes")
        else:
            print(f"  → Request failed")

        verdict = classify_response(baseline, response)
        results[param] = verdict
        print(f"  Result: {verdict.upper()}\n")

        if delay > 0:
            time.sleep(delay)

    # Summary
    required = [p for p, v in results.items() if v == "required"]
    optional = [p for p, v in results.items() if v == "optional"]

    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    if required:
        print(f"\nREQUIRED ({len(required)}):")
        for p in required:
            print(f"  - {p}")
    if optional:
        print(f"\nOPTIONAL ({len(optional)}):")
        for p in optional:
            print(f"  - {p}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Check which URL query parameters are required vs optional.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python url_checker.py "https://example.com/search?q=hello&lang=en&page=1"
  python url_checker.py "https://api.example.com/data?key=abc&format=json" --delay 1
  python url_checker.py "https://example.com/?a=1&b=2" --timeout 15 --user-agent "MyBot/1.0"
        """,
    )
    parser.add_argument("url", help="The URL to analyze (must include query parameters)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        metavar="SECONDS",
        help="Request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="Delay between requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--user-agent",
        metavar="STRING",
        help="Custom User-Agent header for requests",
    )

    args = parser.parse_args()

    if not args.url.startswith(("http://", "https://")):
        print("Error: URL must start with http:// or https://")
        sys.exit(1)

    check_url(args.url, timeout=args.timeout, delay=args.delay, user_agent=args.user_agent)


if __name__ == "__main__":
    main()
