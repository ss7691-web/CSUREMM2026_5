import requests
import pandas as pd
import time
import os

from adding_resoultion_time import resolution_for  

BASE = "https://external-api.kalshi.com/trade-api/v2"

MAX_CANDLES = 5000

def get_with_backoff(url, params, max_retries=6, base_delay=1.0):
    for attempt in range(max_retries):
        resp = requests.get(url, params=params)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else base_delay * (2 ** attempt)
            print(f"  rate limited, waiting {delay:.1f}s")
            time.sleep(delay)
            continue
        return resp
    return resp

def _append(df, path, expected_cols):
    df = df.reindex(columns=expected_cols)
    file_exists = os.path.exists(path)
    df.to_csv(path, mode="a", header=not file_exists, index=False)

def fetch_candlesticks(series, time_kind):
    """time_kind: 'live' or 'historical'"""
    markets_path = f"hourly_market_data/data/{series}/{series}_markets_{time_kind}.csv"
    markets = pd.read_csv(markets_path)
    print(f"[{series}/{time_kind}] loaded {len(markets)} markets")

    
    expected_cols = None
    grand_total, skipped = 0, []

    for _, row in markets.iterrows():
        ticker = row["ticker"]
        market_dir = f"hourly_kalshi/data/{series}/{ticker}"
        os.makedirs(market_dir, exist_ok=True)
        output_path = f"{market_dir}/{ticker}_candlesticks.csv"

        open_ts = int(pd.to_datetime(row["open_time"], utc=True).timestamp())
        close_ts = int(pd.to_datetime(row["close_time"], utc=True).timestamp())

        # CHANGED: pick resolution per ticker from how long the market is open
        dd, period_interval = resolution_for(open_ts, close_ts)
        window_seconds = MAX_CANDLES * period_interval * 60

        if time_kind == "historical":
            url = f"{BASE}/historical/markets/{ticker}/candlesticks"
        else:
            url = f"{BASE}/series/{series}/markets/{ticker}/candlesticks"

        # ── walk the full open→close range in 5000-candle windows ──
        ticker_rows = 0
        win_start = open_ts
        while win_start < close_ts:
            win_end = min(win_start + window_seconds, close_ts)

            resp = get_with_backoff(url, params={
                "start_ts": win_start,
                "end_ts": win_end,
                "period_interval": period_interval,  # CHANGED: was PERIOD_INTERVAL
            })
            time.sleep(0.15)

            if resp.status_code != 200:
                print(f"  {ticker} [{win_start}-{win_end}]: failed ({resp.status_code}) {resp.text[:120]}")
                win_start = win_end
                continue

            candles = resp.json().get("candlesticks", [])
            if not candles:
                win_start = win_end
                continue

            df = pd.json_normalize(candles)
            df["ticker"] = ticker
            # CHANGED: record market duration and chosen resolution in the CSV
            df["delta_days"] = dd
            df["period_interval"] = period_interval
            if expected_cols is None:
                expected_cols = list(df.columns)
            _append(df, output_path, expected_cols)
            ticker_rows += len(df)
            win_start = win_end

        if ticker_rows == 0:
            print(f"  {ticker}: no candlesticks")
            skipped.append(ticker)
        else:
            print(f"  {ticker}: {ticker_rows} candles")
        grand_total += ticker_rows

    print(f"[{series}/{time_kind}] done — {grand_total} rows, {len(skipped)} skipped")
    return skipped