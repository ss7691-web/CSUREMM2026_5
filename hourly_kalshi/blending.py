import glob, os
import pandas as pd

# ── the {series: [etf, ...]} dictionary ──────────────────────────────
blend = {
    "KXSPACEXORBIT": ["S_P","NDX","QQQ","XSD","XLK","CHAT","FTEC","AIQ"],
    "KXBESTLLMOS": ["S_P","XLK","ARTY","XSD","AIQ","FTEC","NDX","QQQ","CHAT"],
    "KXSORA": ["ARTY","QQQ","S_P","NDX"],
    "KXNEWOUTBREAK-P": ["ARTY","S_P","CHAT","NDX","QQQ","FTEC","AIQ","XLK","XSD"],
    "KXSWEBENCH": ["AIQ","CHAT"],
    "KXOAISOCIAL": ["NDX","XSD","QQQ","XLK","ARTY","FTEC","CHAT","AIQ","S_P"],
    "KXNEWOUTBREAKPMEASLES": ["AIQ","XSD","CHAT","XLK"],
    "KXOAIPLATEAU": ["ARTY"],
}

FORECAST_DIR = "hourly_market_data/data/forecasts"
FREQS = {"daily": "D", "weekly": "W"}

def _id_label(n):
    """0->A, 25->Z, 26->AA ..."""
    s = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def _load_open_times(series):
    opens = {}
    for kind in ("historical", "live"):
        p = f"hourly_market_data/data/{series}/{series}_markets_{kind}.csv"
        if not os.path.exists(p):
            continue
        m = pd.read_csv(p)
        for _, r in m.iterrows():
            t = pd.to_datetime(r["open_time"], utc=True)
            if r["ticker"] not in opens or t < opens[r["ticker"]]:
                opens[r["ticker"]] = t
    return opens

def _load_etf(etf, suffix):
    path = os.path.join(FORECAST_DIR, f"{etf}{suffix}.csv")
    if not os.path.exists(path):
        print(f"    missing ETF file: {path}")
        return None
    df = pd.read_csv(path)
    date_col = next((c for c in df.columns if c.lower() in ("date", "datetime", "day")), df.columns[0])
    df["_date"] = pd.to_datetime(df[date_col]).dt.normalize()
    df = df.drop(columns=[date_col])
    df = df.rename(columns={c: f"{etf}_{c}" for c in df.columns if c != "_date"})
    df[f"{etf}_date"] = df["_date"]
    return df.sort_values("_date").reset_index(drop=True)

def _build_markets(series):
    files = sorted(glob.glob(f"hourly_market_data/data/{series}/**/*_candlesticks.csv", recursive=True))
    if not files:
        return None
    opens = _load_open_times(series)
    files.sort(key=lambda f: (
        opens.get(os.path.basename(f).replace("_candlesticks.csv", ""),
                  pd.Timestamp.max.tz_localize("UTC")),
        f,
    ))
    frames = []
    for i, f in enumerate(files):
        df = pd.read_csv(f)
        df.insert(0, "id", _id_label(i))
        frames.append(df)
    blended = pd.concat(frames, ignore_index=True)
    blended["dt"] = pd.to_datetime(blended["end_period_ts"], unit="s", utc=True)
    blended["candle_date"] = (blended["dt"].dt.tz_convert("America/New_York")
                              .dt.normalize().dt.tz_localize(None))
    return blended, len(files)

def blend_series(series):
    built = _build_markets(series)
    if built is None:
        print(f"[{series}] no candlestick files")
        return
    base, n_markets = built
    etfs = blend.get(series, [])

    for freq, suffix in FREQS.items():
        out_df = base.copy()
        used = 0
        for etf in etfs:
            etf_df = _load_etf(etf, suffix)
            if etf_df is None:
                continue
            out_df = pd.merge_asof(
                out_df.sort_values("candle_date"),
                etf_df.sort_values("_date"),
                left_on="candle_date", right_on="_date",
                direction="backward",
            ).drop(columns=["_date"])
            used += 1
        out_df = out_df.sort_values(["dt", "id"]).drop(columns=["dt"]).reset_index(drop=True)
        os.makedirs("hourly_market_data/data/blended", exist_ok=True)
        out = f"hourly_market_data/data/blended/{series}_blended_{freq}.csv"
        out_df.to_csv(out, index=False)
        print(f"[{series}] {freq}: {n_markets} markets x {used} ETFs -> {len(out_df)} rows -> {out}")

# ── quick test: run this file directly to blend one series ──
if __name__ == "__main__":
    blend_series("KXBESTLLMOS")
    blend_series("KXSPACEXORBIT")
    blend_series("KXNEWOUTBREAKPCOVID")
    blend_series("KXSORA")
    blend_series("KXNEWOUTBREAK-P")
    blend_series("KXSWEBENCH")
    blend_series("KXLEAVESEANCOOK")
    blend_series("KXOAISOCIAL")
    blend_series("KXTESLAGAS")
    blend_series("KXASTEROID")
    blend_series("KXNEWOUTBREAKPMEASLES")
    blend_series("KXOPENAIPROFIT")
    blend_series("KXOAIPLATEAU")