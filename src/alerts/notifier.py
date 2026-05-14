from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import os

import pandas as pd

from src.adapters.hkjc_naming import resolve_market_label, resolve_match_display
from src.alerts.telegram_client import TelegramClient
from src.config.settings import get_settings


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
    settings = get_settings()
    alert_tone = str(settings.alert_tone).strip().upper()
    kickoff_hkt = _to_hkt_text(bet.kickoff_time_utc)
    home_team_name = _normalize_text_field(bet.home_team_name)
    away_team_name = _normalize_text_field(bet.away_team_name)
    competition_name = _normalize_text_field(bet.competition)
    home_team_name_zh = _normalize_text_field(bet.home_team_name_zh)
    away_team_name_zh = _normalize_text_field(bet.away_team_name_zh)
    competition_name_zh = _normalize_text_field(bet.competition_zh)
    effective_odds = (
        bet.odds
        if bet.odds > 1.0
        else 1.0 / bet.implied_probability if bet.implied_probability > 0 else 0.0
    )
    recommended_side = _format_recommended_side(bet.predicted_side)
    handicap_text = _format_handicap_line(bet.handicap_line)
    match_display = resolve_match_display(
        home_team_name, away_team_name, competition_name,
        lang="zh-HK",
        home_team_zh=home_team_name_zh, away_team_zh=away_team_name_zh,
        competition_zh=competition_name_zh,
    )
    market_side_label = resolve_market_label(
        market_id=bet.market_id, predicted_side=bet.predicted_side, lang="zh-HK",
    )
    policy_label = _format_policy_label(bet.policy)
    source_label = _format_source_label(bet.source_label)
    signal_tone = _format_signal_tone(edge=bet.edge, confidence_score=bet.confidence_score)
    confidence_label = _format_confidence_label(bet.confidence_score)
    side_debug_lines = ""
    if bet.flip_hkjc_side_enabled and bet.original_predicted_side:
        original_side_label = _format_recommended_side(bet.original_predicted_side)
        effective_side_label = _format_recommended_side(bet.predicted_side)
        side_debug_lines = (
            f"\n🧠 模型方向: {original_side_label}\n"
            f"🔁 生效方向: {effective_side_label}"
        )
    edge_sign = f"+{bet.edge:.2%}" if bet.edge >= 0 else f"{bet.edge:.2%}"

    tone_key = "neutral" if alert_tone == "NEUTRAL" else "expressive"
    tpl_name = f"bet_alert_{tone_key}.txt"
    tpl_path = os.path.join(os.path.dirname(__file__), "templates", tpl_name)

    try:
        with open(tpl_path, encoding="utf-8") as f:
            tpl = f.read()
    except FileNotFoundError:
        # Fallback to inline message
        return f"[{bet.match_number}] {match_display.home_team} vs {match_display.away_team} — edge={bet.edge:.2%}"

    return tpl.format(
        match_number=bet.match_number or "?",
        competition_zh=competition_name_zh or competition_name,
        kickoff_hkt=kickoff_hkt,
        home_display=match_display.home_team,
        away_display=match_display.away_team,
        market_side_label=market_side_label,
        handicap_text=handicap_text,
        odds=f"{effective_odds:.2f}",
        recommended_side=recommended_side,
        policy_label=policy_label,
        model_prob=bet.predicted_win_probability,
        implied_prob=bet.implied_probability,
        edge_sign=edge_sign,
        confidence_score=bet.confidence_score,
        confidence_label=confidence_label,
        expected_value=f"{bet.expected_value:.4f}",
        source_label=source_label,
        side_debug_lines=side_debug_lines,
        signal_tone=signal_tone,
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


def _normalize_text_field(value: str) -> str:
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "na", "n/a"}:
        return ""
    return text


def _format_signal_tone(edge: float, confidence_score: float) -> str:
    if edge >= 0.20 and confidence_score >= 0.50:
        return "🔥 強勢訊號"
    if edge >= 0.12 and confidence_score >= 0.35:
        return "✅ 正向訊號"
    return "🟡 觀察訊號"


def _format_confidence_label(confidence_score: float) -> str:
    if confidence_score >= 0.65:
        return "高"
    if confidence_score >= 0.45:
        return "中"
    return "保守"
