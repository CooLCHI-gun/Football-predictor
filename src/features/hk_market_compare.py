from __future__ import annotations

import numpy as np
import pandas as pd


def implied_prob_from_decimal(odds: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(odds, errors="coerce")
    return np.where(numeric > 1.0, 1.0 / numeric, np.nan)


def add_hk_vs_consensus_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add HKJC-vs-consensus comparison features with safe fallbacks.

    If consensus columns are absent, the function still returns a stable schema.
    """
    enriched = df.copy()

    if "handicap_close_line" in enriched.columns:
        enriched["hk_line"] = pd.to_numeric(enriched["handicap_close_line"], errors="coerce")
    else:
        enriched["hk_line"] = np.nan

    if "consensus_line" not in enriched.columns:
        if "foreign_handicap_line" in enriched.columns:
            enriched["consensus_line"] = enriched["foreign_handicap_line"]
        elif "market_consensus_line" in enriched.columns:
            enriched["consensus_line"] = enriched["market_consensus_line"]
        else:
            enriched["consensus_line"] = np.nan
    enriched["consensus_line"] = pd.to_numeric(enriched["consensus_line"], errors="coerce")
    enriched["hk_line_minus_consensus_line"] = enriched["hk_line"] - enriched["consensus_line"]

    side_series = enriched.get("target_handicap_side", pd.Series("home", index=enriched.index)).astype(str).str.lower()
    home_implied = pd.to_numeric(enriched.get("implied_prob_home_close"), errors="coerce")
    away_implied = pd.to_numeric(enriched.get("implied_prob_away_close"), errors="coerce")

    enriched["hk_implied_prob_side"] = np.where(side_series == "away", away_implied, home_implied)
    enriched["hk_implied_prob"] = enriched["hk_implied_prob_side"]

    def _all_na(value: object) -> bool:
        if isinstance(value, (pd.Series, pd.Index)):
            return bool(value.isna().all())
        return bool(pd.isna(value))

    def _to_numeric_any(value: object) -> object:
        if isinstance(value, (pd.Series, pd.Index)):
            return pd.to_numeric(value, errors="coerce")
        return pd.to_numeric(value, errors="coerce")

    def _to_series(value: object) -> pd.Series:
        if isinstance(value, pd.Series):
            return pd.to_numeric(value, errors="coerce").reindex(enriched.index)
        if isinstance(value, pd.Index):
            return pd.Series(pd.to_numeric(value, errors="coerce"), index=enriched.index)
        numeric = _to_numeric_any(value)
        return pd.Series(numeric, index=enriched.index)

    def _implied_prob_from_odds_any(odds: object) -> object:
        numeric = _to_numeric_any(odds)
        if isinstance(numeric, (pd.Series, pd.Index)):
            return pd.Series(np.where(numeric > 1.0, 1.0 / numeric, np.nan), index=numeric.index)
        if pd.isna(numeric) or numeric <= 1.0:
            return np.nan
        return 1.0 / float(numeric)

    if "consensus_implied_prob_home" not in enriched.columns and "consensus_odds_home" not in enriched.columns:
        enriched["consensus_implied_prob_side"] = np.nan
        enriched["consensus_implied_prob"] = np.nan
        enriched["hk_line_minus_consensus_line"] = np.nan
        enriched["hk_minus_consensus_prob"] = np.nan
        enriched["hk_off_market_flag"] = 0
        enriched["hk_off_market_direction"] = 0.0
        enriched["hk_off_market_agree_with_model_flag"] = 0
        return enriched

    consensus_home_prob = _to_numeric_any(enriched.get("consensus_implied_prob_home"))
    consensus_away_prob = _to_numeric_any(enriched.get("consensus_implied_prob_away"))
    if _all_na(consensus_home_prob) or _all_na(consensus_away_prob):
        consensus_home_odds = _to_numeric_any(enriched.get("consensus_odds_home"))
        consensus_away_odds = _to_numeric_any(enriched.get("consensus_odds_away"))
        consensus_home_raw = _implied_prob_from_odds_any(consensus_home_odds)
        consensus_away_raw = _implied_prob_from_odds_any(consensus_away_odds)

        consensus_home_raw_series = _to_series(consensus_home_raw)
        consensus_away_raw_series = _to_series(consensus_away_raw)
        consensus_total = consensus_home_raw_series + consensus_away_raw_series
        consensus_home_prob = pd.Series(
            np.where(consensus_total > 0, consensus_home_raw_series / consensus_total, np.nan),
            index=enriched.index,
        )
        consensus_away_prob = pd.Series(
            np.where(consensus_total > 0, consensus_away_raw_series / consensus_total, np.nan),
            index=enriched.index,
        )

    consensus_home_prob = _to_series(consensus_home_prob)
    consensus_away_prob = _to_series(consensus_away_prob)

    enriched["consensus_implied_prob_side"] = np.where(side_series == "away", consensus_away_prob, consensus_home_prob)

    if "consensus_implied_prob" not in enriched.columns:
        enriched["consensus_implied_prob"] = enriched["consensus_implied_prob_side"]
    enriched["consensus_implied_prob"] = pd.to_numeric(enriched["consensus_implied_prob"], errors="coerce")
    enriched["consensus_implied_prob_side"] = pd.to_numeric(enriched["consensus_implied_prob_side"], errors="coerce")

    fallback_mask = enriched["consensus_implied_prob"].isna() & enriched["consensus_implied_prob_side"].notna()
    enriched.loc[fallback_mask, "consensus_implied_prob"] = enriched.loc[fallback_mask, "consensus_implied_prob_side"]
    enriched["hk_minus_consensus_prob"] = enriched["hk_implied_prob"] - enriched["consensus_implied_prob"]

    enriched["hk_off_market_flag"] = (
        enriched["hk_minus_consensus_prob"].abs() >= 0.02
    ).fillna(False).astype(int)
    enriched["hk_off_market_direction"] = np.sign(
        pd.to_numeric(enriched["hk_minus_consensus_prob"], errors="coerce").fillna(0.0)
    )
    side_direction = np.where(side_series == "away", -1.0, 1.0)
    enriched["hk_off_market_agree_with_model_flag"] = (
        (enriched["hk_off_market_flag"] == 1)
        & (enriched["hk_off_market_direction"] == side_direction)
    ).astype(int)

    return enriched
