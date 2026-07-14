import asyncio
import logging
import config
from log_setup import setup_logging
from storage import Storage
from auth import Auth
from rest_conn import RestConn
from webs_conn import KalshiWebSocketClient
from market_registry import MarketRegistry
from sector_scanner import SectorScanner
from watchlist import Watchlist
from orderbook import OrderBook
from trade_collector import TradeCollector

logger = logging.getLogger(__name__)


async def main():
    config.validate()
    listener = setup_logging(config.LOG_PATH, getattr(logging, config.LOG_LEVEL),
                             config.LOG_MAX_BYTES, config.LOG_BACKUPS)
    logger.info("starting kalshi collector")


    storage  = Storage()  
    from data_browser import start_browser
    start_browser(storage.con)
    logger.info("data browser at http://localhost:5000")                                 # 
    auth     = Auth(config)
    rest     = RestConn(min_interval=config.REST_MIN_INTERVAL,
                        max_retries=config.REST_MAX_RETRIES,
                        base_backoff=config.REST_BASE_BACKOFF)
    ws       = KalshiWebSocketClient(auth)
    registry = MarketRegistry(ws)


    def request_snapshot(market):
        sid = ws._sid_for_channel("orderbook_delta")
        if sid is not None:
            asyncio.create_task(ws.update_subscription(sid, "get_snapshot", [market]))

    orderbook = OrderBook(storage, request_snapshot=request_snapshot)
    trades    = TradeCollector(storage, registry)
    scanner   = SectorScanner(rest, registry, storage=storage)
    watchlist = Watchlist(rest, registry, storage=storage)

    # 3. route WS messages to the consumers
    ws.register_callback("orderbook_snapshot", orderbook.on_snapshot)
    ws.register_callback("orderbook_delta",    orderbook.on_delta)
    trades.register(ws)                                    # "trade" -> trades.on_trade

    # 4. start the WS supervisor (connects + reconnects forever) and wait for the socket
    ws_task = asyncio.create_task(ws.run())
    while ws.connection is None:
        await asyncio.sleep(0.5)
    logger.info("websocket up; seeding from REST")

    # 5. cold start: pull watchlist + sectors once (this subscribes markets via the registry)
    await watchlist.load()
    await scanner.scan_all_sectors()

    # 6. start the ongoing poll loops; run everything forever
    scan_task  = asyncio.create_task(scanner.run())
    watch_task = asyncio.create_task(watchlist.run())
    logger.info("collector running")

    try:
        await asyncio.gather(ws_task, scan_task, watch_task)
    finally:
        await ws.close()
        storage.close()
        listener.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("shutting down")