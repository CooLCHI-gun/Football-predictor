"""
Probe Line.combinations (Combination type) for odds/handicap data.
Also search football.hkjc.com SSR HTML for match data.
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
    FP_BASE = "{ matches(fbOddsTypes: [HDC]) { foPools(fbOddsTypes: [HDC]) { lines { combinations { "
    FP_END = " } } } } }"

    print("=== Combination fields ===")
    combo_fields = [
        "id", "str", "desc",
        "currentOdds", "odds",
        "homeOdds", "awayOdds",
        "winOdds", "loseOdds",
        "H", "A",
        "h", "a",
        "side", "selection",
        "selectionType",
        "oddsValue",
        "value",
        "line",
        "lineStr",
        "lineValue",
        "handicap",
        "hdcValue",
        "status",
        "result",
        "refund",
        "banker",
        "composite",
        "key",
    ]
    for f in combo_fields:
        q = FP_BASE + f + FP_END
        gql(s, q, f"combo.{f}")

    print("\n=== Dedicated FoPool HDC query with matchNumber, no whitelist-trigger ===")
    # Try minimal safe query with only known-working fields
    qtest = """
    {
      matches(fbOddsTypes: [HDC]) {
        id
        matchDate
        matchNumber
        kickOffTime
        status
        homeTeam { id name_en name_ch code }
        awayTeam { id name_en name_ch code }
        tournament { id name_en name_ch code }
        foPools(fbOddsTypes: [HDC]) {
          id oddsType status matchID sportId
          lines {
            id lineId main status
            combinations { id }
          }
        }
      }
    }
    """
    code, resp = gql(s, qtest, "Full HDC with lines.combinations")
    if code == 200 and resp and resp.get("data", {}).get("matches"):
        print("=== GOT LIVE DATA ===")
        print(json.dumps(resp["data"], indent=2, ensure_ascii=False)[:5000])

    print("\n=== Scan football.hkjc.com HTML for match/odds data ===")
    browser_session = requests.Session()
    browser_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-HK,zh;q=0.9",
    })
    r = browser_session.get("https://football.hkjc.com/football/odds/en/hdc/1", timeout=30)
    html = r.text

    # Search for JSON-like match data patterns in the huge page
    # Look for kickOffTime pattern
    kick_times = re.findall(r'"kickOffTime"\s*:\s*"([^"]+)"', html)
    if kick_times:
        print(f"Found kickOffTime values: {kick_times[:10]}")

    # Look for name_en patterns
    name_ens = re.findall(r'"name_en"\s*:\s*"([^"]+)"', html)
    if name_ens:
        print(f"Found name_en values (first 10): {name_ens[:10]}")

    # Look for HDC-related patterns
    hdc_matches = re.findall(r'"oddsType"\s*:\s*"HDC"', html)
    print(f"HDC pattern occurrences: {len(hdc_matches)}")

    # Look for Apollo cache data
    # football.hkjc.com might use window.__APOLLO_STATE__ or similar
    for pattern in [
        r'window\.__APOLLO_STATE__\s*=\s*',
        r'self\.__next_f\s*=',
        r'"apolloState"',
        r'ROOT_QUERY',
        r'"Match:\d',
        r'"FoPool:',
    ]:
        found = re.findall(pattern, html)
        if found:
            print(f"Pattern '{pattern}' found {len(found)} times")
            # Find surrounding context
            m_pos = html.find(found[0]) if isinstance(found[0], str) else -1
            if m_pos >= 0:
                print(f"  Context: {html[max(0,m_pos-50):m_pos+200]}")

    # Look for Next.js flight data (self.__next_f.push)
    flight_data = re.findall(r'self\.__next_f\.push\(\[(\d+),(.*?)\]\)', html[:50000], re.DOTALL)
    if flight_data:
        print(f"Found Next.js flight data chunks: {len(flight_data)}")
        for chunk_type, chunk_data in flight_data[:3]:
            print(f"  Type={chunk_type}: {chunk_data[:200]}")

    # Check for script tags with Apollo/React state
    script_matches = re.findall(r'<script[^>]*>(.*?)</script>', html[:100000], re.DOTALL)
    for i, scr in enumerate(script_matches[:20]):
        if any(kw in scr for kw in ['kickOffTime', 'name_en', 'matchDate', 'HDC', 'oddsType']):
            print(f"\nScript {i} has relevant data (len={len(scr)}):")
            print(scr[:800])

    print("\n=== Look for embedded JSON data in page (large chunks) ===")
    # Find large JSON blobs anywhere in the page
    # Look for the start of potential JSON objects with match-like data
    for search_term in ['kickOffTime', 'name_en', '"HDC"', 'homeTeam', 'awayTeam']:
        positions = [m.start() for m in re.finditer(re.escape('"' + search_term.strip('"')), html)]
        if positions:
            pos = positions[0]
            print(f"\n'{search_term}' first found at position {pos}:")
            print(repr(html[max(0,pos-100):pos+300]))


if __name__ == "__main__":
    main()
