"""Probe HKJC GQL sub-types: poolInfo, tournament, runningResult, and craft full query."""
from __future__ import annotations
import json
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
        if r.status_code == 200 and resp.get("data"):
            print(f"  OK   [{lbl}] data={json.dumps(resp['data'], ensure_ascii=False)[:300]}")
        elif r.status_code == 400 and resp.get("errors"):
            for e in resp["errors"][:2]:
                print(f"  ERR  [{lbl}]: {e.get('message','')[:180]}")
        else:
            print(f"  HTTP={r.status_code} [{lbl}]: {r.text[:200]}")
        return r.status_code, resp
    except Exception as exc:
        print(f"  EXC [{lbl}]: {exc}")
        return 0, None


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)

    BASE = "{ matches(fbOddsTypes: [HDC]) { "

    # Probe poolInfo sub-fields
    print("=== poolInfo sub-fields ===")
    pi_fields = [
        "oddsType", "name_en", "name_ch", "status",
        "margin", "hadOdds",
        "hdcOdds", "handicapOdds",
        "home", "away", "draw",
        "handLossOdds", "handWinOdds",
        "currentOdds", "openingOdds",
        "line", "handicapLine", "handicap",
        "homeOdds", "awayOdds",
        "h", "a",
    ]
    for f in pi_fields:
        # poolInfo with one sub-field
        q = BASE + "poolInfo { " + f + " } } }"
        gql(s, q, f"poolInfo.{f}")

    print("\n=== poolInfo nested objects ===")
    # Some may be nested
    nested = [
        "odds { home away draw }",
        "hdcOdds { home away line handicap }",
        "currentLine { home away handicap line }",
    ]
    for f in nested:
        q = BASE + "poolInfo { " + f + " } } }"
        gql(s, q, f"poolInfo nested {f[:30]}")

    print("\n=== tournament sub-fields ===")
    tour_fields = ["id", "code", "name_en", "name_ch", "nameEn", "nameCh", "shortName"]
    for f in tour_fields:
        q = BASE + "tournament { " + f + " } } }"
        gql(s, q, f"tournament.{f}")

    print("\n=== runningResult sub-fields ===")
    rr_fields = [
        "homeScore", "awayScore", "score", "result",
        "home", "away",
        "homeCorner", "awayCorner",
        "status",
    ]
    for f in rr_fields:
        q = BASE + "runningResult { " + f + " } } }"
        gql(s, q, f"runningResult.{f}")

    print("\n=== results field exploration ===")
    results_fields = ["homeScore", "awayScore", "status", "winSide", "result", "score",
                      "home", "away"]
    for f in results_fields:
        q = BASE + "results { " + f + " } } }"
        gql(s, q, f"results.{f}")

    print("\n=== Full HDC query attempt ===")
    full_q = """
    {
      matches(fbOddsTypes: [HDC]) {
        id
        matchDate
        kickOffTime
        status
        homeTeam { id name_en name_ch code }
        awayTeam { id name_en name_ch code }
        tournament { id name_en name_ch code }
        poolInfo { oddsType status }
        runningResult { homeScore awayScore }
      }
    }
    """
    code, resp = gql(s, full_q, "Full HDC query")
    if code == 200 and resp:
        print(json.dumps(resp, indent=2, ensure_ascii=False)[:2000])

    print("\n=== Try with date range for upcoming matches ===")
    dated_q = """
    {
      matches(fbOddsTypes: [HDC], startDate: "20260403", endDate: "20260410") {
        id
        matchDate
        kickOffTime
        status
        homeTeam { name_en name_ch }
        awayTeam { name_en name_ch }
        tournament { name_en name_ch }
        poolInfo { oddsType status }
      }
    }
    """
    code, resp = gql(s, dated_q, "HDC with date range")
    if code == 200 and resp:
        print(json.dumps(resp, indent=2, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
