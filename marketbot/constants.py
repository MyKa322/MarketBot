"""Static tables: Steam app ids, currencies, fee rates, refresh tiers.

Kept free of imports from the rest of the package so any module may import it.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Final, NamedTuple

# --------------------------------------------------------------------------- apps


class SteamApp(NamedTuple):
    appid: int
    key: str
    title: str
    emoji: str


SUPPORTED_APPS: Final[tuple[SteamApp, ...]] = (
    SteamApp(730, "cs2", "Counter-Strike 2", "\U0001F52B"),
    SteamApp(570, "dota2", "Dota 2", "\U0001F5E1"),
    SteamApp(440, "tf2", "Team Fortress 2", "\U0001F3A9"),
    SteamApp(252490, "rust", "Rust", "\U0001FAA8"),
    SteamApp(753, "steam", "Steam Community", "\U0001F3AE"),
)

APPS_BY_ID: Final[dict[int, SteamApp]] = {a.appid: a for a in SUPPORTED_APPS}
APPS_BY_KEY: Final[dict[str, SteamApp]] = {a.key: a for a in SUPPORTED_APPS}

DEFAULT_APPID: Final[int] = 730


def app_title(appid: int) -> str:
    app = APPS_BY_ID.get(appid)
    return app.title if app else f"App {appid}"


# ---------------------------------------------------------------------- currencies


class Currency(NamedTuple):
    steam_id: int
    code: str
    symbol: str
    minor_units: int = 2
    symbol_first: bool = True


CURRENCIES: Final[tuple[Currency, ...]] = (
    Currency(1, "USD", "$"),
    Currency(2, "GBP", "£"),
    Currency(3, "EUR", "€", symbol_first=False),
    Currency(5, "RUB", "₽", symbol_first=False),
    Currency(6, "PLN", "zł", symbol_first=False),
    Currency(7, "BRL", "R$"),
    Currency(8, "JPY", "¥", minor_units=0),
    Currency(9, "NOK", "kr", symbol_first=False),
    Currency(13, "SGD", "S$"),
    Currency(16, "KRW", "₩", minor_units=0),
    Currency(17, "TRY", "₺"),
    Currency(18, "UAH", "₴", symbol_first=False),
    Currency(19, "MXN", "Mex$"),
    Currency(20, "CAD", "CDN$"),
    Currency(21, "AUD", "A$"),
    Currency(22, "NZD", "NZ$"),
    Currency(23, "CNY", "¥"),
    Currency(24, "INR", "₹"),
    Currency(28, "ZAR", "R"),
    Currency(32, "AED", "AED", symbol_first=False),
    Currency(37, "KZT", "₸", symbol_first=False),
)

CURRENCY_BY_CODE: Final[dict[str, Currency]] = {c.code: c for c in CURRENCIES}
CURRENCY_BY_STEAM_ID: Final[dict[int, Currency]] = {c.steam_id: c for c in CURRENCIES}

DEFAULT_CURRENCY: Final[str] = "USD"


def currency(code: str) -> Currency:
    return CURRENCY_BY_CODE.get(code.upper(), CURRENCY_BY_CODE[DEFAULT_CURRENCY])


# ----------------------------------------------------------------------- fee rates

# Steam takes a 5% transaction fee; the game publisher takes 10%. Each has a
# one-minor-unit floor. See steam/fees.py for the exact integer algorithm.
STEAM_FEE_PERCENT: Final[float] = 0.05
GAME_FEE_PERCENT_DEFAULT: Final[float] = 0.10
FEE_MINIMUM_MINOR: Final[int] = 1

# A handful of apps use non-standard publisher fees.
GAME_FEE_PERCENT_BY_APP: Final[dict[int, float]] = {
    730: 0.10,
    570: 0.10,
    440: 0.10,
    252490: 0.10,
    753: 0.0,  # Steam-published community items: no publisher cut.
}


# -------------------------------------------------------------------- refresh tiers


class RefreshTier(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


TIER_INTERVAL_SECONDS: Final[dict[RefreshTier, int]] = {
    RefreshTier.HOT: 300,
    RefreshTier.WARM: 1800,
    RefreshTier.COLD: 21600,
}


# ------------------------------------------------------------------------- domains

STEAM_COMMUNITY_BASE: Final[str] = "https://steamcommunity.com"
MARKET_LISTING_URL: Final[str] = STEAM_COMMUNITY_BASE + "/market/listings/{appid}/{name}"
ICON_BASE: Final[str] = "https://community.cloudflare.steamstatic.com/economy/image/"
