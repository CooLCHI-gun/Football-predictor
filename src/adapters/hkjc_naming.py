from __future__ import annotations

from dataclasses import dataclass


# Layer C fallback dictionary: easy to extend over time.
_COMPETITION_ZH_MAP: dict[str, str] = {
    "English Premier League": "英超",
    "English Championship": "英冠",
    "Spanish Primera Division": "西甲",
    "Italian Serie A": "意甲",
    "German Bundesliga": "德甲",
    "French Ligue 1": "法甲",
    "Portuguese Premier": "葡超",
    "Portuguese Primeira Liga": "葡超",
    "Dutch Division 2": "荷乙",
    "Women Italian Division 1": "意女甲",
    "J1 100Y Vision League": "日職",
    "Japan J1 League": "日職",
}

_TEAM_ZH_MAP: dict[str, str] = {
    "Manchester City": "曼城",
    "Manchester United": "曼聯",
    "Liverpool": "利物浦",
    "Arsenal": "阿仙奴",
    "Chelsea": "車路士",
    "Tottenham": "熱刺",
    "Barcelona": "巴塞隆拿",
    "Real Madrid": "皇家馬德里",
    "Atletico Madrid": "馬德里體育會",
    "Juventus": "祖雲達斯",
    "Inter": "國際米蘭",
    "AC Milan": "AC米蘭",
    "Bayern Munich": "拜仁慕尼黑",
    "PSG": "巴黎聖日耳門",
    "Paris Saint-Germain": "巴黎聖日耳門",
    "Benfica": "賓菲加",
    "Porto": "波圖",
    "Sporting CP": "士砵亭",
    "Fiorentina Women": "費倫天拿女足",
    "Juventus Women": "祖雲達斯女足",
    "Mito Hollyhock": "水戶蜀葵",
    "Kashima Antlers": "鹿島鹿角",
    "Venlo": "芬洛",
    "Cambuur": "甘堡爾",
    "Gil Vicente": "基維辛迪",
    "AVS": "AVS",
}

_MARKET_LABEL_ZH_MAP: dict[str, str] = {
    "ah_ft": "讓球",
    "hdc": "讓球",
    "edc": "讓球",
}

_SIDE_LABEL_ZH_MAP: dict[str, str] = {
    "home": "主",
    "away": "客",
    "h": "主",
    "a": "客",
    "主": "主",
    "客": "客",
}


@dataclass(frozen=True)
class MatchDisplay:
    competition: str
    home_team: str
    away_team: str


def resolve_competition_name(raw_name: str, lang: str = "zh-HK", payload_name_zh: str | None = None) -> str:
    """Resolve competition to HKJC-style Chinese display with safe fallback."""
    if lang.lower() != "zh-hk":
        return _safe_text(payload_name_zh) or _safe_text(raw_name)

    zh_payload = _safe_text(payload_name_zh)
    if zh_payload:
        return zh_payload

    key = _safe_text(raw_name)
    if not key:
        return ""
    return _COMPETITION_ZH_MAP.get(key, key)


def resolve_team_name(raw_name: str, lang: str = "zh-HK", payload_name_zh: str | None = None) -> str:
    """Resolve team name to HKJC-style Chinese display with safe fallback."""
    if lang.lower() != "zh-hk":
        return _safe_text(payload_name_zh) or _safe_text(raw_name)

    zh_payload = _safe_text(payload_name_zh)
    if zh_payload:
        return zh_payload

    key = _safe_text(raw_name)
    if not key:
        return ""
    return _TEAM_ZH_MAP.get(key, key)


def resolve_market_label(
    *,
    market_id: str | None = None,
    market_type: str | None = None,
    predicted_side: str | None = None,
    lang: str = "zh-HK",
) -> str:
    """Resolve market + side wording in HKJC-style Chinese."""
    side_label = _SIDE_LABEL_ZH_MAP.get(_safe_text(predicted_side).lower(), _safe_text(predicted_side) or "主")
    if lang.lower() != "zh-hk":
        return f"AH {side_label}".strip()

    market_key = _safe_text(market_id).lower() or _safe_text(market_type).lower()
    market_label = _MARKET_LABEL_ZH_MAP.get(market_key, "讓球")
    return f"{market_label}{side_label}"


def resolve_match_display(
    home_team: str,
    away_team: str,
    competition: str,
    *,
    lang: str = "zh-HK",
    home_team_zh: str | None = None,
    away_team_zh: str | None = None,
    competition_zh: str | None = None,
) -> MatchDisplay:
    """Resolve competition/home/away display names with layered fallback."""
    resolved_competition = resolve_competition_name(competition, lang=lang, payload_name_zh=competition_zh)
    resolved_home = resolve_team_name(home_team, lang=lang, payload_name_zh=home_team_zh)
    resolved_away = resolve_team_name(away_team, lang=lang, payload_name_zh=away_team_zh)

    return MatchDisplay(
        competition=resolved_competition or _safe_text(competition),
        home_team=resolved_home or _safe_text(home_team),
        away_team=resolved_away or _safe_text(away_team),
    )


def _safe_text(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()
