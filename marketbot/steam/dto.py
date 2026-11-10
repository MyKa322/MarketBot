"""Plain data objects returned by the Steam client, free of ORM and network types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ItemRef:
    appid: int
    market_hash_name: str

    @property
    def key(self) -> str:
        return f"{self.appid}:{self.market_hash_name}"


@dataclass(frozen=True, slots=True)
class PriceOverview:
    """Response of /market/priceoverview -- the cheap, primary polling endpoint."""

    item: ItemRef
    currency: str
    lowest_price: int | None = None
    median_price: int | None = None
    volume: int | None = None
    fetched_at: datetime | None = None

    @property
    def best_price(self) -> int | None:
        return self.lowest_price if self.lowest_price is not None else self.median_price


@dataclass(frozen=True, slots=True)
class OrderBook:
    """Response of /market/itemordershistogram -- real supply/demand, needs item_nameid."""

    item: ItemRef
    currency: str
    highest_buy_order: int | None = None
    lowest_sell_order: int | None = None
    buy_order_count: int = 0
    sell_order_count: int = 0
    buy_graph: list[tuple[int, int]] = field(default_factory=list)
    sell_graph: list[tuple[int, int]] = field(default_factory=list)

    @property
    def spread(self) -> int | None:
        if self.highest_buy_order is None or self.lowest_sell_order is None:
            return None
        return self.lowest_sell_order - self.highest_buy_order

    @property
    def spread_pct(self) -> float | None:
        if self.spread is None or not self.lowest_sell_order:
            return None
        return self.spread / self.lowest_sell_order * 100.0


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One row of /market/search render output."""

    item: ItemRef
    name: str
    icon_url: str | None = None
    sell_price: int | None = None
    sell_listings: int = 0
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    ts: datetime
    price: int
    volume: int


@dataclass(frozen=True, slots=True)
class ItemMeta:
    """Details scraped once from the listing page and cached forever."""

    item: ItemRef
    item_nameid: str | None = None
    icon_url: str | None = None
    name_color: str | None = None
