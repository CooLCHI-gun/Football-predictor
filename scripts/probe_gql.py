"""Probe HKJC GraphQL endpoint with football-specific queries."""
from __future__ import annotations
import json
import re
import requests

GQL = "https://info.cld.hkjc.com/graphql/base/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Referer": "https://bet.hkjc.com/ch/football/hdc",
    "Origin": "https://bet.hkjc.com",
}

FOOTBALL_QUERIES = [
    # Standard patterns used by HKJC apps
    "{ footballSchedule { matchID homeTeam awayTeam kickOffTime competitionCode } }",
    "{ football { matches { matchID homeTeam awayTeam kickOffTime } } }",
    "{ footballMatchList { matchID homeTeam { nameEn } awayTeam { nameEn } kickOffTime } }",
    "{ fbMatches { matchID homeTeam awayTeam kickOffTime poolInfo { hdcOdds { home away line } } } }",
    # Try aliases seen in HKJC JS apps
    "{ footballData(lang: \"zh_HK\") { matchID homeTeam awayTeam kickOffTime } }",
    "{ fb { schedule { matchID homeTeam awayTeam kickOffTime hdc { home away line } } } }",
    # Very simple exploratory
    "{ __typename }",
]


def gql_post(session: requests.Session, query: str) -> None:
    try:
        r = session.post(GQL, json={"query": query}, timeout=12)
        body = r.text[:400]
        print(f"  STATUS={r.status_code}")
        if r.status_code == 200:
            try:
                data = r.json()
                if "data" in data and data["data"]:
                    print(f"  DATA KEYS: {list(data['data'].keys())}")
                    print(f"  FULL: {json.dumps(data['data'], ensure_ascii=False)[:600]}")
                elif "errors" in data:
                    err_msg = data["errors"][0].get("message", "") if data["errors"] else ""
                    print(f"  ERROR: {err_msg[:200]}")
                else:
                    print(f"  RAW: {body}")
            except Exception:
                print(f"  RAW: {body}")
        else:
            print(f"  BODY: {body}")
    except Exception as exc:
        print(f"  EXCEPTION: {exc}")


def scan_js_for_queries(session: requests.Session) -> None:
    """Load a large JS bundle from bet.hkjc.com and scan for GraphQL queries."""
    # Fetch the page to get chunk script paths
    r = session.get("https://bet.hkjc.com/ch/football/hdc", timeout=20)
    links = re.findall(r'<link[^>]+href=["\']([^"\']+\.js)["\']', r.text)
    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', r.text)
    all_js = links + scripts
    print(f"Found {len(all_js)} JS resources on main page")
    print(r.text[:1500])
    print("---")
    for js_path in all_js[:12]:
        full = js_path if js_path.startswith("http") else "https://bet.hkjc.com" + js_path
        try:
            rjs = session.get(full, timeout=15)
            # Look for gql query patterns
            gql_strings = re.findall(r'query\s+(\w+)\s*\{', rjs.text)
            gql_strings += re.findall(r'"query"\s*:\s*"([^"]{10,200})"', rjs.text)
            field_refs = re.findall(r'footballOdds|hdcOdds|footballMatch|fbMatch|matchID|kickOffTime', rjs.text)
            if gql_strings or field_refs:
                print(f"\nJS: {full[:80]}")
                print(f"  GQL queries: {gql_strings[:5]}")
                print(f"  Field refs: {set(field_refs[:10])}")
        except Exception as exc:
            print(f"  SKIP {full[:60]}: {exc}")


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("=== Scan JS for GraphQL query names ===")
    scan_js_for_queries(session)

    print("\n=== Try known GraphQL queries ===")
    for q in FOOTBALL_QUERIES:
        print(f"\nQuery: {q[:80]}")
        gql_post(session, q)


if __name__ == "__main__":
    main()
