import pandas as pd 

df = pd.read_csv("hourly_kalshi/data/blended/blended_KXBRENTD.csv")

df["date_only"] = pd.to_datetime(df["converted_time"].str[:10])
 
df["weekday"] = df["date_only"].dt.dayofweek.apply(
    lambda d: "weekend" if d >= 5 else "weekday"
)
 
weekday_df = df[df["weekday"] == "weekday"].copy()
 
weekday_df.to_csv("hourly_kalshi/data/blended/weekday_KXBRENTD_blended.csv", index=False)
