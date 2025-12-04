"""
Hyperliquid fee calculation utilities.

Implements the official fee formula from:
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FeeRates:
    """Maker/taker rates expressed as decimals (e.g. 0.00045 = 0.045%)."""

    maker_rate: float
    taker_rate: float


# Base tier fees from the Hyperliquid docs (Tier 0)
PERP_BASE_FEES = FeeRates(maker_rate=0.00015, taker_rate=0.00045)
SPOT_BASE_FEES = FeeRates(maker_rate=0.0004, taker_rate=0.0007)


def _clamp_discount(value: float) -> float:
    """Ensure discount percentages stay within [0, 1]."""
    return max(0.0, min(1.0, value))


def calculate_fee_rates(
    base_fees: FeeRates,
    *,
    active_referral_discount: float = 0.0,
    is_aligned_quote_token: bool = False,
    market_type: Literal["perp", "spot"] = "perp",
    is_stable_pair: bool = False,
    deployer_fee_scale: float = 0.0,
    growth_mode: bool = False,
) -> FeeRates:
    """
    Calculate maker/taker fee rates using the Hyperliquid fee formula.

    Args:
        base_fees: Raw maker/taker rates from the user's fee tier.
        active_referral_discount: Referral discount (0-1).
        is_aligned_quote_token: Whether the quote token is aligned (see docs).
        market_type: "perp" or "spot".
        is_stable_pair: True for stable spot pairs.
        deployer_fee_scale: HIP-3 deployer scale (0-3, 0 for non-HIP-3).
        growth_mode: Whether growth mode is enabled (HIP-3 only).

    Returns:
        FeeRates with maker/taker rates as decimals (per side).
    """
    active_referral_discount = _clamp_discount(active_referral_discount)

    scale_if_stable_pair = 0.2 if market_type == "spot" and is_stable_pair else 1
    scale_if_hip3 = 1.0
    growth_mode_scale = 1.0
    deployer_share = 0.0

    if market_type == "perp":
        if deployer_fee_scale < 1:
            scale_if_hip3 = deployer_fee_scale + 1
            deployer_share = deployer_fee_scale / (1 + deployer_fee_scale)
        else:
            scale_if_hip3 = deployer_fee_scale * 2
            deployer_share = 0.5
        growth_mode_scale = 0.1 if growth_mode else 1.0

    maker_percentage = (
        base_fees.maker_rate * 100 * scale_if_stable_pair * growth_mode_scale
    )
    if maker_percentage > 0:
        maker_percentage *= scale_if_hip3 * (1 - active_referral_discount)
    else:
        maker_rebate_scale_if_aligned_quote_token = (
            (1 - deployer_share) * 1.5 + deployer_share if is_aligned_quote_token else 1
        )
        maker_percentage *= maker_rebate_scale_if_aligned_quote_token

    taker_percentage = (
        base_fees.taker_rate
        * 100
        * scale_if_stable_pair
        * scale_if_hip3
        * growth_mode_scale
        * (1 - active_referral_discount)
    )
    if is_aligned_quote_token:
        taker_scale_if_aligned_quote_token = (1 - deployer_share) * 0.8 + deployer_share
        taker_percentage *= taker_scale_if_aligned_quote_token

    return FeeRates(
        maker_rate=maker_percentage / 100,
        taker_rate=taker_percentage / 100,
    )


def default_perp_fee_rates(
    *,
    active_referral_discount: float = 0.0,
    is_aligned_quote_token: bool = False,
    deployer_fee_scale: float = 0.0,
    growth_mode: bool = False,
) -> FeeRates:
    """
    Convenience wrapper for standard perp fee calculations.

    Uses Tier-0 base fees (taker 0.045%, maker 0.015%) as defaults.
    """
    return calculate_fee_rates(
        PERP_BASE_FEES,
        active_referral_discount=active_referral_discount,
        is_aligned_quote_token=is_aligned_quote_token,
        market_type="perp",
        deployer_fee_scale=deployer_fee_scale,
        growth_mode=growth_mode,
    )
