"""Probe PoolInfo and WageringResult sub-types, then run full query."""
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
        if r.status_code == 200:
            print(f"  OK   [{lbl}] data={json.dumps(resp.get('data'), ensure_ascii=False)[:250]}")
        elif r.status_code == 400 and resp.get("errors"):
            for e in resp["errors"][:2]:
                print(f"  ERR  [{lbl}]: {e.get('message','')[:200]}")
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

    # PoolInfo fields — hint said "chpType" is a field
    print("=== PoolInfo fields ===")
    pi_fields = [
        "chpType", "id", "poolId", "matchID", "selection",
        "oddsKey", "poolStatus", "winOdds", "loseOdds",
        "adjustedLine", "line",
        "hdc",
    ]
    for f in pi_fields:
        q = BASE + "poolInfo { " + f + " } } }"
        gql(s, q, f"poolInfo.{f}")

    # Try poolInfo as an array
    print("\n=== Try allodds / odds list on match directly ===")
    match_extra = [
        "sequence", "matchNumber",
        "endTime",
        "liveEventScores",
        "sResults",
        "fbMatches",
        "foPools",
    ]
    for f in match_extra:
        q = BASE + f + " } }"
        gql(s, q, f"match.{f}")

    # WageringResult fields
    print("\n=== WageringResult sub-fields ===")
    wr_fields = [
        "id", "code", "resultCode", "hdcResult",
        "homeResult", "awayResult",
        "winLose", "winningCombo",
        "dividend", "poolId",
        "chpResult",
    ]
    for f in wr_fields:
        q = BASE + "results { " + f + " } } }"
        gql(s, q, f"results.{f}")

    # Now try the matchList which has different args
    print("\n=== matchList probing ===")
    for f in ["id", "matchNo", "kickOffTime", "homeTeam", "awayTeam", "status"]:
        q = "{ matchList { " + f + " } }"
        gql(s, q, f"matchList.{f}")

    print("\n=== Full valid Match query (no poolInfo) ===")
    full_q = """
    {
      matches(fbOddsTypes: [HDC], startDate: "20260403", endDate: "20260410") {
        id
        matchDate
        matchNumber
        kickOffTime
        endTime
        status
        homeTeam { id name_en name_ch code }
        awayTeam { id name_en name_ch code }
        tournament { id name_en name_ch code }
        runningResult { homeScore awayScore homeCorner awayCorner }
      }
    }
    """
    code, resp = gql(s, full_q, "Full Match no ODDs")
    if code == 200 and resp and resp.get("data", {}).get("matches"):
        print("=== GOT MATCH DATA ===")
        print(json.dumps(resp["data"], indent=2, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
