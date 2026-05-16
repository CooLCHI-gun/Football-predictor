#!/usr/bin/env python3
"""
HKJC Daily Data Collector — cron-job friendly version.
Fetches live HDC odds + completed results from HKJC, saves to data/raw/hkjc/.

Called by cronjob tool daily. Outputs summary for delivery.
"""
import json, subprocess, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path("/mnt/c/Users/lccqs/dev/playground/Football-predictor")
os.chdir(ROOT)

HKT = timezone(timedelta(hours=8))
today = datetime.now(HKT).strftime("%Y-%m-%d")
now_hour = datetime.now(HKT).hour

def W(path):
    return f"C:\\Users\\lccqs\\dev\\playground\\Football-predictor\\{path}"

def ps(script, timeout=60):
    try:
        r = subprocess.run(['powershell.exe', '-Command', script], capture_output=True, timeout=timeout, cwd=ROOT)
        return r.stdout.decode('utf-16le', errors='replace')
    except:
        return "TIMEOUT/ERROR"

# Create data dirs
(ROOT / "data" / "raw" / "hkjc").mkdir(parents=True, exist_ok=True)

report = []

# ===== 1. Fetch live HDC odds =====
report.append(f"[{today}] Step 1: Live HDC odds")
payload = json.loads((ROOT / "artifacts" / "hkjc_har_payload.json").read_text())
payload['variables']['startIndex'] = 1
payload['variables']['endIndex'] = 60
(ROOT / "artifacts" / "_cron_hdc_payload.json").write_text(json.dumps(payload))

out = ps(f'''
$h = @{{'accept'='*/*';'content-type'='application/json';'origin'='https://bet.hkjc.com';'referer'='https://bet.hkjc.com/';'user-agent'='Mozilla/5.0'}}
$body = Get-Content '{W("artifacts/_cron_hdc_payload.json")}' -Raw
$r = Invoke-WebRequest 'https://info.cld.hkjc.com/graphql/base/' -Method POST -Headers $h -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30
$r.Content | Out-File '{W("data/raw/hkjc/2026-05-17_live_odds.json")}' -Encoding utf8
$d = $r.Content | ConvertFrom-Json
$c = $d.data.matches.Count
$hdc = 0; foreach ($m in $d.data.matches) {{ foreach ($p in $m.foPools) {{ if ($p.oddsType -eq "HDC") {{ $hdc++; break }} }} }}
Write-Host ("LIVE:" + $c + "|HDC:" + $hdc)
''')

report.append(f"  {out.strip()}")

# ===== 2. Fetch completed results =====
report.append(f"Step 2: Completed results")
match_results_q = json.loads((ROOT / "artifacts" / "hkjc_matchResults_query.json").read_text())['query']
payload2 = {'query': match_results_q, 'variables': {'startDate': None, 'endDate': None, 'startIndex': None, 'endIndex': None, 'teamId': None}}
(ROOT / "artifacts" / "_cron_results_payload.json").write_text(json.dumps(payload2))

out = ps(f'''
$h = @{{'accept'='*/*';'content-type'='application/json';'origin'='https://bet.hkjc.com';'referer'='https://bet.hkjc.com/';'user-agent'='Mozilla/5.0'}}
$body = Get-Content '{W("artifacts/_cron_results_payload.json")}' -Raw
$r = Invoke-WebRequest 'https://info.cld.hkjc.com/graphql/base/' -Method POST -Headers $h -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30
$r.Content | Out-File '{W("data/raw/hkjc/2026-05-17_results.json")}' -Encoding utf8
$d = $r.Content | ConvertFrom-Json
$c = $d.data.matches.Count
$scores = 0
foreach ($m in $d.data.matches) {{ if ($m.results -and $m.results[0].homeResult) {{ $scores++ }} }}
Write-Host ("RESULTS:" + $c + "|WITH_SCORES:" + $scores)
''')

report.append(f"  {out.strip()}")

# ===== 3. Parse results for summary =====
report.append(f"Step 3: Summary")
try:
    resp = json.loads((ROOT / "data" / "raw" / "hkjc" / "2026-05-17_results.json").read_text(encoding='utf-8-sig'))
    matches = resp.get('data', {}).get('matches', [])
    with_scores = sum(1 for m in matches if m.get('results') and m['results'][0].get('homeResult'))
    report.append(f"  Total matches: {len(matches)}, with scores: {with_scores}")
    
    # Show sample results
    for m in matches[:5]:
        if m.get('results') and m['results'][0].get('homeResult'):
            r = m['results'][0]
            report.append(f"    {m.get('homeTeam',{}).get('name_en','?'):25s} {r['homeResult']}-{r['awayResult']} {m.get('awayTeam',{}).get('name_en','?'):25s}")
except:
    report.append("  (no results data)")

try:
    odds_resp = json.loads((ROOT / "data" / "raw" / "hkjc" / "2026-05-17_live_odds.json").read_text(encoding='utf-8-sig'))
    odds_matches = odds_resp.get('data', {}).get('matches', [])
    hdc = sum(1 for m in odds_matches for p in m.get('foPools',[]) if p.get('oddsType') == 'HDC')
    report.append(f"  Live matches: {len(odds_matches)}, with HDC: {hdc}")
except:
    report.append("  (no odds data)")

# Cleanup temp files
for f in ROOT.glob("artifacts/_cron_*.json"):
    f.unlink()

print("\n".join(report))
sys.exit(0)
