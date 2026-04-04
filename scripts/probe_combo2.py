"""
Probe Combination.currentOdds sub-fields and look for persisted GQL queries in JS bundles.
"""
from __future__ import annotations
import json
import re
import requests

GQL = "https://info.cld.hkjc.com/graphql/base/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "zh-HK,zh;q=0.9",
    "Content-Type": "application/json",
    "Referer": "https://bet.hkjc.com/ch/football/hdc",
    "Origin": "https://bet.hkjc.com",
}


def gql(s: requests.Session, query: str, lbl: str = "") -> tuple[int, dict | None]:
    lbl = lbl or query[:60]
    try:
        r = s.post(GQL, json={"query": query}, timeout=20)
        resp = r.json()
        if r.status_code == 200:
            d = resp.get("data")
            has_data = d and any(v is not None for v in d.values())
            print(f"  {'DATA' if has_data else 'null':4s} [{lbl}]")
            if has_data:
                print(json.dumps(d, indent=2, ensure_ascii=False)[:4000])
        elif r.status_code == 400 and resp.get("errors"):
            for e in resp["errors"][:2]:
                print(f"  ERR  [{lbl}]: {e.get('message','')[:240]}")
        else:
            print(f"  HTTP={r.status_code} [{lbl}]: {r.text[:200]}")
        return r.status_code, resp
    except Exception as exc:
        print(f"  EXC [{lbl}]: {exc}")
        return 0, None


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)

    COMBO_BASE = "{ matches(fbOddsTypes: [HDC]) { foPools(fbOddsTypes: [HDC]) { lines { combinations { "
    COMBO_END = " } } } } }"

    # currentOdds is nested object - probe its fields
    print("=== Combination.currentOdds sub-fields ===")
    odds_sub = [
        "value", "val",
        "decimal", "decimalOdds",
        "odds", "price",
        "h", "a", "d",
        "H", "A",
        "home", "away", "draw",
        "current", "opening",
        "status",
        "line",
    ]
    for f in odds_sub:
        q = COMBO_BASE + "currentOdds { " + f + " } " + COMBO_END
        gql(s, q, f"currentOdds.{f}")

    print("\n=== Combination.selections sub-fields ===")
    sel_sub = [
        "id", "str", "key",
        "selectionId", "selId",
        "currentOdds", "odds",
    ]
    for f in sel_sub:
        q = COMBO_BASE + "selections { " + f + " } " + COMBO_END
        gql(s, q, f"selections.{f}")

    print("\n=== Combination.winOrd ===")
    q = COMBO_BASE + "winOrd " + COMBO_END
    gql(s, q, "combo.winOrd")

    print("\n=== Work backwards: scan football.hkjc.com JS bundles for GQL queries ===")
    browser = requests.Session()
    browser.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Accept-Language": "zh-HK,zh;q=0.9",
        "Referer": "https://football.hkjc.com/",
    })
    # Get the page to find JS chunk names
    r = browser.get("https://football.hkjc.com/football/odds/en/hdc/1", timeout=30)
    html = r.text

    # Find all chunk JS files
    chunks = re.findall(r'/_next/static/chunks/([^"\']+\.js)', html)
    print(f"Found {len(chunks)} JS chunks")
    print("Chunks:", chunks[:8])

    # Download and scan chunks for GraphQL queries
    for chunk in chunks[:15]:
        url = f"https://football.hkjc.com/_next/static/chunks/{chunk}"
        try:
            rjs = browser.get(url, timeout=20)
            js = rjs.text
            # Look for GraphQL query strings
            gql_queries = re.findall(r'query\s+\w+\s*(?:\([^)]*\))?\s*\{[^{]{20,500}\}', js[:500000])
            gql_fragments = re.findall(r'fragment\s+\w+\s+on\s+\w+\s*\{[^{]{10,200}\}', js[:500000])
            keywords = re.findall(r'(kickOffTime|name_en|foPools|hdcOdds|combinations|currentOdds|fbOddsTypes)', js[:500000])
            if gql_queries or keywords:
                print(f"\nChunk: {chunk}")
                if keywords:
                    print(f"  Keywords: {set(keywords)}")
                for q in gql_queries[:3]:
                    print(f"  QUERY: {q[:300]}")
                for f in gql_fragments[:3]:
                    print(f"  FRAGMENT: {f[:200]}")
        except Exception as exc:
            print(f"  SKIP {chunk}: {exc}")

    print("\n=== Also try the app directory chunks ===")
    app_chunks = re.findall(r'/_next/static/chunks/app/([^"\']+\.js)', html)
    print(f"Found {len(app_chunks)} app chunks: {app_chunks[:5]}")
    for chunk in app_chunks[:6]:
        url = f"https://football.hkjc.com/_next/static/chunks/app/{chunk}"
        try:
            rjs = browser.get(url, timeout=20)
            js = rjs.text
            keywords = re.findall(
                r'(kickOffTime|name_en|foPools|hdcOdds|combinations|currentOdds|fbOddsTypes|FBOddsType)',
                js[:1000000]
            )
            if keywords:
                print(f"\nApp chunk: {chunk} has keywords: {set(keywords)}")
                # Extract context around fbOddsTypes
                for kw in ['fbOddsTypes', 'combinations', 'currentOdds', 'kickOffTime']:
                    pos = js.find(kw)
                    if pos >= 0:
                        print(f"  Context around '{kw}': {js[max(0,pos-100):pos+200]}")
                        break
        except Exception as exc:
            print(f"  SKIP app/{chunk}: {exc}")


if __name__ == "__main__":
    main()
