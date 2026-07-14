import hashlib
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class OrderBook:
    def __init__(self, storage, request_snapshot=None, use_yes_price=False):
        self.storage = storage
        self.request_snapshot = request_snapshot
        self.use_yes_price = use_yes_price          # item 9
        self._books = {}
        self._seq = {}
        self._stored_hash = {}   

    def _store_snapshot(self, market, book, seq):
        payload = {"yes": dict(book["yes"]), "no": dict(book["no"])}
        h = hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if self._stored_hash.get(market) == h:
            logger.debug("snapshot %s unchanged since last stored; skipping", market)
            return False
        self.storage.write_snapshot(market, datetime.now(timezone.utc).isoformat(),
                                    payload, seq)
        self._stored_hash[market] = h
        return True
    
    def _levels(self, msg, side):
        return msg.get(f"{side}_dollars_fp") or []

    def on_snapshot(self, message):
        body = message.get("msg", {})
        market = body.get("market_ticker")
        book = {"yes": {}, "no": {}}
        for side in ("yes", "no"):
            for level in self._levels(body, side):
                book[side][str(level[0])] = float(level[1])
        self._books[market] = book
        self._seq[market] = message.get("seq")
        logger.info("snapshot %s: yes=%d no=%d levels (seq=%s)",
                    market, len(book["yes"]), len(book["no"]), message.get("seq"))
        
        if self.storage is not None:
            self._store_snapshot(market, book, message.get("seq"))


    def on_delta(self, message):
        body = message.get("msg", {})
        market = body.get("market_ticker")
        book = self._books.get(market)
        if book is None:
            logger.warning("delta for %s with no snapshot yet; skipping", market)
            return
        seq = message.get("seq")
        last = self._seq.get(market)
        if last is not None and seq != last + 1:
            logger.warning("seq gap on %s: expected %s, got %s; refreshing snapshot",
                           market, last + 1, seq)
            self._on_gap(market)
            return
        side = body["side"]
        price = body["price_dollars"]
        new_qty = book[side].get(price, 0.0) + float(body["delta_fp"])
        if new_qty <= 0:
            book[side].pop(price, None)
        else:
            book[side][price] = new_qty
        self._seq[market] = seq
        if self.storage is not None:
            self.storage.write_delta(market, body.get("ts"), side, price, body["delta_fp"], seq)

    def _on_gap(self, market):
        self._books.pop(market, None)
        self._seq.pop(market, None)
        if self.request_snapshot is not None:
            self.request_snapshot(market)

    def get_book(self, market):
        book = self._books.get(market)
        if book is None:
            return None
        return {"yes": dict(book["yes"]), "no": dict(book["no"])}


    def get_best_bid_ask(self, market):
        book = self._books.get(market)
        if book is None:
            return None
        yes_prices = [float(p) for p in book["yes"]]
        no_prices  = [float(p) for p in book["no"]]
        yes_bid = max(yes_prices) if yes_prices else None
        if no_prices:
            best_no = max(no_prices)
            yes_ask = best_no if self.use_yes_price else round(1.0 - best_no, 4)
        else:
            yes_ask = None
        return {"yes_bid": yes_bid, "yes_ask": yes_ask}

    def write_snapshot(self, market):
        book = self._books.get(market)
        if book is None or self.storage is None:
            return
        if self._store_snapshot(market, book, self._seq.get(market)):
            logger.info("wrote snapshot for %s (seq=%s)", market, self._seq.get(market))

 
    def handle_unsubscribe(self, market):
        self.write_snapshot(market)
        self._books.pop(market, None)
        self._seq.pop(market, None)
        logger.info("cleared book state for %s (unsubscribed)", market)