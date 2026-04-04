"""Extract actual GraphQL query from HKJC football layout JS bundle."""
from __future__ import annotations
import re
import requests

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-HK,zh;q=0.9",
    "Referer": "https://football.hkjc.com/",
})

url = "https://football.hkjc.com/_next/static/chunks/app/%5Blng%5D/layout-298bfad5dfc5674f.js"
r = s.get(url, timeout=30)
js = r.text
print(f"JS length: {len(js)}")

# Find fbOddsTypes context
positions = [m.start() for m in re.finditer(r"fbOddsTypes", js)]
print(f"fbOddsTypes occurrences: {len(positions)}")
for pos in positions[:2]:
    chunk = js[max(0, pos - 300) : pos + 600]
    print(f"\n--- pos={pos} ---")
    print(repr(chunk))

# Find kickOffTime context
pos = js.find("kickOffTime")
if pos >= 0:
    print(f"\n--- kickOffTime at pos={pos} ---")
    print(repr(js[max(0, pos - 400) : pos + 600]))

# Look for template literal or string containing GraphQL queries
# GQL in Next.js is often stored as tagged template literals: gql`...`
# or as string with "query" keyword
gql_tag_matches = re.findall(r'gql`([^`]+)`', js)
if gql_tag_matches:
    print(f"\nGQL tag templates: {len(gql_tag_matches)}")
    for q in gql_tag_matches[:5]:
        print(f"\n{q[:600]}")

# Also look for document nodes stored as query strings
query_strings = re.findall(r'"((?:query|fragment)\s+[^"]{30,})"', js)
if query_strings:
    print(f"\nQuery strings: {len(query_strings)}")
    for q in query_strings[:5]:
        print(q[:600])

# Also search for 'combinations' context
pos2 = js.find("combinations")
if pos2 >= 0:
    print(f"\n--- combinations at pos={pos2} ---")
    print(repr(js[max(0, pos2 - 200) : pos2 + 400]))

# Look for currentOdds
pos3 = js.find("currentOdds")
if pos3 >= 0:
    print(f"\n--- currentOdds at pos={pos3} ---")
    print(repr(js[max(0, pos3 - 200) : pos3 + 400]))
