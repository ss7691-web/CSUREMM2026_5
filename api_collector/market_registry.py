import asyncio
import logging

logger = logging.getLogger(__name__)


class MarketRegistry:
    def __init__(self, ws):
        self.ws = ws
        self._sources = {}
        self._series_to_markets = {}
        self._sector_to_series = {}
        self._lock = asyncio.Lock()
        self._empty_counts = {}
        
    #if want to change what subscribing 
    SECTOR_CHANNELS = ("trade")
    WATCHLIST_CHANNELS = ("trade", "orderbook_delta")

    def _channels_for(self, ticker):
        channels = set()
        for src in self._sources.get(ticker, set()):
            channels.update(self.WATCHLIST_CHANNELS if src.startswith("watchlist") else self.SECTOR_CHANNELS)
        return channels

    async def add_ticker(self, ticker, source):
        async with self._lock:
            before = self._channels_for(ticker)
            self._sources.setdefault(ticker, set()).add(source)
            after = self._channels_for(ticker)
            new_channels = after - before
            logger.info("registry add: %s source=%s new_channels=%s",
                        ticker, source, sorted(new_channels))
            if self.ws is not None:
                for channel in new_channels:
                    await self.ws.add_markets(channel, [ticker])
        return new_channels

    async def add_tickers(self, tickers, source):
        tickers = list(tickers)
        async with self._lock:
            batches = {}                      
            for ticker in tickers:
                before = self._channels_for(ticker)
                self._sources.setdefault(ticker, set()).add(source)
                after = self._channels_for(ticker)
                for channel in (after - before):
                    batches.setdefault(channel, []).append(ticker)
            logger.info("registry add batch: source=%s tickers=%d new=%s",
                        source, len(tickers), {c: len(v) for c, v in batches.items()})
            if self.ws is not None:
                for channel, batch in batches.items():
                    await self.ws.add_markets(channel, batch)
        return batches

    async def remove_ticker(self, ticker, source):
        async with self._lock:
            if ticker not in self._sources:
                return set()
            before = self._channels_for(ticker)
            self._sources[ticker].discard(source)
            if not self._sources[ticker]:
                del self._sources[ticker]              
            after = self._channels_for(ticker)         
            dropped = before - after
            logger.info("registry remove: %s source=%s dropped_channels=%s",
                        ticker, source, sorted(dropped))
            if self.ws is not None:
                for channel in dropped:
                    await self.ws.remove_markets(channel, [ticker])
        return dropped

    async def record_series_market(self, series_ticker, market_ticker):
        async with self._lock:
            self._series_to_markets.setdefault(series_ticker, set()).add(market_ticker)

    async def record_sector_series(self, sector, series_ticker):
        async with self._lock:
            self._sector_to_series.setdefault(sector, set()).add(series_ticker)
    def tickers_for_series(self, series_ticker):
        return set(self._series_to_markets.get(series_ticker, set()))

    def tickers_for_sector(self, sector):
        tickers = set()
        for series in self._sector_to_series.get(sector, set()):
            tickers.update(self._series_to_markets.get(series, set()))
        return tickers
    
    def tickers_by_source(self, source):
        return {t for t, srcs in self._sources.items() if source in srcs}
    