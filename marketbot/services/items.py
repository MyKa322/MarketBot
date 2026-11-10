"""Resolving user input ("AK-47 Redline", or a market URL) into a tracked Item."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.config import Settings
from marketbot.db.models import Item
from marketbot.db.repo import items as item_repo
from marketbot.steam import parse as P
from marketbot.steam.client import SteamError, SteamMarketClient
from marketbot.steam.dto import ItemRef

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Candidate:
    appid: int
    market_hash_name: str
    display_name: str
    icon_url: str | None = None
    price_minor: int | None = None
    listings: int = 0
    item_id: int | None = None


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """Either an exact item, or a shortlist for the user to disambiguate."""

    item: Item | None = None
    candidates: list[Candidate] | None = None
    created: bool = False
    source: str = "none"
    error: str | None = None

    @property
    def is_exact(self) -> bool:
        return self.item is not None


class ItemService:
    def __init__(self, client: SteamMarketClient, settings: Settings) -> None:
        self._client = client
        self._s = settings

    async def resolve(
        self,
        session: AsyncSession,
        raw: str,
        *,
        appid: int | None = None,
        currency: str | None = None,
    ) -> ResolveResult:
        """Resolve free text or a market URL, cheapest path first.

        Order matters for rate limits: a URL needs no Steam call at all, and a local
        hit needs none either. Only a genuinely unknown name reaches Steam's search.
        """
        text = P.normalize_hash_name(raw)
        if not text:
            return ResolveResult(error="Send an item name or a Steam Market link.")

        # 1. A market URL is unambiguous and free to parse.
        parsed = P.parse_market_url(text)
        if parsed:
            url_appid, name = parsed
            item, created = await item_repo.get_or_create(session, url_appid, name)
            return ResolveResult(item=item, created=created, source="url")
        if text.lower().startswith(("http://", "https://", "steamcommunity.com")):
            return ResolveResult(
                error="That link is not a Steam Market listing URL.\n"
                "Expected: steamcommunity.com/market/listings/&lt;appid&gt;/&lt;item&gt;"
            )

        # 2. Exact local match: already tracked for someone, so no request needed.
        local = await item_repo.search_local(session, text, appid=appid, limit=8)
        for item in local:
            if item.market_hash_name.casefold() == text.casefold():
                return ResolveResult(item=item, source="local")

        # 3. Ask Steam, but keep local hits as fallback if Steam is unavailable.
        code = currency or self._s.steam_currency
        try:
            hits = await self._client.search(
                text, appid=appid or self._s.steam_default_appid, limit=8, code=code
            )
        except SteamError as exc:
            log.info("steam search unavailable: %s", exc)
            if local:
                return ResolveResult(
                    candidates=[self._from_item(i) for i in local], source="local"
                )
            return ResolveResult(
                error="Steam search is unavailable right now. Try again shortly, "
                "or paste the item's Market URL - that works without a search."
            )

        if not hits:
            if local:
                return ResolveResult(
                    candidates=[self._from_item(i) for i in local], source="local"
                )
            return ResolveResult(error=f'No market items found for "{text}".')

        # An exact name match from Steam can be adopted without asking.
        for hit in hits:
            if hit.item.market_hash_name.casefold() == text.casefold():
                item, created = await item_repo.get_or_create(
                    session,
                    hit.item.appid,
                    hit.item.market_hash_name,
                    icon_url=hit.icon_url,
                )
                return ResolveResult(item=item, created=created, source="steam")

        return ResolveResult(
            candidates=[
                Candidate(
                    appid=h.item.appid,
                    market_hash_name=h.item.market_hash_name,
                    display_name=h.name,
                    icon_url=h.icon_url,
                    price_minor=h.sell_price,
                    listings=h.sell_listings,
                )
                for h in hits
            ],
            source="steam",
        )

    @staticmethod
    def _from_item(item: Item) -> Candidate:
        return Candidate(
            appid=item.appid,
            market_hash_name=item.market_hash_name,
            display_name=item.market_hash_name,
            icon_url=item.icon_url,
            item_id=item.id,
        )

    async def adopt(self, session: AsyncSession, candidate: Candidate) -> Item:
        item, _ = await item_repo.get_or_create(
            session,
            candidate.appid,
            candidate.market_hash_name,
            icon_url=candidate.icon_url,
        )
        return item

    async def verify_exists(self, session: AsyncSession, item: Item) -> bool:
        """Confirm an item added by URL actually has market data."""
        try:
            await self._client.price_overview(
                ItemRef(item.appid, item.market_hash_name), self._s.steam_currency
            )
            return True
        except SteamError:
            return False
