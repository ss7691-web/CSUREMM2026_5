import asyncio
import logging
import config

logger = logging.getLogger(__name__)

class SectorScanner:
    def __init__(self, rest, registry, storage=None):
        self.rest = rest
        self.registry = registry
        self.storage = storage
        self.sectors = list(config.SECTORS)
        self._empty_counts = {}
        

    def _collect_series_sync(self, sector):
        tickers = []
        for page in self.rest.paginated_get("/series", {"category": sector}):
            for record in page.get("series") or []:
                tickers.append(record["ticker"])
        return tickers

    async def fetch_series_for_sector(self, sector):
        return await asyncio.to_thread(self._collect_series_sync, sector)

    def _collect_markets_sync(self, series_ticker):
        markets = []
        for page in self.rest.paginated_get("/markets", {"series_ticker": series_ticker, "status": "open"}):
            markets.extend(page.get("markets") or [])
        return markets
    
    async def scan_sector(self, sector):
        source = f"sector:{sector}"
        previous = self.registry.tickers_by_source(source)
        found = set()
        for series in await self.fetch_series_for_sector(sector):
                await self.registry.record_sector_series(sector, series)
                for m in await self.fetch_markets_for_series(series):
                    if m.get("status") != "active":
                        continue
                    ticker = m["ticker"]
                    await self.registry.record_series_market(series, ticker)
                    if self.storage is not None:
                        self.storage.upsert_market(m, series_ticker=series, category=sector)
                    found.add(ticker)
        await self.registry.add_tickers(found, source)                  

        new = found - previous                       
        if found:
            self._empty_counts[sector] = 0           
            closed = previous - found                
            for ticker in closed:
                await self.registry.remove_ticker(ticker, source)
        else:
            self._empty_counts[sector] = self._empty_counts.get(sector, 0) + 1
            count = self._empty_counts[sector]
            if count >= config.EMPTY_REMOVAL_THRESHOLD:
                logger.warning("sector %s empty %dx; removing all %d tracked",
                               sector, count, len(previous))
                for ticker in previous:
                    await self.registry.remove_ticker(ticker, source)
                closed = previous
            else:
                logger.warning("sector %s empty (%d/%d); skipping removals (likely transient)",
                               sector, count, config.EMPTY_REMOVAL_THRESHOLD)
                closed = set()
        logger.info("sector %s: %d active (%d new, %d closed)",
                    sector, len(found), len(new), len(closed))
        return found

    async def fetch_markets_for_series(self, series_ticker):
        return await asyncio.to_thread(self._collect_markets_sync, series_ticker)

    async def scan_all_sectors(self):
        for i, sector in enumerate(self.sectors):
            await self.scan_sector(sector)
            if i < len(self.sectors) - 1:
                await asyncio.sleep(config.SECTOR_STAGGER)

    async def run(self):
        while True:
            try:
                await self.scan_all_sectors()
            except Exception as e:
                logger.warning("scan cycle failed; will retry next interval: %s", e)
            await asyncio.sleep(config.SCAN_INTERVAL)