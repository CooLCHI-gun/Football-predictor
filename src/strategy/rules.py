def describe_strategy_modes() -> list[str]:
    """Return planned strategy modes for later phases."""
    return [
        "direct_cover_classification",
        "expected_goal_diff_mapping",
        "market_prior_hybrid",
    ]


def maybe_flip_hkjc_side(
    predicted_side: str | None,
    source_market: str | None,
    flip_hkjc_side: bool,
) -> str | None:
    """Return effective side after optional HKJC-only side flip."""
    if predicted_side is None:
        return None

    normalized_side = predicted_side.strip().lower()
    if normalized_side not in {"home", "away"}:
        return predicted_side

    if not flip_hkjc_side:
        return normalized_side

    normalized_market = (source_market or "").strip().upper()
    is_hkjc_market = normalized_market == "HKJC" or normalized_market.startswith("HKJC_")
    if not is_hkjc_market:
        return normalized_side

    return "away" if normalized_side == "home" else "home"
