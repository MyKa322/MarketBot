"""Steam fee arithmetic."""
from __future__ import annotations

import pytest

from marketbot.steam.fees import (
    breakeven_sale_price,
    game_fee_percent,
    gross_for_net,
    net_from_buyer_price,
)


def test_canonical_115_to_100():
    """Steam's documented example: a buyer paying $1.15 leaves the seller $1.00."""
    result = net_from_buyer_price(115)
    assert result.seller_receives == 100
    assert result.steam_fee == 5
    assert result.game_fee == 10
    assert result.total_fee == 15


def test_gross_for_net_is_inverse():
    assert gross_for_net(100).buyer_pays == 115


@pytest.mark.parametrize("buyer", [3, 4, 10, 23, 57, 115, 999, 1000, 12345, 987654])
def test_never_exceeds_buyer_price(buyer):
    """The seller amount plus fees must never exceed what the buyer paid."""
    b = net_from_buyer_price(buyer)
    assert b.seller_receives + b.total_fee == buyer
    assert b.seller_receives <= buyer


@pytest.mark.parametrize("buyer", [3, 10, 23, 115, 1000, 54321])
def test_maximal_seller_amount(buyer):
    """One more cent to the seller would overflow the buyer's price."""
    b = net_from_buyer_price(buyer)
    assert gross_for_net(b.seller_receives + 1).buyer_pays > buyer


def test_minimum_fee_floor():
    """Both fees floor at one cent, so a 3c sale nets exactly 1c."""
    b = net_from_buyer_price(3)
    assert (b.seller_receives, b.steam_fee, b.game_fee) == (1, 1, 1)


def test_naive_multiplication_is_wrong():
    """Guards the reason this module exists: price*0.85 does not equal Steam's answer."""
    naive = int(115 * 0.85)
    assert naive == 97
    assert net_from_buyer_price(115).seller_receives == 100


def test_zero_and_negative():
    assert net_from_buyer_price(0).seller_receives == 0
    assert net_from_buyer_price(-5).seller_receives == 0
    assert gross_for_net(0).buyer_pays == 0


def test_publisher_free_app_has_no_game_fee():
    """appid 753 (Steam community items) carries no publisher cut."""
    assert game_fee_percent(753) == 0.0
    b = net_from_buyer_price(1000, game_pct=0.0)
    assert b.game_fee == 0
    assert b.seller_receives > net_from_buyer_price(1000).seller_receives


@pytest.mark.parametrize("cost", [1, 50, 100, 1000, 25000])
def test_breakeven_recovers_cost(cost):
    """Listing at the break-even price must return at least the original cost."""
    price = breakeven_sale_price(cost, 730)
    assert net_from_buyer_price(price).seller_receives >= cost
    assert net_from_buyer_price(price - 1).seller_receives < cost
