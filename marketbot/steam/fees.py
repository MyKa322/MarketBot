"""Exact Steam Community Market fee arithmetic, in integer minor units.

Steam charges the *seller* two fees on top of the amount they receive: a 5% Steam
transaction fee and a publisher fee (10% for CS2/Dota/TF2/Rust). Both are floored at
one minor unit. Critically, Steam does not compute `buyer_price * 0.15`; it solves the
inverse problem -- given the price the buyer pays, find the largest seller amount whose
fees fit inside it. The two differ by a cent or two per trade because of flooring, and
on a portfolio of hundreds of trades that drift is exactly the size of the profit
people think they made.

Ported from Steam's own `CalculateAmountToSendForDesiredReceivedAmount` /
`CalculateFeeAmount` wallet helpers.
"""
from __future__ import annotations

from dataclasses import dataclass

from marketbot.constants import (
    FEE_MINIMUM_MINOR,
    GAME_FEE_PERCENT_BY_APP,
    GAME_FEE_PERCENT_DEFAULT,
    STEAM_FEE_PERCENT,
)


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """All figures are integer minor units of a single currency."""

    buyer_pays: int
    seller_receives: int
    steam_fee: int
    game_fee: int

    @property
    def total_fee(self) -> int:
        return self.steam_fee + self.game_fee

    @property
    def effective_rate(self) -> float:
        """Fees as a fraction of what the buyer paid."""
        return self.total_fee / self.buyer_pays if self.buyer_pays else 0.0


def game_fee_percent(appid: int) -> float:
    return GAME_FEE_PERCENT_BY_APP.get(appid, GAME_FEE_PERCENT_DEFAULT)


def gross_for_net(net_minor: int, *, game_pct: float = GAME_FEE_PERCENT_DEFAULT) -> FeeBreakdown:
    """What a buyer must pay for the seller to receive exactly `net_minor`."""
    if net_minor <= 0:
        return FeeBreakdown(0, 0, 0, 0)
    steam_fee = int(max(net_minor * STEAM_FEE_PERCENT, FEE_MINIMUM_MINOR))
    game_fee = int(max(net_minor * game_pct, FEE_MINIMUM_MINOR)) if game_pct > 0 else 0
    return FeeBreakdown(
        buyer_pays=net_minor + steam_fee + game_fee,
        seller_receives=net_minor,
        steam_fee=steam_fee,
        game_fee=game_fee,
    )


def net_from_buyer_price(
    buyer_minor: int, *, game_pct: float = GAME_FEE_PERCENT_DEFAULT
) -> FeeBreakdown:
    """Seller proceeds for an item listed at `buyer_minor`.

    Finds the largest `net` with `gross_for_net(net) <= buyer_minor`. `gross_for_net`
    is monotonically non-decreasing in `net`, so binary search is exact; any leftover
    minor units (flooring slack) are absorbed into the Steam fee, which is what Steam
    itself does.
    """
    if buyer_minor <= 0:
        return FeeBreakdown(0, 0, 0, 0)

    lo, hi, best = 0, buyer_minor, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if gross_for_net(mid, game_pct=game_pct).buyer_pays <= buyer_minor:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    exact = gross_for_net(best, game_pct=game_pct)
    slack = buyer_minor - exact.buyer_pays
    return FeeBreakdown(
        buyer_pays=buyer_minor,
        seller_receives=best,
        steam_fee=exact.steam_fee + slack,
        game_fee=exact.game_fee,
    )


def net_for_app(buyer_minor: int, appid: int) -> FeeBreakdown:
    return net_from_buyer_price(buyer_minor, game_pct=game_fee_percent(appid))


def breakeven_sale_price(cost_minor: int, appid: int) -> int:
    """Listing price at which a position bought for `cost_minor` breaks even.

    The number every "is this profitable yet?" question actually reduces to.
    """
    if cost_minor <= 0:
        return 0
    pct = game_fee_percent(appid)
    price = gross_for_net(cost_minor, game_pct=pct).buyer_pays
    # Flooring can leave the result a minor unit short; nudge up until it clears.
    while net_from_buyer_price(price, game_pct=pct).seller_receives < cost_minor:
        price += 1
    return price
