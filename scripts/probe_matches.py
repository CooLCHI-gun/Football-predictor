"""Probe HKJC GraphQL 'matches' field - discovered from error hint."""
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


def gql(session: requests.Session, query: str, label: str = "") -> None:
    label = label or query[:60]
    try:
        r = session.post(GQL, json={"query": query}, timeout=15)
        print(f"\n[{label}] STATUS={r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "data" in data:
                print(json.dumps(data["data"], indent=2, ensure_ascii=False)[:1200])
            elif "errors" in data:
                for e in data["errors"][:3]:
                    print(f"  ERR: {e.get('message','')[:200]}")
        else:
            print(f"  BODY: {r.text[:300]}")
    except Exception as exc:
        print(f"  EXCEPTION: {exc}")


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)

    # The error hint said "matches" is a valid field
    gql(s, "{ matches { id } }", "matches raw")
    gql(s, "{ matches { matchID homeTeam awayTeam kickOffTime } }", "matches basic")
    gql(s, "{ matches { matchID } }", "matches matchID only")

    # Try with pool type filter for HDC
    gql(s, '{ matches(poolType: "HDC") { matchID homeTeam awayTeam } }', "matches HDC pool")
    gql(s, '{ matches(lang: "zh_HK") { matchID homeTeam awayTeam kickOffTime } }', "matches zh_HK")

    # What other fields exist on Query?
    # Try common GQL field names
    for field in ["fixtures", "events", "schedule", "footballMatches", "competitions",
                  "leagues", "pools", "odds", "market", "matchOdds"]:
        gql(s, "{ " + field + " { id } }", f"probe {field}")


if __name__ == "__main__":
    main()
