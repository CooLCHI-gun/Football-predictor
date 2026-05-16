#!/usr/bin/env python3
"""
HKJC Market Depth Feature Extractor
Extracts features from HKJC HDC odds data for model training.
Call: python hkjc_market_features.py <path_to_hkjc_odds.json>
Output: CSV with market depth features ready for model pipeline
"""
import json, sys, pandas as pd
from pathlib import Path

def extract_market_depth_features(odds_path: Path) -> pd.DataFrame:
    """Extract market depth features from HKJC HDC odds response."""
    with open(odds_path, encoding='utf-8-sig') as f:
        resp = json.loads(f.read())
    
    matches = resp.get('data', {}).get('matches', [])
    features = []
    
    for m in matches:
        home = m.get('homeTeam', {}).get('name_en', '')
        away = m.get('awayTeam', {}).get('name_en', '')
        
        row = {
            'match_id': m.get('id'),
            'home_team': home,
            'away_team': away,
            'status': m.get('status', ''),
            'match_date': m.get('matchDate', ''),
            'tournament': m.get('tournament', {}).get('name_en', '') if m.get('tournament') else '',
        }
        
        for pool in m.get('foPools', []):
            if pool.get('oddsType') != 'HDC':
                continue
            
            lines = pool.get('lines', [])
            row['hdc_pool_status'] = pool.get('status', '')
            row['hdc_num_lines'] = len(lines)
            row['hdc_active'] = 1 if pool.get('status') == 'SELLINGSTARTED' else 0
            
            for i, l in enumerate(lines[:5]):
                cond = l.get('condition', '')
                h_odds = a_odds = None
                for c in l.get('combinations', []):
                    if c.get('str') == 'H': h_odds = c.get('currentOdds')
                    elif c.get('str') == 'A': a_odds = c.get('currentOdds')
                
                row[f'hdc_l{i+1}_cond'] = cond
                if h_odds: row[f'hdc_l{i+1}_h_odds'] = float(h_odds)
                if a_odds: row[f'hdc_l{i+1}_a_odds'] = float(a_odds)
                if h_odds and a_odds:
                    row[f'hdc_l{i+1}_spread'] = abs(float(h_odds) - float(a_odds))
            
            # Main line implied probability
            h = row.get('hdc_l1_h_odds')
            a = row.get('hdc_l1_a_odds')
            if h and a:
                p_h = 1.0 / h; p_a = 1.0 / a
                row['implied_prob_home'] = p_h / (p_h + p_a)
                row['implied_prob_away'] = p_a / (p_h + p_a)
                row['market_margin'] = p_h + p_a - 1.0
            
            # Feature: how many extra lines beyond main
            for level in [1, 2, 3]:
                key = f'hdc_l{level}_h_odds'
                row[f'has_line_{level}'] = 1 if key in row else 0
        
        features.append(row)
    
    return pd.DataFrame(features)


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'artifacts/hkjc_har_response.json'
    df = extract_market_depth_features(Path(path))
    out = path.rsplit('.', 1)[0] + '_features.csv'
    df.to_csv(out, index=False)
    print(f"Extracted {len(df)} rows with {len(df.columns)} features -> {out}")
    print(f"Numerical features: {[c for c in df.columns if df[c].dtype in ('float64','int64')]}")
