"""Probe HKJC GraphQL schema - deep exploration of Match type fields."""
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


def gql(s: requests.Session, query: str, lbl: str = "") -> dict | None:
    lbl = lbl or query[:60]
    try:
        r = s.post(GQL, json={"query": query}, timeout=15)
        print(f"\n[{lbl}] HTTP={r.status_code}")
        resp = r.json()
        if "data" in resp and resp["data"]:
            print(json.dumps(resp["data"], indent=2, ensure_ascii=False)[:2000])
            return resp["data"]
        elif "errors" in resp:
            for e in resp["errors"][:4]:
                print(f"  ERR: {e.get('message','')[:250]}")
        else:
            print(f"  BODY: {r.text[:300]}")
    except Exception as exc:
        print(f"  EXCEPTION: {type(exc).__name__}: {exc}")
    return None


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)

    # 1. matches with fbOddsTypes=[HDC]
    gql(s, '{ matches(fbOddsTypes: [HDC]) { id } }', "matches HDC id")

    # Try different enum values
    for val in ["HDC", "HAD", "FHA", "OOE", "CHL"]:
        gql(s, '{ matches(fbOddsTypes: [' + val + ']) { id } }', f"matches {val}")

    # 2. matchList
    gql(s, "{ matchList { id } }", "matchList id")
    gql(s, "{ matchList { matchID } }", "matchList matchID")

    # 3. foPools
    gql(s, "{ foPools { id } }", "foPools id")

    # 4. Try the Match type with common football field names
    common_fields = [
        "id", "eventId", "matchId", "fixtureId",
        "homeTeam { name nameEn nameCh }",
        "awayTeam { name nameEn nameCh }",
        "kickOffTime",  "matchDate", "startTime",
        "competition { name code }",
        "league { name }",
        "status",
    ]
    for field in common_fields:
        # Try each sub-field on matches with HDC type
        q = "{ matches(fbOddsTypes: [HDC]) { " + field + " } }"
        gql(s, q, f"matches.{field.split('{')[0].strip()}")

    # 5. After discovering valid id, try a full query
    gql(
        s,
        '{ matches(fbOddsTypes: [HDC]) { id homeTeam { name } awayTeam { name } kickOffTime } }',
        "full HDC query"
    )


if __name__ == "__main__":
    main()
