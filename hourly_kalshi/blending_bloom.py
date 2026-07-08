import glob
import pandas as pd
import os

SERIES = "KXBTC15M"
DATA_DIR = f"hourly_market_data/data_bitcoin/{SERIES}"

candle_files = glob.glob(f"{DATA_DIR}/**/*_candlesticks.csv", recursive=True)
candlesticks = pd.concat([pd.read_csv(f) for f in candle_files], ignore_index=True)
times = pd.concat([
    pd.read_csv(f"{DATA_DIR}/{SERIES}_markets_historical.csv")[["ticker", "open_time", "close_time"]],
    pd.read_csv(f"{DATA_DIR}/{SERIES}_markets_live.csv")[["ticker", "open_time", "close_time"]],
]).drop_duplicates(subset="ticker", keep="first")

candlesticks = candlesticks.merge(times, on="ticker", how="left")

candlesticks["resolution_time"] = (
    pd.to_datetime(candlesticks["close_time"], utc=True)
      .dt.tz_convert("America/New_York")
      .dt.strftime("%Y-%m-%d %H:%M:%S")
)

bloom = pd.read_csv("hourly_market_data/data_bitcoin/forecast_BRTI15m.csv")
bloom["Date"] = pd.to_datetime(bloom["Date"]).dt.strftime("%Y-%m-%d %H:%M:%S")

btc_data_final = bloom.merge(
    candlesticks, how="outer", left_on="Date", right_on="resolution_time"
)
os.makedirs("hourly_market_data/data/blended", exist_ok=True)
btc_data_final.to_csv("hourly_market_data/data/blended/blended_BTC15MIN.csv", index=False)
print("written", len(btc_data_final), "rows")