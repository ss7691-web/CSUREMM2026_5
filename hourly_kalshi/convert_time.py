import glob
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

eastern = ZoneInfo("America/New_York")

def _to_est(ts, tz=eastern):
    return datetime.fromtimestamp(ts, tz=tz)

def convert_series(series):
    paths = glob.glob(f"hourly_kalshi/data/{series}/*/*_candlesticks.csv")
    print(f"[{series}] converting time for {len(paths)} files")
    for path in paths:
        df = pd.read_csv(path)
        if "end_period_ts" not in df.columns:
            continue
        df["converted_time"] = df["end_period_ts"].apply(_to_est)
        df.to_csv(path, index=False)

'''
df.to_csv("hourly_market_data/data/btcusd_1-min_data.csv", index=False)
'''

'''
import pandas as pd
from datetime import timedelta

# Read the CSV file
df = pd.read_csv('hourly_market_data/data/btcusd_1-min_data.csv')

# Convert Unix timestamp (seconds) to datetime, then shift to EDT (UTC-4)
#f['Date'] = pd.to_datetime(df['Timestamp'], unit='s') - timedelta(hours=4)


#df['Date'] = df['Date'].dt.strftime('%Y-%m-%d %H:%M:%S')

df_15m = df[df['Timestamp'] % 900 == 0]
# Save to a new CSV
df_15m.to_csv('hourly_market_data/data/btcusd_1-min_data_15min.csv', index=False)
'''