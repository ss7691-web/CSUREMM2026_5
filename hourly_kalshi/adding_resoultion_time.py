CUTOFF_DAYS = 1          
MINUTE_INTERVAL = 1      
HOUR_INTERVAL = 60       


def delta_days(open_ts, close_ts):
    """Days the market is open (float), from unix timestamps."""
    return (close_ts - open_ts) / 86400


def resolution_for(open_ts, close_ts, cutoff_days=CUTOFF_DAYS):
    """Return (delta_days, period_interval) for a market."""
    dd = delta_days(open_ts, close_ts)
    interval = MINUTE_INTERVAL if dd <= cutoff_days else HOUR_INTERVAL
    return dd, interval
