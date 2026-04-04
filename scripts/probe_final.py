"""
Probe Line.condition and related fields.
Build the final working HDC query.
"""
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
        r = s.post(GQL, json={"query": query}, timeout=20)
        resp = r.json()
        if r.status_code == 200:
            d = resp.get("data")
            has_data = d and any(v is not None for v in d.values())
            print(f"  {'DATA' if has_data else 'null':4s} [{lbl}]")
            if has_data:
                print(json.dumps(d, indent=2, ensure_ascii=False)[:6000])
        elif r.status_code == 400 and resp.get("errors"):
            for e in resp["errors"][:3]:
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

    LINE_BASE = "{ matches(fbOddsTypes: [HDC]) { foPools(fbOddsTypes: [HDC]) { lines { "
    LINE_END = " } } } }"
    COMBO_BASE = "{ matches(fbOddsTypes: [HDC]) { foPools(fbOddsTypes: [HDC]) { lines { combinations { "
    COMBO_END = " } } } } }"

    print("=== Line.condition and other Line fields ===")
    for f in ["condition", "conditionStr", "lineStr", "value", "label", "desc", "text", "type"]:
        q = LINE_BASE + f + LINE_END
        gql(s, q, f"line.{f}")

    print("\n=== Combination.selections sub-fields (verified) ===")
    for f in ["id", "str", "selId", "currentOdds", "status", "condition", "odds", "openOdds"]:
        q = COMBO_BASE + "selections { " + f + " } " + COMBO_END
        gql(s, q, f"combo.sel.{f}")

    print("\n=== Full Line exploration ===")
    # Also try the FoPool direct for condition
    fp_fields = ["condition", "conditionStr", "lineStr", "label", "text", "type"]
    for f in fp_fields:
        q = "{ matches(fbOddsTypes: [HDC]) { foPools(fbOddsTypes: [HDC]) { " + f + " } } }"
        gql(s, q, f"fopool.{f}")

    print("\n=== Combination type - complete scan ===")
    combo_all = [
        "id", "str", "currentOdds", "status", "winOrd",
        "condition", "conditionStr",
        "content", "value", "text",
        "point", "spread",
        "teamId", "teamCode",
        "side", "type",
        "openOdds",
        "highestOdds",
        "lowestOdds",
    ]
    for f in combo_all:
        q = COMBO_BASE + f + COMBO_END
        gql(s, q, f"combo.{f}")

    print("\n=== Build final HDC query with all discovered fields ===")
    final_q = """
    {
      matches(fbOddsTypes: [HDC], startDate: "20260401", endDate: "20260410") {
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
          id
          oddsType
          status
          matchID
          sportId
          lines {
            id
            lineId
            main
            status
            combinations {
              id
              str
              currentOdds
              status
              winOrd
              selections {
                id
                str
                selId
              }
            }
          }
        }
      }
    }
    """
    code, resp = gql(s, final_q, "FINAL HDC query")
    if code == 200 and resp:
        print("=== RESPONSE ===")
        print(json.dumps(resp, indent=2, ensure_ascii=False)[:8000])


if __name__ == "__main__":
    main()
