import requests
import csv
import os
import time

BASE = "https://external-api.kalshi.com/trade-api/v2"

def _discover_all(url, series):
    out, cursor = [], ""
    while True:
        params = {"series_ticker": series, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(url, params=params)
        while resp.status_code == 429:
            time.sleep(1)
            resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        out.extend(data["markets"])
        cursor = data.get("cursor", "")
        if not cursor:
            break
    return out

def _write_csv(path, markets):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "open_time", "close_time"])
        writer.writeheader()
        for m in markets:
            writer.writerow({"ticker": m["ticker"], "open_time": m["open_time"], "close_time": m["close_time"]})

def discover(series):
    folder = f"hourly_market_data/data/{series}"
    os.makedirs(folder, exist_ok=True)

    live = _discover_all(f"{BASE}/markets", series)
    hist = _discover_all(f"{BASE}/historical/markets", series)

    _write_csv(f"{folder}/{series}_markets_live.csv", live)
    _write_csv(f"{folder}/{series}_markets_historical.csv", hist)

    print(f"[{series}] discover: {len(live)} live, {len(hist)} historical markets")
    return live, hist