#!/usr/bin/env python3
"""
PowerShell Bridge for HKJC GraphQL — replaces direct requests with powershell.exe calls
Used when requests to info.cld.hkjc.com fail with WHITELIST_ERROR (WSL datacenter IP)
"""
import json, subprocess, logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
WSL_WIN_HOME = "C:\\Users\\lccqs\\dev\\playground\\Football-predictor"

def wsl_to_win(path: str | Path) -> str:
    """Convert WSL path to Windows path for PowerShell."""
    p = str(path).replace('/', '\\')
    if p.startswith('\\mnt\\c\\'):
        p = 'C:' + p[6:]
    elif p.startswith('\\mnt\\d\\'):
        p = 'D:' + p[6:]
    return p

def ps_fetch(payload: dict, timeout: int = 30) -> dict[str, Any]:
    """Send a GraphQL request via PowerShell and return parsed JSON response."""
    import os
    
    # Use project artifacts dir (accessible from both WSL and Windows)
    project_root = Path(__file__).resolve().parents[3]  # src/live_feed/providers/ → project root
    if not (project_root / ".git").exists() and not (project_root / "src").exists():
        project_root = Path("/mnt/c/Users/lccqs/dev/playground/Football-predictor")
    
    artifacts = project_root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    
    # Use random suffix to avoid collisions
    import random
    suffix = f"{random.randint(10000, 99999)}"
    payload_file = artifacts / f"_ps_payload_{suffix}.json"
    response_file = artifacts / f"_ps_resp_{suffix}.json"
    
    # Write payload as clean JSON 
    payload_file.write_text(json.dumps(payload))
    
    win_payload = wsl_to_win(payload_file)
    win_response = wsl_to_win(response_file)
    
    ps_script = f'''
$headers = @{{'accept'='*/*';'content-type'='application/json';'origin'='https://bet.hkjc.com';'referer'='https://bet.hkjc.com/';'user-agent'='Mozilla/5.0'}}
$body = Get-Content '{win_payload}' -Raw
try {{
    $r = Invoke-WebRequest 'https://info.cld.hkjc.com/graphql/base/' -Method POST -Headers $headers -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec {timeout}
    $r.Content | Out-File '{win_response}' -Encoding utf8
    Write-Host "OK:$($r.Content.Length)"
}} catch {{
    Write-Host "ERR:$_"
}}
'''
    
    try:
        result = subprocess.run(
            ['powershell.exe', '-Command', ps_script],
            capture_output=True, timeout=timeout + 10
        )
        stdout = result.stdout.decode('utf-16le', errors='replace').strip()
        
        if stdout.startswith('ERR:'):
            LOGGER.error(f"PowerShell bridge error: {stdout}")
            return {}
        
        if response_file.exists() and response_file.stat().st_size > 0:
            raw = response_file.read_text(encoding='utf-8-sig')
            return json.loads(raw) if raw.strip() else {}
        return {}
    except subprocess.TimeoutExpired:
        LOGGER.error("PowerShell bridge timeout after %ss", timeout)
        return {}
    except Exception as e:
        LOGGER.error(f"PowerShell bridge exception: {e}")
        return {}
    finally:
        # Cleanup temp files
        for f in [payload_file, response_file]:
            try:
                if f.exists(): os.unlink(str(f))
            except: pass


def ps_fetch_matches(fb_odds_types: list[str] | None = None,
                     start_index: int = 1, end_index: int = 60,
                     timeout: int = 30) -> dict[str, Any]:
    """Fetch match list with HDC odds via PowerShell bridge.
    
    Uses the matchList query with fbOddsTypes to get HDC/EDC odds.
    Returns the full GraphQL response dict.
    """
    # Use the updated FRONTEND_MATCH_LIST_QUERY (now matches HAR capture)
    from src.live_feed.providers.hkjc_request_debug import FRONTEND_MATCH_LIST_QUERY
    
    fb_odds_types = fb_odds_types or ["HDC", "EDC"]
    variables = {
        "fbOddsTypes": fb_odds_types,
        "fbOddsTypesM": fb_odds_types,
        "featuredMatchesOnly": False,
        "startDate": None, "endDate": None,
        "tournIds": None, "matchIds": None,
        "tournId": None, "tournProfileId": None, "subType": None,
        "startIndex": start_index, "endIndex": end_index,
        "frontEndIds": None, "earlySettlementOnly": False,
        "showAllMatch": False, "tday": None, "tIdList": None,
    }
    
    payload = {
        "query": FRONTEND_MATCH_LIST_QUERY,
        "variables": variables,
    }
    
    return ps_fetch(payload, timeout=timeout)


if __name__ == '__main__':
    # Quick test
    import sys
    print("Testing PowerShell bridge...")
    resp = ps_fetch_matches()
    matches = resp.get("data", {}).get("matches", [])
    hdc = sum(1 for m in matches for p in m.get("foPools", []) if p.get("oddsType") == "HDC")
    print(f"Result: {len(matches)} matches, {hdc} with HDC odds")
    if len(matches) > 0:
        sys.exit(0)
    else:
        print("ERROR: No matches returned")
        sys.exit(1)
