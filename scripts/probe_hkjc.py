"""Probe HKJC endpoints to find working JSON data sources."""
from __future__ import annotations
import json
import re
import sys
import requests

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    "Referer": "https://bet.hkjc.com/ch/football/hdc",
    "Cache-Control": "no-cache",
}


def probe(url: str, session: requests.Session) -> None:
    try:
        r = session.get(url, timeout=15)
        ctype = r.headers.get("Content-Type", "")
        body = r.text or ""
        is_json = "json" in ctype or (body.lstrip().startswith(("{", "[")))
        print(f"STATUS={r.status_code} JSON={is_json} LEN={len(body)} CTYPE={ctype[:60]}")
        print(f"  URL={url}")
        if is_json:
            try:
                parsed = r.json()
                print(f"  JSON_KEYS={list(parsed.keys())[:10] if isinstance(parsed, dict) else type(parsed).__name__ + ' len=' + str(len(parsed))}")
            except Exception:
                pass
        print(f"  PREVIEW={repr(body[:250])}")
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}")
        print(f"  URL={url}")
    print()


def extract_next_data(html: str) -> dict | None:
    m = re.search(r'id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def find_api_endpoints_in_js(js_text: str) -> list[str]:
    # Look for /api/ or getJSON patterns
    matches = re.findall(r'["\'](/(?:api|football/api)[^"\'?#]{0,80})["\']', js_text)
    return list(set(matches))


def main() -> None:
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    # --- Step 1: fetch the page itself ---
    print("=" * 60)
    print("STEP 1: Fetch main HDC page")
    print("=" * 60)
    r = session.get("https://bet.hkjc.com/ch/football/hdc", timeout=20)
    html = r.text
    print(f"Page length: {len(html)}")

    # Extract __NEXT_DATA__
    ndata = extract_next_data(html)
    if ndata:
        print("Found __NEXT_DATA__:")
        print(json.dumps(ndata, indent=2, ensure_ascii=False)[:1500])
    else:
        print("No __NEXT_DATA__ found")

    # Extract script tags
    script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
    print(f"\nScript tags found: {len(script_srcs)}")
    for src in script_srcs[:8]:
        print(f"  {src}")

    # Look for API endpoint references in inline scripts
    inline_scripts = re.findall(r'<script(?:\s[^>]*)?>(.+?)</script>', html, re.DOTALL)
    all_api = []
    for chunk in inline_scripts:
        all_api.extend(find_api_endpoints_in_js(chunk))
    if all_api:
        print(f"\nAPI endpoints found in inline scripts: {all_api}")

    print()
    print("=" * 60)
    print("STEP 2: Probe known HKJC JSON endpoint patterns")
    print("=" * 60)

    # Known endpoints from community research + variations
    endpoints_to_test = [
        # football.hkjc.com older style (with .aspx suffix)
        "https://football.hkjc.com/football/getJSON.aspx?jsontype=schedule.aspx&pageno=1",
        "https://football.hkjc.com/football/getJSON.aspx?jsontype=odds_allodds.aspx&pageno=1",
        "https://football.hkjc.com/football/getJSON.aspx?jsontype=odds_hdc.aspx&pageno=1",
        # bet.hkjc.com older style
        "https://bet.hkjc.com/football/getJSON.aspx?jsontype=odds_allodds.aspx&pageno=1",
        "https://bet.hkjc.com/football/getJSON.aspx?jsontype=schedule.aspx&pageno=1",
        "https://bet.hkjc.com/football/getJSON.aspx?jsontype=odds_hdc.aspx&pageno=1",
        # API v2 style guesses
        "https://bet.hkjc.com/api/football/matches",
        "https://bet.hkjc.com/api/football/hdc/odds",
        # Next.js API routes
        "https://bet.hkjc.com/api/football",
        "https://bet.hkjc.com/ch/api/football/hdc",
        # Static JSON paths sometimes used in Next.js
        "https://bet.hkjc.com/_next/data/football/hdc.json",
    ]

    for url in endpoints_to_test:
        probe(url, session)

    # --- Step 3: Look at a real JS bundle chunk for API URLs ---
    print("=" * 60)
    print("STEP 3: Scan JS bundle for API patterns")
    print("=" * 60)
    js_api_found: list[str] = []
    for src in script_srcs[:6]:
        if not src.startswith("http"):
            src = "https://bet.hkjc.com" + src
        try:
            rjs = session.get(src, timeout=15)
            apis = find_api_endpoints_in_js(rjs.text)
            if apis:
                print(f"Found in {src[:80]}:")
                for a in apis[:10]:
                    print(f"  {a}")
                js_api_found.extend(apis)
        except Exception as exc:
            print(f"  SKIP {src[:80]}: {exc}")

    # Dedupe and probe any new ones
    new_endpoints = list(set(js_api_found) - set(endpoints_to_test))
    if new_endpoints:
        print("\n=== Probing endpoints found in JS ===")
        for url in new_endpoints[:15]:
            full = url if url.startswith("http") else "https://bet.hkjc.com" + url
            probe(full, session)


if __name__ == "__main__":
    main()
