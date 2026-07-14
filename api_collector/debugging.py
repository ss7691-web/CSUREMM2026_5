import json
from storage import Storage

st = Storage(db_path="test.duckdb")

st.upsert_market(
    {"ticker": "KXTEST-1", "title": "Will X happen?", "status": "active",
     "open_time": "2026-06-22T09:00:00Z", "close_time": "2026-06-23T21:00:00Z",
     "price_level_structure": "linear_cent"},
    series_ticker="KXTEST", category="Science and Technology")

st.write_trade({"trade_id": "t1", "market_ticker": "KXTEST-1", "ts": 1782181213,
                "yes_price_dollars": "0.40", "no_price_dollars": "0.60",
                "count_fp": "100", "taker_side": "yes"})
st.write_delta("KXTEST-1", "2026-06-22T17:26:15Z", "no", "0.90", "555.00", 2)
st.write_snapshot("KXTEST-1", "2026-06-22T17:26:00Z", {"yes": {"0.40": 100.0}, "no": {}}, 1)

print("markets  :", st.list_markets())
print("trades   :", st.read_trades("KXTEST-1"))
print("trades rng:", st.read_trades("KXTEST-1", 1782181000, 1782182000))
print("lob      :", st.read_lob("KXTEST-1"))
print("snapshots:", st.read_snapshots("KXTEST-1"))
st.close()