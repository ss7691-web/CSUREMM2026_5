import logging
from collections import deque
import asyncio
import time

logger = logging.getLogger(__name__)


class TradeCollector:
    def __init__(self, storage, registry, window_size=100, dedup_size=5000):
        self.storage = storage
        self.registry = registry
        self.window_size = window_size
        self._recent = {}
        self._seen_ids = deque(maxlen=dedup_size)
        self._seen_set = set()
        self._volume = {}
        self._last_price = {}
        self._windows = {}     


    def register(self, ws):
        ws.register_callback("trade", self.on_trade)

  
    def _is_duplicate(self, trade_id):
        if trade_id in self._seen_set:
            return True
        if len(self._seen_ids) == self._seen_ids.maxlen:
            self._seen_set.discard(self._seen_ids[0])   
        self._seen_ids.append(trade_id)
        self._seen_set.add(trade_id)
        return False


    def on_trade(self, message):
        body = message.get("msg", {})
        trade_id = body.get("trade_id")
        if trade_id is None or self._is_duplicate(trade_id):
            return
        market = body["market_ticker"]
        trade = {
            "trade_id": trade_id,
            "market_ticker": market,
            "ts": body.get("ts"),
            "yes_price_dollars": body.get("yes_price_dollars"),
            "no_price_dollars": body.get("no_price_dollars"),
            "count_fp": body.get("count_fp"),
            "taker_side": body.get("taker_side"),
        }

        if self.storage is not None:                                    
            self.storage.write_trade(trade)
        self._recent.setdefault(market, deque(maxlen=self.window_size)).append(trade)  
        self._volume[market] = self._volume.get(market, 0.0) + float(body.get("count_fp") or 0) 
        self._last_price[market] = float(body.get("yes_price_dollars") or 0)           
        logger.debug("trade %s %s @ %s x %s",
                     market, trade["taker_side"], trade["yes_price_dollars"], trade["count_fp"])

        w = self._windows.get(market)                 
        if w is not None and w["active"]:
            w["storage"].write_trade(trade)
            w["count"] += 1

    def get_recent_trades(self, market):
        return list(self._recent.get(market, []))

    def get_trades_in_range(self, market, start_ts, end_ts):
        if self.storage is None:
            return []
        return self.storage.read_trades(market, start_ts, end_ts)

    def get_volume(self, market):
        return self._volume.get(market, 0.0)


    def get_last_price(self, market):
        return self._last_price.get(market)

    async def add_time_window(self, market, settlement_ts, open_offset, close_offset, storage_target):
        self._windows[market] = {
            "open_ts": settlement_ts + open_offset,
            "close_ts": settlement_ts + close_offset,
            "active": False, "storage": storage_target, "count": 0,
        }
        self._windows[market]["task"] = asyncio.create_task(self._run_window(market))
        logger.info("added time-window for %s: [%s, %s]",
                    market, self._windows[market]["open_ts"], self._windows[market]["close_ts"])

    async def _run_window(self, market):
        w = self._windows.get(market)
        if w is None:
            return
        delay = w["open_ts"] - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        if market not in self._windows or time.time() >= w["close_ts"]:
            return                                      
        w["active"] = True
        logger.info("time-window ACTIVATED for %s", market)
        delay = w["close_ts"] - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        w["active"] = False
        logger.info("time-window DEACTIVATED for %s (collected %d trades)", market, w["count"])

    async def remove_time_window(self, market):
        w = self._windows.pop(market, None)
        if w is None:
            return
        if w.get("task") is not None:
            w["task"].cancel()
        logger.info("removed time-window for %s (collected %d trades)", market, w["count"])