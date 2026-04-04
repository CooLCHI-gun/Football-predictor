"""
Scan the 2.3MB football.hkjc.com SSR page for embedded match data.
Also probe remaining FoPool fields for odds.
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
        r = s.post(GQL, json={"query": query}, timeout=15)
        resp = r.json()
        if r.status_code == 200:
            d = resp.get("data")
            has_data = d and any(v is not None for v in d.values())
            status = "DATA" if has_data else "null"
            print(f"  {status:4s} [{lbl}]")
            if has_data:
                print(json.dumps(d, indent=2, ensure_ascii=False)[:3000])
        elif r.status_code == 400 and resp.get("errors"):
            for e in resp["errors"][:2]:
                print(f"  ERR  [{lbl}]: {e.get('message','')[:220]}")
        else:
            print(f"  HTTP={r.status_code} [{lbl}]: {r.text[:200]}")
        return r.status_code, resp
    except Exception as exc:
        print(f"  EXC [{lbl}]: {exc}")
        return 0, None


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)
    FP_BASE = "{ matches(fbOddsTypes: [HDC]) { foPools(fbOddsTypes: [HDC]) { "
    FP_END = " } } }"

    print("=== More FoPool fields ===")
    more_fp = [
        "key", "combineInfo", "allupInfo",
        "jackpotInfo", "jackpotOdds",
        "bankerOdds", "legOdds",
        "singleOdds", "combinedOdds",
        "hdcOdds", "currentLine",
        "oddsDetails",
        "hcOdds",
        "marginInfo",
        "hadOdds", "fcOdds",
    ]
    for f in more_fp:
        q = FP_BASE + f + FP_END
        gql(s, q, f"foPools.{f}")

    print("\n=== Line type additional fields ===")
    line_more = [
        "seq", "sequence", "sort",
        "value", "lineValue",
        "hdcValue",
        "score",
        "W",  # win
        "desc",
        "description",
        "combinations",
    ]
    for f in line_more:
        q = FP_BASE + "lines { " + f + " } " + FP_END
        gql(s, q, f"lines.{f}")

    print("\n=== Scan large football.hkjc.com SSR HTML ===")
    browser = requests.Session()
    browser.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    })
    r = browser.get("https://football.hkjc.com/football/odds/en/hdc/1", timeout=30)
    html = r.text
    print(f"Response length: {len(html)}")
    print(f"Content-Type: {r.headers.get('Content-Type','')}")

    # Look for inline script with JSON data
    # HKJC React app might use Apollo InMemoryCache or similar
    # Search for match-like data patterns
    patterns = [
        r'"kickOffTime":"([^"]+)"',
        r'"homeTeam":\{"[^}]+\}',
        r'"name_en":"([^"]+)"',
        r'"matchDate":"([^"]+)"',
        r'\"oddsType\":\"HDC\"',
        r'graphql|apollo|cache',
        r'"__typename":"Match"',
        r'"__typename":"FoPool"',
    ]
    for pat in patterns:
        matches_found = re.findall(pat, html[:50000], re.IGNORECASE)
        if matches_found:
            print(f"\nPattern '{pat[:40]}' found {len(matches_found)} times:")
            print(f"  First few: {matches_found[:3]}")

    # Look for window.__APOLLO_STATE__ or similar
    apollo_state = re.search(r'window\.__APOLLO_STATE__\s*=\s*(\{.+?\});\s*</script>', html, re.DOTALL)
    if apollo_state:
        print("\nFound Apollo state!")
        try:
            state = json.loads(apollo_state.group(1))
            print(json.dumps(state, indent=2, ensure_ascii=False)[:3000])
        except Exception:
            print(apollo_state.group(1)[:1000])

    # Look for any script tags with large JSON
    inline_scripts = re.findall(r'<script[^>]*>(\{[^<]{50,})</script>', html[:100000], re.DOTALL)
    for i, scr in enumerate(inline_scripts[:5]):
        print(f"\nInline JSON script {i}: {scr[:300]}")

    # Check the first 3000 chars and last 3000 chars
    print("\n=== Page start ===")
    print(html[:2000])
    print("\n=== Page mid (chars 100k-102k) ===")
    print(html[100000:102000])


if __name__ == "__main__":
    main()
