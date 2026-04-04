"""
Probe FoPool.lines sub-fields and scan football.hkjc.com SSR HTML for embedded JSON data.
Also look for persisted/whitelisted query IDs in JS bundles.
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
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-HK,zh;q=0.9",
}


def gql(s: requests.Session, query: str, lbl: str = "") -> tuple[int, dict | None]:
    lbl = lbl or query[:60]
    try:
        r = s.post(GQL, json={"query": query}, timeout=15)
        resp = r.json()
        if r.status_code == 200:
            d = resp.get("data")
            print(f"  OK   [{lbl}] matches={'non-null' if (d and d.get('matches')) else 'null'}")
            if d and d.get("matches"):
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

    print("=== FoPool.lines sub-fields ===")
    line_fields = [
        "str", "id", "lineId", "num", "main",
        "home", "away", "handicap", "line",
        "hdcOdds", "currentOdds",
        "oddsH", "oddsA",
        "H", "A",
        "homeOdds", "awayOdds",
        "winOdds", "loseOdds",
        "give", "receive",
        "status",
        "isMain",
    ]
    for f in line_fields:
        q = FP_BASE + "lines { " + f + " } " + FP_END
        gql(s, q, f"lines.{f}")

    print("\n=== Try safe full query (no results/poolInfo) ===")
    safe_q = """
    {
      matches(fbOddsTypes: [HDC], startDate: "20260403", endDate: "20260410") {
        id
        matchDate
        matchNumber
        sequence
        kickOffTime
        endTime
        status
        homeTeam { id name_en name_ch code }
        awayTeam { id name_en name_ch code }
        tournament { id name_en name_ch code }
        runningResult { homeScore awayScore homeCorner awayCorner }
        foPools(fbOddsTypes: [HDC]) {
          id oddsType status matchID sportId
          lines { str }
        }
      }
    }
    """
    code, resp = gql(s, safe_q, "Safe full HDC query")
    if code == 200 and resp:
        print(json.dumps(resp, indent=2, ensure_ascii=False)[:4000])

    print("\n=== Scan football.hkjc.com SSR HTML for embedded data ===")
    fs = requests.Session()
    fs.headers.update(BROWSER_HEADERS)
    r = fs.get("https://football.hkjc.com/football/odds/en/hdc/1", timeout=30)
    html = r.text
    print(f"Page length: {len(html)}")

    # Look for __NEXT_DATA__
    m = re.search(r'id=["\']__NEXT_DATA__["\'][^>]*>(\{.*?\})</script>', html, re.DOTALL)
    if m:
        try:
            nd = json.loads(m.group(1))
            print("__NEXT_DATA__ found, keys:", list(nd.keys())[:10])
            # Dig for match data
            props = nd.get("props", {})
            print("props keys:", list(props.keys())[:10])
            page_props = props.get("pageProps", {})
            print("pageProps keys:", list(page_props.keys())[:10])
            print(json.dumps(page_props, indent=2, ensure_ascii=False)[:3000])
        except Exception as exc:
            print(f"JSON parse error: {exc}")
            print(m.group(1)[:500])
    else:
        print("No __NEXT_DATA__ found, trying other patterns...")
        # Look for JSON data in script tags
        json_blobs = re.findall(r'window\.__(?:INITIAL|STATE|DATA)__\s*=\s*(\{.+?\});', html, re.DOTALL)
        if json_blobs:
            for blob in json_blobs[:2]:
                print(f"Found window data: {blob[:500]}")

        # Look for match data in JSON format
        match_data = re.findall(r'"kickOffTime":\s*"([^"]+)"', html[:10000])
        if match_data:
            print(f"Found kickOffTime values: {match_data[:5]}")

    print("\n=== Try football.hkjc.com with Accept: application/json ===")
    fs2 = requests.Session()
    fs2.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Accept-Language": "zh-HK",
        "Referer": "https://football.hkjc.com/football/odds/en/hdc/1",
    })
    for url in [
        "https://football.hkjc.com/football/odds/en/hdc/1",
        "https://football.hkjc.com/football/api/odds/hdc",
        "https://football.hkjc.com/api/football/hdc",
        "https://football.hkjc.com/football/getjson/odds/hdc",
    ]:
        try:
            r2 = fs2.get(url, timeout=10)
            ctype = r2.headers.get("Content-Type", "")
            is_json = "json" in ctype or r2.text.lstrip().startswith(("{", "["))
            print(f"  STATUS={r2.status_code} JSON={is_json} LEN={len(r2.text)} URL={url}")
            if is_json:
                print(f"  BODY={r2.text[:400]}")
        except Exception as exc:
            print(f"  ERROR {url}: {exc}")


if __name__ == "__main__":
    main()
