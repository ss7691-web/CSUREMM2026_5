import logging
from urllib.parse import urlparse
import config
import asyncio
import websockets
import json
import random
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class KalshiWebSocketClient:

    SERVER_ERROR_CODES = {10, 17, 18}     

    def __init__(self, auth, ws_url=None):
        self.auth = auth
        self.ws_url = ws_url or config.WS
        self.sign_path = urlparse(self.ws_url).path      
        self.connection = None
        self._next_id = 1
        self.subscriptions = {}
        self._seq = {}
        self._callbacks = {}
        self._pending = {}   

    async def connect(self):
        headers = self.auth.get_headers("GET", self.sign_path)
        self.connection = await websockets.connect(self.ws_url, additional_headers=headers)
        logger.info("websocket connected to %s", self.ws_url)
        return self.connection

    def _next_command_id(self):
        cid = self._next_id
        self._next_id += 1
        return cid
    
    async def _send(self, command):
        conn = self.connection
        if conn is None:
            logger.warning("skip send (no connection): %s", command.get("cmd"))
            return
        try:
            await conn.send(json.dumps(command))
            logger.debug("sent command: %s", command)
        except websockets.ConnectionClosed as e:
            logger.warning("send failed, connection closed (%s); will recover on reconnect: %s",
                           e, command.get("cmd"))

    async def subscribe(self, channels, market_tickers=None):
            cid = self._next_command_id()
            params = {"channels": channels}
            if market_tickers is not None:
                params["market_tickers"] = market_tickers
            command = {"id": cid, "cmd": "subscribe", "params": params}
            self._pending[cid] = market_tickers      
            await self._send(command)
            return cid

    def _record_subscribed(self, message):
        cmd_id = message.get("id")
        body = message.get("msg", {})
        sid = body.get("sid")
        channel = body.get("channel")
        tickers = self._pending.pop(cmd_id, None)   
        self.subscriptions[sid] = {"channel": channel, "tickers": tickers}
        return sid

    async def update_subscription(self, sid, action, market_tickers):
        cid = self._next_command_id()
        command = {
            "id": cid,
            "cmd": "update_subscription",
            "params": {"sids": [sid], "action": action, "market_tickers": market_tickers},
        }
        await self._send(command)
        sub = self.subscriptions.get(sid)
        if sub is not None and sub.get("tickers") is not None:
            current = set(sub["tickers"])
            if action == "add_markets":
                current.update(market_tickers)
            elif action == "delete_markets":
                current.difference_update(market_tickers)
            sub["tickers"] = list(current)
        return cid
    
    def register_callback(self, message_type, handler):
        self._callbacks[message_type] = handler

    async def _check_seq(self, message):
        sid = message.get("sid")
        seq = message.get("seq")
        if sid is None or seq is None:
            return                              # no seq to track (e.g. the "subscribed" ack)
        last = self._seq.get(sid)
        if last is not None and seq != last + 1:
            logger.warning("seq gap on sid %s: expected %s, got %s", sid, last + 1, seq)
            sub = self.subscriptions.get(sid)
            tickers = sub.get("tickers") if sub else None
            if tickers:
                await self.update_subscription(sid, "get_snapshot", tickers)
        self._seq[sid] = seq

    async def _receive_loop(self):
        async for raw in self.connection:
            message = json.loads(raw)
            await self._check_seq(message)          
            mtype = message.get("type")
            if mtype == "subscribed":
                self._record_subscribed(message)
            elif mtype in ("ok", "unsubscribed"):
                logger.info("%s: %s", mtype, message)
            elif mtype == "error":
                self._handle_error(message)
            else:
                handler = self._callbacks.get(mtype)
                if handler is not None:
                    handler(message)
                else:
                    logger.debug("no callback for type %s", mtype)

    def _log_disconnect(self, cause):
        record = {
            "disconnected_at": datetime.now(timezone.utc).isoformat(),
            "cause": str(cause),
            "active_sids": {sid: sub["channel"] for sid, sub in self.subscriptions.items()},
            "last_seq": dict(self._seq),
        }
        logger.warning("disconnect: %s", record)

    async def _resubscribe(self):
        previous = [(sub["channel"], sub["tickers"]) for sub in self.subscriptions.values()]
        self.subscriptions = {}
        self._seq = {}
        self._pending = {}
        self._next_id = 1                       
        for channel, tickers in previous:
            await self.subscribe([channel], tickers)

    def _sid_for_channel(self, channel):
        for sid, sub in self.subscriptions.items():
            if sub["channel"] == channel:
                return sid
        return None


    async def add_markets(self, channel, tickers):
        sid = self._sid_for_channel(channel)
        if sid is None:
            return await self.subscribe([channel], tickers)        
        return await self.update_subscription(sid, "add_markets", tickers)


    async def remove_markets(self, channel, tickers):
        sid = self._sid_for_channel(channel)
        if sid is None:
            logger.warning("remove_markets: no active subscription for channel %s", channel)
            return None
        return await self.update_subscription(sid, "delete_markets", tickers)
    
 
    def _handle_error(self, message):
        body = message.get("msg") or {}
        code = body.get("code", message.get("code"))
        text = body.get("msg") or message.get("msg")
        if code in self.SERVER_ERROR_CODES:
            logger.warning("server error %s (transient): %s", code, text)
        else:
            logger.error("user error %s (bug — not retrying): %s", code, text)
    

    async def run(self):
        attempt = 0
        while True:
            try:
                await self.connect()
                await self._resubscribe()       
                attempt = 0                     
                await self._receive_loop()  
            except (websockets.ConnectionClosed, OSError) as e:
                self._log_disconnect(e)
            else:
                self._log_disconnect("receive loop ended")
            delay = min(2 ** attempt, 60) + random.uniform(0, 1)
            logger.info("reconnecting in %.1fs (attempt %d)", delay, attempt + 1)
            await asyncio.sleep(delay)
            attempt += 1
    
    async def unsubscribe(self, sid):
        cid = self._next_command_id()
        await self._send({"id": cid, "cmd": "unsubscribe", "params": {"sids": [sid]}})
        return cid

    async def close(self):
        if self.connection is not None:
            for sid in list(self.subscriptions.keys()):
                try:
                    await self.unsubscribe(sid)
                except Exception as e:
                    logger.warning("error unsubscribing sid %s: %s", sid, e)
            await self.connection.close()
        logger.info("websocket closed gracefully")
