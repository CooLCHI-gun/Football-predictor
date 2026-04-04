"""Deep HKJC GraphQL Match schema probe - find all valid fields."""
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
    try:
        r = s.post(GQL, json={"query": query}, timeout=15)
        resp = r.json()
        return r.status_code, resp
    except Exception as exc:
        print(f"  EXCEPTION: {exc}")
        return 0, None


def probe_field(s: requests.Session, field: str) -> None:
    q = "{ matches(fbOddsTypes: [HDC]) { " + field + " } }"
    code, resp = gql(s, q)
    if code == 200:
        print(f"  OK   {field}")
    elif code == 400 and resp:
        err = resp["errors"][0]["message"] if resp.get("errors") else "?"
        if "Did you mean" in err:
            print(f"  HINT {field}: {err[:150]}")
        elif "must have a selection" in err:
            print(f"  OBJ  {field}  (is object type)")
        else:
            print(f"  BAD  {field}: {err[:100]}")


def probe_sub_field(s: requests.Session, parent: str, field: str) -> None:
    q = "{ matches(fbOddsTypes: [HDC]) { " + parent + " { " + field + " } } }"
    code, resp = gql(s, q)
    if code == 200:
        print(f"    OK   {parent}.{field}")
    elif code == 400 and resp:
        err = resp["errors"][0]["message"] if resp.get("errors") else "?"
        if "Did you mean" in err:
            print(f"    HINT {parent}.{field}: {err[:120]}")
        elif "must have a selection" in err:
            print(f"    OBJ  {parent}.{field}  (nested object)")
        else:
            print(f"    BAD  {parent}.{field}: {err[:80]}")


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)

    print("=== Probing Match fields ===")
    match_fields = [
        "id", "no", "num", "matchNum", "seq",
        "kickOffTime", "matchDate", "date", "time", "datetime",
        "status", "statusCode", "matchStatus",
        "homeTeam", "awayTeam",
        "competition", "league", "tournament", "pool",
        "poolId", "competitionCode", "leagueCode",
        "venue", "venueName", "location",
        "liveScore", "score", "result",
        "poolInfo", "oddsInfo", "odds", "hdc",
        "inplay", "live",
        "isInplay", "isLive",
        "runningResult", "halfTimeResult",
        "fbMatchID", "matchID",
    ]
    for f in match_fields:
        probe_field(s, f)

    print("\n=== Probing Team sub-fields ===")
    team_fields = [
        "id", "name_en", "name_ch", "name", "code", "shortName",
        "nameEn", "nameCh", "abbr",
    ]
    for f in team_fields:
        probe_sub_field(s, "homeTeam", f)

    print("\n=== Try matches with date range ===")
    # Try date filter arguments
    date_args = [
        'dateFrom: "20260403", dateTo: "20260404"',
        'startDate: "20260403", endDate: "20260404"',
        'date: "20260403"',
        'pageno: 1',
        'page: 1',
        'startIndex: 0',
    ]
    for arg in date_args:
        q = '{ matches(fbOddsTypes: [HDC], ' + arg + ') { id } }'
        code, resp = gql(s, q)
        if code == 200:
            d = resp.get("data", {}) if resp else {}
            print(f"  OK   arg={arg} data={d}")
        elif code == 400 and resp:
            err = resp["errors"][0]["message"] if resp.get("errors") else "?"
            if "Unknown argument" in err:
                print(f"  NO   arg={arg}")
            else:
                print(f"  ERR  arg={arg}: {err[:100]}")

    print("\n=== Try matchList with HDC ===")
    q = "{ matchList(fbOddsTypes: [HDC]) { id } }"
    code, resp = gql(s, q)
    print(f"  matchList(HDC) HTTP={code} data={resp}")

    print("\n=== Try foPools ===")
    q = "{ foPools(fbOddsTypes: [HDC]) { id } }"
    code, resp = gql(s, q)
    if code == 200:
        print(f"  foPools(HDC): {json.dumps(resp, ensure_ascii=False)[:500]}")
    else:
        if resp and resp.get("errors"):
            err = resp["errors"][0]["message"]
            print(f"  foPools ERR: {err[:200]}")

    print("\n=== Probe foPools sub-fields ===")
    pool_fields = ["id", "oddsType", "status", "name", "matches", "match",
                   "noOfLegs", "poolId"]
    for f in pool_fields:
        q = "{ foPools(fbOddsTypes: [HDC]) { " + f + " } }"
        code, resp = gql(s, q)
        if code == 200:
            print(f"  OK   foPools.{f}")
        elif code == 400 and resp:
            err = resp["errors"][0]["message"] if resp.get("errors") else "?"
            if "Did you mean" in err:
                print(f"  HINT foPools.{f}: {err[:120]}")
            elif "must have a selection" in err:
                print(f"  OBJ  foPools.{f}")
            else:
                print(f"  BAD  foPools.{f}: {err[:80]}")


if __name__ == "__main__":
    main()
