import glob
import pandas as pd
import os

SERIES = "KXBRENTD"
DATA_DIR = f"hourly_kalshi/data/{SERIES}"

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
      .dt.strftime("%Y-%m-%d")
)

bloom = pd.read_csv("hourly_kalshi/data/forecast_BRENT.csv")
bloom["Date"] = pd.to_datetime(bloom["Date"]).dt.strftime("%Y-%m-%d")

btc_data_final = bloom.merge(
    candlesticks, how="inner", left_on="Date", right_on="resolution_time"
)
os.makedirs("hourly_kalshi/data/blended", exist_ok=True)
btc_data_final.to_csv("hourly_kalshi/data/blended/blended_KXBRENTD.csv", index=False)
print("written", len(btc_data_final), "rows")