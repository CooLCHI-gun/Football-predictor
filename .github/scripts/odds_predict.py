#!/usr/bin/env python3
"""
GitHub Actions: Fetch odds from The Odds API → run model → send Telegram alerts.
Called by daily-train.yml after model training.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib import parse, request
from typing import Any

import numpy as np
import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.models.baselines import load_model_bundle, get_feature_columns, build_feature_frame


# ─── Config ───────────────────────────────────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
RUN_ID = os.environ.get("RUN_ID", f"gha_predict_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
EDGE_THRESHOLD = float(os.environ.get("EDGE_THRESHOLD", "0.02"))
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.05"))
MAX_ALERTS = int(os.environ.get("MAX_ALERTS", "5"))

# Leagues to track — all have spreads available via Matchbook
SOCCER_LEAGUES = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga2",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_usa_mls",
]

LEAGUE_LABELS = {
    "soccer_epl": "EPL",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_germany_bundesliga2": "Bundesliga 2",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_uefa_champs_league": "UCL",
    "soccer_usa_mls": "MLS",
}


# ─── API Client ────────────────────────────────────────────────
def fetch_odds(sport: str) -> list[dict[str, Any]]:
    """Fetch upcoming matches with spreads for a sport key."""
    params = parse.urlencode({
        "apiKey": ODDS_API_KEY,
        "regions": "uk",
        "markets": "spreads,h2h",
        "oddsFormat": "decimal",
        "bookmakers": "matchbook",
    })
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds?{params}"
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and "message" in data:
        print(f"  API error: {data['message']}")
        return []
    return data if isinstance(data, list) else []


def send_telegram(text: str) -> None:
    """Send message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  Telegram not configured, skipping")
        return
    payload = parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        req = request.Request(url, data=payload, method="POST")
        with request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        if '"ok":true' not in body.replace(" ", ""):
            print(f"  Telegram API non-ok: {body[:200]}")
        else:
            print("  Telegram sent")
    except Exception as e:
        print(f"  Telegram send error: {e}")


# ─── Prediction Logic ──────────────────────────────────────────
def build_match_rows(matches: list[dict[str, Any]], league_key: str) -> pd.DataFrame:
    """Convert Odds API matches to feature DataFrame rows."""
    rows: list[dict[str, float | str | None]] = []
    for match in matches:
        bm = match.get("bookmakers", [])
        if not bm:
            continue
        # Extract spreads
        spreads_market = None
        h2h_market = None
        for m in bm[0].get("markets", []):
            if m["key"] == "spreads":
                spreads_market = m
            elif m["key"] == "h2h":
                h2h_market = m

        if not spreads_market:
            continue

        outcomes = spreads_market.get("outcomes", [])
        spread_home = next((o for o in outcomes if o.get("name") == match["home_team"]), None)
        spread_away = next((o for o in outcomes if o.get("name") == match["away_team"]), None)

        h2h_outcomes = h2h_market.get("outcomes", []) if h2h_market else []
        h2h_home = next((o for o in h2h_outcomes if o.get("name") == match["home_team"]), None)
        h2h_away = next((o for o in h2h_outcomes if o.get("name") == match["away_team"]), None)
        h2h_draw = next((o for o in h2h_outcomes if o.get("name") == "Draw"), None)

        if not spread_home or not spread_away:
            continue

        handicap_line = -float(spread_home.get("point", 0))  # Convert to home-centric
        odds_home = float(spread_home["price"])
        odds_away = float(spread_away["price"])

        # Calculate implied probabilities
        implied_home = 1.0 / odds_home if odds_home > 0 else 0.5
        implied_away = 1.0 / odds_away if odds_away > 0 else 0.5

        # H2H implied prob for reference
        h2h_implied = {}
        if h2h_home:
            h2h_implied["h2h_home_price"] = float(h2h_home["price"])
        if h2h_away:
            h2h_implied["h2h_away_price"] = float(h2h_away["price"])
        if h2h_draw:
            h2h_implied["h2h_draw_price"] = float(h2h_draw["price"])

        rows.append({
            "provider_match_id": match["id"],
            "match_id": match["id"],
            "match_number": "",
            "competition": LEAGUE_LABELS.get(league_key, league_key),
            "competition_ch": "",
            "home_team_name": match["home_team"],
            "away_team_name": match["away_team"],
            "home_team_name_ch": "",
            "away_team_name_ch": "",
            "kickoff_time_utc": match.get("commence_time", ""),
            "handicap_close_line": handicap_line,
            "odds_home_close": odds_home,
            "odds_away_close": odds_away,
            "implied_prob_home_close": implied_home,
            "implied_prob_away_close": implied_away,
            "source_market": "ODDS_API",
            "handicap_side": "home",
            "target_handicap_side": "home",
            **h2h_implied,
        })
    return pd.DataFrame(rows)


def predict_matches(
    df: pd.DataFrame,
    model_path: Path,
    include_market_features: bool,
) -> pd.DataFrame:
    """Run model predictions on match data."""
    bundle = load_model_bundle(model_path)

    # Build feature frame (missing columns become NaN → imputed by model pipeline)
    feature_columns = get_feature_columns(include_market_features=include_market_features)
    # Only keep columns that exist in df (rest will be NaN)
    existing = [c for c in feature_columns if c in df.columns]
    feature_frame = build_feature_frame(df=df, feature_columns=existing)

    from src.models.baselines import predict_home_cover_probability
    probabilities = predict_home_cover_probability(bundle=bundle, df=df)

    result = df.copy()
    result["home_cover_probability"] = probabilities
    result["away_cover_probability"] = 1.0 - probabilities
    result["predicted_side"] = np.where(probabilities >= 0.5, "home", "away")
    result["model_probability"] = np.where(
        result["predicted_side"] == "home",
        result["home_cover_probability"],
        result["away_cover_probability"],
    )
    result["confidence_score"] = (result["model_probability"] - 0.5).abs() * 2.0
    result["model_name"] = bundle.model_name
    result["model_approach"] = bundle.approach
    result["market_feature_variant"] = "market" if include_market_features else "base"

    # Calculate edge
    result["implied_probability"] = np.where(
        result["predicted_side"] == "home",
        result["implied_prob_home_close"],
        result["implied_prob_away_close"],
    )
    result["edge"] = result["model_probability"] - result["implied_probability"]
    result["expected_roi"] = result["edge"] / result["implied_probability"].replace(0, 0.5)
    return result


def format_alert(match: dict[str, Any]) -> str:
    """Format a single prediction as Telegram message."""
    home = match.get("home_team_name", "?")
    away = match.get("away_team_name", "?")
    league = match.get("competition", "?")
    side = "主" if match.get("predicted_side") == "home" else "客"
    prob = match.get("model_probability", 0)
    edge = match.get("edge", 0)
    conf = match.get("confidence_score", 0)
    odds = match.get("odds_home_close" if match.get("predicted_side") == "home" else "odds_away_close", 0)
    handicap = match.get("handicap_close_line", 0)
    kickoff = str(match.get("kickoff_time_utc", ""))[:16].replace("T", " ")

    edge_pct = f"+{edge*100:.1f}%" if edge >= 0 else f"{edge*100:.1f}%"
    conf_label = "強勢" if conf >= 0.5 else "正向" if conf >= 0.3 else "觀察"

    return (
        f"⚽ {home} vs {away} ({league})\n"
        f"🕐 {kickoff} HKT\n"
        f"📊 讓球: {handicap:+.2f} | 賠率: {odds:.2f}\n"
        f"🎯 推薦: {side} (信心: {conf_label} {conf:.0%})\n"
        f"💹 Edge: {edge_pct}  | 預測: {prob:.1%}"
    )


def main() -> int:
    if not ODDS_API_KEY:
        print("ODDS_API_KEY not set")
        return 1

    # Find latest trained model
    model_path = Path("artifacts/model_bundle_rule_based.pkl")
    if not model_path.exists():
        model_path = Path("artifacts/model_bundle.pkl")
    if not model_path.exists():
        print(f"Model not found at {model_path}")
        return 1

    print(f"[{RUN_ID}] Loading model from {model_path}")
    print(f"[{RUN_ID}] Edge threshold: {EDGE_THRESHOLD}, Conf threshold: {CONFIDENCE_THRESHOLD}")

    all_candidates: list[dict[str, Any]] = []

    for league in SOCCER_LEAGUES:
        print(f"\n--- {league} ---")
        matches = fetch_odds(league)
        if not matches:
            print("  No matches or no spreads available")
            continue
        print(f"  {len(matches)} matches with spreads")

        df = build_match_rows(matches, league)
        if df.empty:
            continue

        predictions = predict_matches(df, model_path, include_market_features=True)

        # Filter by edge and confidence
        candidates = predictions[
            (predictions["edge"] >= EDGE_THRESHOLD)
            & (predictions["confidence_score"] >= CONFIDENCE_THRESHOLD)
        ].copy()
        candidates = candidates.sort_values("edge", ascending=False).head(MAX_ALERTS)
        print(f"  Candidates after filter: {len(candidates)}")

        for _, row in candidates.iterrows():
            alert = format_alert(row.to_dict())
            print(f"\n{alert}\n")
            send_telegram(alert)
            all_candidates.append(row.to_dict())

    # Save results
    output_dir = Path("artifacts/predictions")
    output_dir.mkdir(parents=True, exist_ok=True)

    if all_candidates:
        result_df = pd.DataFrame(all_candidates)
        result_df.to_csv(output_dir / f"today_candidates_{datetime.now().strftime('%Y%m%d')}.csv", index=False)
        print(f"\n✅ Total alerts sent: {len(all_candidates)}")
        send_telegram(f"📊 每日預測完成：{len(all_candidates)} 個推薦")
    else:
        print("\n❌ No candidates found")
        send_telegram("📊 每日預測完成：今日無推薦（邊際不足）")

    # Save all predictions for review
    all_predictions = pd.concat([pd.DataFrame(all_candidates)], ignore_index=True) if all_candidates else pd.DataFrame()
    if not all_predictions.empty:
        all_predictions.to_csv(output_dir / f"predictions_{datetime.now().strftime('%Y%m%d')}.csv", index=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
