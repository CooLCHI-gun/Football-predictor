#!/usr/bin/env python3
"""
HKJC Daily Data Collector
Fetches live HDC odds + recent results, appends to growing dataset.
Run via cronjob daily at 10:00 HKT.
"""
import json, subprocess, sys, os
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path("/mnt/c/Users/lccqs/dev/playground/Football-predictor")
DATA_DIR = ROOT / "data" / "raw" / "hkjc"
DAILY_DIR = DATA_DIR / "daily"
RESULTS_DIR = DATA_DIR / "results"
os.chdir(ROOT)

DAILY_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

HKT = timezone(timedelta(hours=8))
today = datetime.now(HKT).strftime("%Y-%m-%d")

def W(path):
    return f"C:\\Users\\lccqs\\dev\\playground\\Football-predictor\\{path}"

def run_ps(script, timeout=120):
    """Run a PowerShell command and return decoded stdout."""
    result = subprocess.run(
        ['powershell.exe', '-Command', script],
        capture_output=True, timeout=timeout, cwd=ROOT
    )
    return result.stdout.decode('utf-16le', errors='replace')

# Step 1: Fetch live HDC odds
print(f"[{today}] Step 1: Fetching live HDC odds...")

# Build payload for matchList query with HDC odds
har_query = json.loads((ROOT / "artifacts" / "hkjc_har_query.json").read_text())
query = har_query['query']
payload = {
    'query': query,
    'variables': {
        'fbOddsTypes': ['HDC', 'EDC'],
        'fbOddsTypesM': ['HDC', 'EDC'],
        'featuredMatchesOnly': False,
        'startDate': None, 'endDate': None,
        'tournIds': None, 'matchIds': None,
        'startIndex': 1, 'endIndex': 100,
        'frontEndIds': None, 'earlySettlementOnly': False,
        'showAllMatch': False, 'inplayOnly': False,
    }
}

payload_path = ROOT / "artifacts" / f"daily_payload_{today}.json"
resp_path = ROOT / "artifacts" / f"daily_resp_{today}.json"
payload_path.write_text(json.dumps(payload))

ps = f'''
$headers = @{{'accept'='*/*';'content-type'='application/json';'origin'='https://bet.hkjc.com';'referer'='https://bet.hkjc.com/';'user-agent'='Mozilla/5.0'}}
$body = Get-Content '{W(f"artifacts/daily_payload_{today}.json")}' -Raw
$r = Invoke-WebRequest 'https://info.cld.hkjc.com/graphql/base/' -Method POST -Headers $headers -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30
$r.Content | Out-File '{W(f"artifacts/daily_resp_{today}.json")}' -Encoding utf8
$d = $r.Content | ConvertFrom-Json
Write-Host ("MATCHES:" + $d.data.matches.Count)
$hdc = 0; foreach ($m in $d.data.matches) {{ foreach ($p in $m.foPools) {{ if ($p.oddsType -eq "HDC") {{ $hdc++; break }} }} }}
Write-Host ("HDC:" + $hdc)
'''

out = run_ps(ps)
lines = [l.strip() for l in out.split('\n') if l.strip()]
print(f"  {', '.join(lines[-2:])}")

# Copy to daily archive
if resp_path.exists() and resp_path.stat().st_size > 0:
    archive_path = DAILY_DIR / f"{today}_hdc_odds.json"
    import shutil
    shutil.copy2(resp_path, archive_path)
    print(f"  Saved: {archive_path}")

# Step 2: Fetch recent historical results
print(f"  Step 2: Fetching historical results...")

match_results_q = json.loads((ROOT / "artifacts" / "hkjc_matchResults_query.json").read_text())['query']
results_payload = {
    'query': match_results_q,
    'variables': {
        'startDate': None, 'endDate': None,
        'startIndex': None, 'endIndex': None, 'teamId': None,
    }
}

(ROOT / "artifacts" / f"daily_results_payload_{today}.json").write_text(json.dumps(results_payload))

ps2 = f'''
$headers = @{{'accept'='*/*';'content-type'='application/json';'origin'='https://bet.hkjc.com';'referer'='https://bet.hkjc.com/';'user-agent'='Mozilla/5.0'}}
$body = Get-Content '{W(f"artifacts/daily_results_payload_{today}.json")}' -Raw
$r = Invoke-WebRequest 'https://info.cld.hkjc.com/graphql/base/' -Method POST -Headers $headers -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30
$r.Content | Out-File '{W(f"artifacts/daily_results_resp_{today}.json")}' -Encoding utf8
$d = $r.Content | ConvertFrom-Json
Write-Host ("RESULTS:" + $d.data.matches.Count)
'''

out2 = run_ps(ps2)
lines2 = [l.strip() for l in out2.split('\n') if l.strip()]
print(f"  {', '.join(lines2[-1:])}")

# Copy results
results_resp = ROOT / "artifacts" / f"daily_results_resp_{today}.json"
if results_resp.exists() and results_resp.stat().st_size > 0:
    archive_results = RESULTS_DIR / f"{today}_results.json"
    import shutil
    shutil.copy2(results_resp, archive_results)
    print(f"  Saved: {archive_results}")

# Step 3: Build/update cumulative training CSV
print(f"  Step 3: Building cumulative training data...")

# Collect all historical results files
all_results_files = sorted(RESULTS_DIR.glob("*_results.json"))
all_matches = []
seen_ids = set()

for rf in all_results_files:
    try:
        data = json.loads(rf.read_text(encoding='utf-8-sig'))
        matches = data.get('data', {}).get('matches', [])
        for m in matches:
            mid = m.get('id')
            if mid and mid not in seen_ids:
                # Check if it has results (scores)
                results = m.get('results', []) or []
                if results and results[0].get('homeResult') is not None:
                    seen_ids.add(mid)
                    hr = int(results[0]['homeResult'])
                    ar = int(results[0]['awayResult'])
                    all_matches.append({
                        'id': mid,
                        'date': m.get('matchDate', ''),
                        'home_team': m.get('homeTeam', {}).get('name_en', ''),
                        'away_team': m.get('awayTeam', {}).get('name_en', ''),
                        'tournament': m.get('tournament', {}).get('name_en', '') if m.get('tournament') else '',
                        'home_goals': hr,
                        'away_goals': ar,
                        'goal_diff': hr - ar,
                    })
    except:
        pass

print(f"  Total historical matches with scores: {len(all_matches)}")

# Save cumulative
import pandas as pd
if all_matches:
    hist_df = pd.DataFrame(all_matches)
    hist_csv = DATA_DIR / "hkjc_historical_results_cumulative.csv"
    hist_df.to_csv(hist_csv, index=False)
    print(f"  Saved: {hist_csv} ({len(hist_df)} rows)")
    
    # Show summary
    home_wins = (hist_df['goal_diff'] > 0).sum()
    draws = (hist_df['goal_diff'] == 0).sum()
    away_wins = (hist_df['goal_diff'] < 0).sum()
    print(f"  Home wins: {home_wins}, Draws: {draws}, Away wins: {away_wins}")

# Step 4: Extract market depth features from daily HDC odds
print(f"  Step 4: Extracting market depth features...")

all_daily_files = sorted(DAILY_DIR.glob("*_hdc_odds.json"))
hdc_data = []

for df_path in all_daily_files:
    try:
        data = json.loads(df_path.read_text(encoding='utf-8-sig'))
        matches = data.get('data', {}).get('matches', [])
        for m in matches:
            home = m.get('homeTeam', {}).get('name_en', '')
            away = m.get('awayTeam', {}).get('name_en', '')
            
            for pool in m.get('foPools', []):
                if pool.get('oddsType') != 'HDC':
                    continue
                
                lines = pool.get('lines', [])
                pool_status = pool.get('status', '')
                
                hdc_lines = []
                for l in lines[:5]:
                    condition = l.get('condition', '')
                    h_odds = None
                    a_odds = None
                    for c in l.get('combinations', []):
                        if c.get('str') == 'H':
                            h_odds = c.get('currentOdds')
                        elif c.get('str') == 'A':
                            a_odds = c.get('currentOdds')
                    hdc_lines.append({
                        'condition': condition,
                        'home_odds': h_odds,
                        'away_odds': a_odds,
                    })
                
                if hdc_lines:
                    hdc_data.append({
                        'date': df_path.stem[:10],
                        'match_id': m.get('id'),
                        'home_team': home,
                        'away_team': away,
                        'status': m.get('status', ''),
                        'pool_status': pool_status,
                        'num_lines': len(lines),
                        'lines': hdc_lines,
                    })
    except:
        pass

if hdc_data:
    # Save full depth data
    depth_path = DATA_DIR / "hkjc_market_depth.json"
    with open(depth_path, 'w') as f:
        json.dump(hdc_data, f, indent=2)
    
    # Create feature summary
    features = []
    for d in hdc_data:
        row = {
            'date': d['date'],
            'match_id': d['match_id'],
            'home_team': d['home_team'],
            'away_team': d['away_team'],
            'status': d['status'],
            'num_hdc_lines': d['num_lines'],
            'pool_active': 1 if d['pool_status'] == 'SELLINGSTARTED' else 0,
        }
        
        # Extract odds features
        lines = d.get('lines', [])
        if len(lines) >= 1:
            row['hdc_line_1_condition'] = lines[0]['condition']
            row['hdc_line_1_home_odds'] = lines[0]['home_odds']
            row['hdc_line_1_away_odds'] = lines[0]['away_odds']
            if lines[0]['home_odds'] and lines[0]['away_odds']:
                row['hdc_line_1_spread'] = abs(float(lines[0]['home_odds']) - float(lines[0]['away_odds']))
        if len(lines) >= 2:
            row['hdc_line_2_home_odds'] = lines[1]['home_odds']
            row['hdc_line_2_away_odds'] = lines[1]['away_odds']
        if len(lines) >= 3:
            row['hdc_line_3_home_odds'] = lines[2]['home_odds']
            row['hdc_line_3_away_odds'] = lines[2]['away_odds']
        
        features.append(row)
    
    feat_df = pd.DataFrame(features)
    feat_csv = DATA_DIR / "hkjc_market_depth_features.csv"
    feat_df.to_csv(feat_csv, index=False)
    print(f"  Market depth features: {feat_csv} ({len(feat_df)} rows)")
    
    # Show latest
    latest_date = feat_df['date'].max()
    latest_count = len(feat_df[feat_df['date'] == latest_date])
    print(f"  Latest ({latest_date}): {latest_count} matches, avg lines={feat_df[feat_df['date']==latest_date]['num_hdc_lines'].mean():.1f}")

# Cleanup temp files
for f in ROOT.glob(f"artifacts/daily_*_{today}.json"):
    f.unlink()

print(f"\nDone. Data saved to {DATA_DIR}")
sys.exit(0)
