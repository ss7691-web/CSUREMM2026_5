import requests
import pandas as pd
import os

CATEGORY = "Politics"          # <- verify this returns results
OUT_DIR  = "hourly_market_data/data"
OUT_CSV  = f"{OUT_DIR}/all_tech.csv"

all_series = []
cursor = ""
while True:
    params = {"category": CATEGORY, "limit": 1000}
    if cursor:
        params["cursor"] = cursor
    resp = requests.get(
        "https://external-api.kalshi.com/trade-api/v2/series",
        params=params,
    )
    data = resp.json()
    all_series.extend(data.get("series") or [])
    cursor = data.get("cursor", "")
    if not cursor:
        break

# keep just the unique series tickers, order preserved
rows, seen = [], set()
for s in all_series:
    tk = s.get("ticker")
    if tk and tk not in seen:
        seen.add(tk)
        rows.append({"series_ticker": tk, "title": s.get("title", "")})

os.makedirs(OUT_DIR, exist_ok=True)
df = pd.DataFrame(rows)
df.to_csv(OUT_CSV, index=False)

print(f"Status: {resp.status_code}")
print(f"Found {len(rows)} unique series in '{CATEGORY}' -> {OUT_CSV}")
print(df["series_ticker"].tolist())