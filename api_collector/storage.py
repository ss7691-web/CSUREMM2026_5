import json
import logging
import duckdb
import config

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, db_path=None, read_only=False):
        self.db_path = db_path or config.DB_PATH
        self.con = duckdb.connect(self.db_path, read_only=read_only)
        self._init_schema()

    def _init_schema(self):
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                ticker                VARCHAR PRIMARY KEY,
                title                 VARCHAR,
                series_ticker         VARCHAR,
                category              VARCHAR,
                price_level_structure VARCHAR,
                open_ts               VARCHAR,
                close_ts              VARCHAR,
                status                VARCHAR
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id          VARCHAR,
                market_ticker     VARCHAR,
                ts                BIGINT,
                yes_price_dollars VARCHAR,
                no_price_dollars  VARCHAR,
                count_fp          VARCHAR,
                taker_side        VARCHAR
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS lob (
                market_ticker VARCHAR,
                ts            VARCHAR,
                side          VARCHAR,
                price_dollars VARCHAR,
                delta_fp      VARCHAR,
                seq           BIGINT
            )
        """)
        
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                market_ticker VARCHAR,
                ts            VARCHAR,
                seq           BIGINT,
                book          VARCHAR
            )
        """)

    def upsert_market(self, market, series_ticker=None, category=None):
        self.con.execute("""
            INSERT INTO markets (ticker, title, series_ticker, category,
                                 price_level_structure, open_ts, close_ts, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker) DO UPDATE SET
                title = excluded.title,
                series_ticker = excluded.series_ticker,
                category = excluded.category,
                price_level_structure = excluded.price_level_structure,
                open_ts = excluded.open_ts,
                close_ts = excluded.close_ts,
                status = excluded.status
        """, [
            market.get("ticker"), market.get("title"), series_ticker, category,
            market.get("price_level_structure"), market.get("open_time"),
            market.get("close_time"), market.get("status"),
        ])

 
    def write_trade(self, trade):
        self.con.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?)",
            [trade.get("trade_id"), trade.get("market_ticker"), trade.get("ts"),
             trade.get("yes_price_dollars"), trade.get("no_price_dollars"),
             trade.get("count_fp"), trade.get("taker_side")])

    def write_delta(self, market, ts, side, price_dollars, delta_fp, seq):
        self.con.execute(
            "INSERT INTO lob VALUES (?, ?, ?, ?, ?, ?)",
            [market, ts, side, price_dollars, delta_fp, seq])

   
    def write_snapshot(self, market, ts, book, seq):
        self.con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
            [market, ts, seq, json.dumps(book)])

  
    def _rows(self, sql, params=None):
        cur = self.con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def read_trades(self, market, start_ts=None, end_ts=None):
        if start_ts is not None and end_ts is not None:
            return self._rows(
                "SELECT * FROM trades WHERE market_ticker = ? AND ts BETWEEN ? AND ? ORDER BY ts",
                [market, start_ts, end_ts])
        return self._rows("SELECT * FROM trades WHERE market_ticker = ? ORDER BY ts", [market])

    
    def list_markets(self):
        return self._rows(
            "SELECT ticker, title, series_ticker, category, status FROM markets ORDER BY ticker")

    
    def read_lob(self, market):
        return self._rows("SELECT * FROM lob WHERE market_ticker = ? ORDER BY seq", [market])

    
    def read_snapshots(self, market):
        return self._rows("SELECT * FROM snapshots WHERE market_ticker = ? ORDER BY seq", [market])

    def read_trades_by_series(self, series_ticker):
        return self._rows("""
            SELECT t.* FROM trades t
            JOIN markets m ON t.market_ticker = m.ticker
            WHERE m.series_ticker = ?
            ORDER BY t.market_ticker, t.ts""", [series_ticker])

    def read_lob_by_series(self, series_ticker):
        return self._rows("""
            SELECT l.* FROM lob l
            JOIN markets m ON l.market_ticker = m.ticker
            WHERE m.series_ticker = ?
            ORDER BY l.market_ticker, l.seq""", [series_ticker])

    def read_snapshots_by_series(self, series_ticker):
        return self._rows("""
            SELECT s.* FROM snapshots s
            JOIN markets m ON s.market_ticker = m.ticker
            WHERE m.series_ticker = ?
            ORDER BY s.market_ticker, s.seq""", [series_ticker])

    def close(self):
        self.con.close()