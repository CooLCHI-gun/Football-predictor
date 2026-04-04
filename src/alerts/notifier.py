from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone

import pandas as pd

from src.adapters.hkjc_naming import resolve_market_label, resolve_match_display
from src.alerts.telegram_client import TelegramClient


_POLICY_LABEL_MAP: dict[str, str] = {
    "flat": "固定注額",
    "fixed_fraction": "固定比例",
    "fractional_kelly": "分數凱利",
    "vol_target": "波動目標",
}

_SOURCE_LABEL_MAP: dict[str, str] = {
    "HKJC": "香港賽馬會",
    "HKJC_LIKE": "香港賽馬會（模擬）",
    "MOCK": "模擬資料",
    "CSV": "CSV 匯入",
}


@dataclass(frozen=True)
class BetRecord:
    provider_match_id: str
    kickoff_time_utc: str
    home_team_name: str
    away_team_name: str
    handicap_line: float
    model_name: str
    model_approach: str
    predicted_side: str
    predicted_win_probability: float
    implied_probability: float
    edge: float
    stake_size: float
    original_predicted_side: str | None = None
    flip_hkjc_side_enabled: bool = False
    confidence_score: float = 0.0
    odds: float = 0.0
    source_label: str = "HKJC"
    policy: str = "fractional_kelly"
    mode_label: str = "DRY-RUN"
    competition: str = "HKJC"
    competition_zh: str = ""
    home_team_name_zh: str = ""
    away_team_name_zh: str = ""
    market_id: str = "ah_ft"
    match_number: str = ""
    expected_value: float = 0.0


def send_bet_alert(bet: BetRecord, client: TelegramClient) -> str:
    text = build_bet_alert_message(bet)
    return client.send_message(text=text, parse_mode="Markdown")


def build_bet_alert_message(bet: BetRecord) -> str:
    kickoff_hkt = _to_hkt_text(bet.kickoff_time_utc)
    effective_odds = (
        bet.odds
        if bet.odds > 1.0
        else 1.0 / bet.implied_probability if bet.implied_probability > 0 else 0.0
    )
    recommended_side = _format_recommended_side(bet.predicted_side)
    handicap_text = _format_handicap_line(bet.handicap_line)
    match_display = resolve_match_display(
        bet.home_team_name,
        bet.away_team_name,
        bet.competition,
        lang="zh-HK",
        home_team_zh=bet.home_team_name_zh,
        away_team_zh=bet.away_team_name_zh,
        competition_zh=bet.competition_zh,
    )
    market_side_label = resolve_market_label(
        market_id=bet.market_id,
        predicted_side=bet.predicted_side,
        lang="zh-HK",
    )
    policy_label = _format_policy_label(bet.policy)
    source_label = _format_source_label(bet.source_label)
    side_debug_lines = ""
    if bet.flip_hkjc_side_enabled and bet.original_predicted_side:
        original_side_label = _format_recommended_side(bet.original_predicted_side)
        effective_side_label = _format_recommended_side(bet.predicted_side)
        side_debug_lines = (
            f"\n🧠 model side: {original_side_label}"
            f"\n🔁 effective side: {effective_side_label}"
        )
    return (
        f"⚽ 第{bet.match_number or '?'}場 - {match_display.competition} {kickoff_hkt}\n"
        f"📍 {match_display.home_team} 對 {match_display.away_team}\n"
        f"🧾 盤口: {market_side_label} {handicap_text} | 賠率: {effective_odds:.2f}\n"
        f"📊 模型勝率: {bet.predicted_win_probability:.2%} | 隱含機率: {bet.implied_probability:.2%}\n"
        f"🎯 Edge: {bet.edge:.2%} | 信心: {bet.confidence_score:.2%}\n"
        "⭐ 讓球推薦\n"
        f"1️⃣ {recommended_side} {handicap_text}\n"
        f"💰 注碼政策: {policy_label}\n"
        f"📈 EV: {bet.expected_value:.4f}\n"
        f"🧪 來源: {source_label}\n"
        f"{side_debug_lines}\n"
        "⚠️ 僅供研究參考，不構成投注建議"
    )


def _to_hkt_text(kickoff_time_utc: str) -> str:
    parsed = pd.to_datetime(kickoff_time_utc, utc=True, errors="coerce")
    if pd.isna(parsed):
        return kickoff_time_utc
    return parsed.tz_convert(timezone.utc).tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d %H:%M HKT")


def _format_recommended_side(predicted_side: str) -> str:
    normalized = predicted_side.strip().lower()
    if normalized == "away":
        return "客"
    return "主"


def _format_handicap_line(handicap_line: float) -> str:
    if handicap_line > 0:
        return f"+{handicap_line:.2f}"
    if handicap_line < 0:
        return f"{handicap_line:.2f}"
    return "0.00"


def _format_policy_label(policy: str) -> str:
    key = policy.strip().lower()
    return _POLICY_LABEL_MAP.get(key, policy)


def _format_source_label(source_label: str) -> str:
    key = source_label.strip().upper()
    return _SOURCE_LABEL_MAP.get(key, source_label)
