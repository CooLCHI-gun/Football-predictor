"""Probe FoPool fields nested under Match.foPools - this is where HDC odds live."""
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
            d = resp.get("data")
            if d and d.get("matches") is not None:
                print(f"  OK   [{lbl}] data={json.dumps(d, ensure_ascii=False)[:300]}")
            else:
                print(f"  OK   [{lbl}] matches=null")
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

    # Nested foPools path base
    FP_BASE = "{ matches(fbOddsTypes: [HDC]) { foPools(fbOddsTypes: [HDC]) { "
    FP_END = " } } }"

    print("=== FoPool fields via matches.foPools ===")
    fp_fields = [
        "id", "oddsType", "status",
        "matchID", "poolId",
        "sportId",
        "match",  # should OBJ
        "selections",  # might be array
        "selection",
        "line", "handicapLine", "handicap",
        "home", "away",
        "homeOdds", "awayOdds",
        "h", "a",
        "oddsH", "oddsA",
        "currentOdds",
        "openOdds",
        "winOdds",
        "conditions",
        "lineInput",
    ]
    for f in fp_fields:
        q = FP_BASE + f + FP_END
        gql(s, q, f"foPools.{f}")

    print("\n=== FoPool.selections sub-fields ===")
    sel_fields = ["id", "selId", "str", "currentOdds", "odds", "line", "handicap"]
    for f in sel_fields:
        q = FP_BASE + "selections { " + f + " } " + FP_END
        gql(s, q, f"foPools.selections.{f}")

    print("\n=== liveEvents sub-fields ===")
    # liveEvents was hinted earlier
    live_fields = ["eventType", "eventTime", "teamId", "team", "score"]
    for f in live_fields:
        q = "{ matches(fbOddsTypes: [HDC]) { liveEvents { " + f + " } } }"
        gql(s, q, f"liveEvents.{f}")

    print("\n=== PoolInfo.chpType + more fields ===")
    # We know chpType works on PoolInfo - find siblings
    poolinfo_extra = [
        "chpType", "match",
        "bankerOdds", "unitBet",
        "noOfCombination",
    ]
    for f in poolinfo_extra:
        q = "{ matches(fbOddsTypes: [HDC]) { poolInfo { " + f + " } } }"
        gql(s, q, f"poolInfo.{f}")

    print("\n=== Full comprehensive query attempt ===")
    # Build best guess query with all confirmed valid fields
    comprehensive = """
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
        results { homeResult awayResult resultType }
        foPools(fbOddsTypes: [HDC]) { id oddsType status }
      }
    }
    """
    code, resp = gql(s, comprehensive, "Full comprehensive")
    if code == 200 and resp:
        print(json.dumps(resp, indent=2, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
