import asyncio
import logging
import config
from rest_conn import RestError

logger = logging.getLogger(__name__)


class Watchlist:
    def __init__(self, rest, registry, storage=None):
        self.rest = rest
        self.registry = registry
        self.storage = storage
        self._ticker_entries = set(config.WATCHLIST_TICKERS)
        self._series_entries = set(config.WATCHLIST_SERIES)

    def _collect_markets_sync(self, series_ticker):
        markets = []
        for page in self.rest.paginated_get("/markets", {"series_ticker": series_ticker, "status": "open"}):
            markets.extend(page.get("markets") or [])
        return markets

    async def _fetch_markets(self, series_ticker):
        return await asyncio.to_thread(self._collect_markets_sync, series_ticker)

    async def register_series(self, series_ticker):
        source = f"watchlist:series:{series_ticker}"
        found = set()
        for m in await self._fetch_markets(series_ticker):
            if m.get("status") != "active":
                continue
            ticker = m["ticker"]
            await self.registry.record_series_market(series_ticker, ticker)
            if self.storage is not None:
                self.storage.upsert_market(m, series_ticker=series_ticker)
            found.add(ticker)

        await self.registry.add_tickers(found, source) 
        logger.info("watchlist series %s: registered %d active markets", series_ticker, len(found))
        return found


    async def load(self):
        for series_ticker in self._series_entries:
            try:
                await self.register_series(series_ticker)
            except RestError as e:
                logger.warning("watchlist series %s: load failed (HTTP %s) — skipping, "
                               "will retry on next poll", series_ticker, e.status_code)
        for ticker in self._ticker_entries:
            await self.register_ticker(ticker)
        logger.info("watchlist loaded: %d series, %d tickers",
                    len(self._series_entries), len(self._ticker_entries))

    def _fetch_market_sync(self, ticker):
        return self.rest.get_with_auth(f"/markets/{ticker}")

    async def register_ticker(self, ticker):
        try:
            resp = await asyncio.to_thread(self._fetch_market_sync, ticker)
        except RestError as e:
            logger.warning("watchlist ticker %s: lookup failed (HTTP %s) — skipping",
                           ticker, e.status_code)
            return False
        market = resp.get("market", {})
        if market.get("status") != "active":
            logger.warning("watchlist ticker %s: not active (status=%s) — skipping",
                           ticker, market.get("status"))
            return False
        await self.registry.add_ticker(ticker, "watchlist:ticker")
        if self.storage is not None:
            self.storage.upsert_market(market)
        logger.info("watchlist ticker %s: registered", ticker)
        return True
    

    async def poll_series(self, series_ticker):
        source = f"watchlist:series:{series_ticker}"
        previous = self.registry.tickers_by_source(source)
        found = await self.register_series(series_ticker)
        if found:                                   
            for ticker in previous - found:
                await self.registry.remove_ticker(ticker, source)
        return found

 
    async def poll_all_series(self):
        for series_ticker in list(self._series_entries):
            try:
                await self.poll_series(series_ticker)
            except RestError as e:
                logger.warning("watchlist series %s: poll failed (HTTP %s) — skipping this cycle",
                               series_ticker, e.status_code)


    async def run(self):
        while True:
            await asyncio.sleep(config.WATCHLIST_POLL_INTERVAL)
            await self.poll_all_series()

    async def add_ticker_entry(self, ticker):
        self._ticker_entries.add(ticker)
        await self.register_ticker(ticker)

    async def add_series_entry(self, series_ticker):
        self._series_entries.add(series_ticker)
        await self.register_series(series_ticker)

    async def remove_ticker_entry(self, ticker):
        self._ticker_entries.discard(ticker)
        await self.registry.remove_ticker(ticker, "watchlist:ticker")
        logger.info("watchlist: removed ticker entry %s", ticker)
        
    async def remove_series_entry(self, series_ticker):
        self._series_entries.discard(series_ticker)
        source = f"watchlist:series:{series_ticker}"
        for ticker in self.registry.tickers_by_source(source):
            await self.registry.remove_ticker(ticker, source)
        logger.info("watchlist: removed series entry %s", series_ticker)