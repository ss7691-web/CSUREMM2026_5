from discover import discover
from candlesticks import fetch_candlesticks
from convert_time import convert_series
import pandas as pd


SERIES_LIST = ["KXBRENTD"]

#SERIES_LIST = pd.read_csv("hourly_kalshi/data/all_tech.csv")["series_ticker"].tolist()

for series in SERIES_LIST:
    print(f"\n===== {series} =====")
    discover(series)
    fetch_candlesticks(series, "historical")
    fetch_candlesticks(series, "live")
    convert_series(series)

print("\nAll done.")