import matplotlib
from scipy.stats import norm
from scipy.optimize import minimize
import numpy as np
import matplotlib.pyplot as plt
import glob
import os

def objective_multi(weights, p1, p, gk0_list, gk1_list, c, sigma, k_list, lam):
    n       = len(k_list)
    a_long  = weights[0]
    a_short = weights[1]
    a       = a_long - a_short
    bs_low  = np.array(weights[2:n+2])
    bs_high = np.array(weights[n+2:])
    gk0     = np.array(gk0_list)
    gk1     = np.array(gk1_list)
    k       = np.array(k_list)

    z   = (k - p1) / sigma
    fk0 = norm.cdf(z)
    fk1 = 1 - fk0
    phi = norm.pdf(z)

    gold_ret = a * (p1 - p) / p
    pred_ret = np.sum(bs_low  / gk0 * (fk0 - gk0)) \
             + np.sum(bs_high / gk1 * (fk1 - gk1))
    friction = np.sum(bs_low  * 0.07 * gk0 * (1 - gk0)) \
             + np.sum(bs_high * 0.07 * gk1 * (1 - gk1))
    num = gold_ret + pred_ret - friction

    gold_var = a**2 * sigma**2 / p**2
    z_min    = np.minimum(z[:, None], z[None, :])
    cov_LL   = (norm.cdf(z_min) - fk0[:, None] * fk0[None, :]) / (gk0[:, None] * gk0[None, :])
    z_max    = np.maximum(z[:, None], z[None, :])
    cov_HH   = ((1 - norm.cdf(z_max)) - fk1[:, None] * fk1[None, :]) / (gk1[:, None] * gk1[None, :])
    cov_LH   = (np.maximum(0, fk0[:, None] - fk0[None, :]) - fk0[:, None] * fk1[None, :]) \
               / (gk0[:, None] * gk1[None, :])
    pred_var = bs_low  @ cov_LL @ bs_low \
             + bs_high @ cov_HH @ bs_high \
             + 2 * bs_low @ cov_LH @ bs_high

    gold_cov_low  = a * np.sum(bs_low  * (-sigma * phi) / (p * gk0))
    gold_cov_high = a * np.sum(bs_high * ( sigma * phi) / (p * gk1))
    var = gold_var + pred_var + 2 * (gold_cov_low + gold_cov_high)

    raw_sharpe = num / np.sqrt(max(var, 1e-15))
    all_b     = bs_low+bs_high   # sum low+high per strike
    b_weights = all_b / (np.sum(all_b) + 1e-9)
    entropy   = -np.sum(b_weights * np.log(b_weights + 1e-9))
    return -raw_sharpe - lam * entropy

def compute_ev(weights, p1, p, gk0_list, gk1_list, sigma, k_list):
    n       = len(k_list)
    a_long  = weights[0]
    a_short = weights[1]
    a       = a_long - a_short
    bs_low  = np.array(weights[2:n+2])
    bs_high = np.array(weights[n+2:])
    gk0     = np.array(gk0_list)
    gk1     = np.array(gk1_list)
    k       = np.array(k_list)

    z   = (k - p1) / sigma
    fk0 = norm.cdf(z)
    fk1 = 1 - fk0
    phi = norm.pdf(z)

    gold_ret = a * (p1 - p) / p
    pred_ret = np.sum(bs_low  / gk0 * (fk0 - gk0)) \
             + np.sum(bs_high / gk1 * (fk1 - gk1))
    friction = np.sum(bs_low  * 0.07 * gk0 * (1 - gk0)) \
             + np.sum(bs_high * 0.07 * gk1 * (1 - gk1))
    num = gold_ret + pred_ret - friction
    return num


class _NoTradeResult:
    __slots__ = ('x',)
    def __init__(self, n):
        self.x = np.zeros(2 * n + 2)


def run_optimizer(p1, p, gk0_list, gk1_list, c, sigma, k_list):
    n   = len(k_list)
    gk0 = np.array(gk0_list)
    gk1 = np.array(gk1_list)
    k   = np.array(k_list)
    z   = (k - p1) / sigma
    fk0 = norm.cdf(z)
    fk1 = 1 - fk0

    best = None
    for case in cases:
        w0 = np.zeros(2*n + 2)
        if case['long']:
            w0[0] = 0.95
        else:
            w0[1] = 0.95
        if case['low'] and case['high']:
            w0[2:2+n] = 0.025 / n
            w0[2+n:]  = 0.025 / n
        elif case['low']:
            b = np.argmax((fk0 - gk0) / gk0)
            w0[2 + b] = 0.05
        else:
            b = np.argmax((fk1 - gk1) / gk1)
            w0[2 + n + b] = 0.05
        bounds = [
            (0, 1) if case['long']     else (0, 0),
            (0, 1) if not case['long'] else (0, 0),
            *[(0, 1) if case['low']  else (0, 0)] * n,
            *[(0, 1) if case['high'] else (0, 0)] * n,
        ]
        constraints = [{'type': 'ineq', 'fun': lambda w: 1 - np.sum(w)}]  # sum of weights <= 1
        res = minimize(objective_multi, w0,
                       args=(p1, p, gk0_list, gk1_list, c, sigma, k_list, lam),
                       method='SLSQP', bounds=bounds, constraints=constraints)
        if not res.success:
            continue

        net_edge = compute_ev(res.x, p1, p, gk0_list, gk1_list, sigma, k_list)
        if net_edge <= 0:
            continue
        

        if best is None or res.fun < best.fun:
            best = res
        
    if best is None:
        return _NoTradeResult(n)
    total = best.x.sum()
    if total > 1e-9:
        best.x = best.x / total

            
    return best

def compute_kelly(weights, returns, p1, p, gk0_list, gk1_list, c, sigma, k_list):
    ev  = compute_ev(weights, p1, p, gk0_list, gk1_list, sigma, k_list)
    var = compute_position_var(weights, p1, p, gk0_list, gk1_list, sigma, k_list)   # was: compute_var(returns)
    if np.isnan(ev):
        print(f"    EV IS NAN")
        print(f"    p1={p1}  p={p}  sigma={sigma}")
        print(f"    gk0_list={gk0_list}")
        print(f"    gk1_list={gk1_list}")
        print(f"    weights={weights}")
    if np.isnan(var) or var <= 0:
        return 0
    return max(min(ev / (var), 2), 0)




# --- data ---
k_list   = [4092, 4102, 4112, 4122, 4132, 4142, 4152, 4162, 4172, 4182, 4192, 4202, 4212, 4222, 4232, 4242, 4252, 4262, 4272, 4282, 4292, 4302, 4312, 4322, 4332, 4342, 4352, 4362, 4372, 4382, 4392, 4402, 4412, 4422, 4432, 4442, 4452, 4462]
gk1_list = [0.99,0.99,0.99,0.99,0.99,0.99,0.99,0.99,0.99,0.98,0.99,0.98,0.98,
 0.99,0.98,0.97,0.95,0.93,0.93,0.88,0.82,0.79,0.67,0.60,0.48,0.46,
 0.35,0.24,0.22,0.15,0.11,0.08,0.05,0.04,0.04,0.03,0.01,0.02]
gk0_list = [0.01,0.01,0.01,0.01,0.01,0.01,0.01,0.01,0.01,0.02,0.01,0.02,0.02,
 0.01,0.02,0.03,0.05,0.07,0.07,0.12,0.18,0.21,0.33,0.40,0.52,0.54,
 0.65,0.76,0.78,0.85,0.89,0.92,0.95,0.96,0.96,0.97,0.99,0.98]  # market prices for each upper contract
n        = len(k_list)
p1, p, c, sigma = 4309.74, 4329.88, 0.0001, 100
lam = .01
bankroll = 100

# --- optimizer ---
cases = [
    {'long': True,  'low': True,  'high': False},
    {'long': True,  'low': False, 'high': True},
    {'long': False, 'low': True,  'high': False},
    {'long': False, 'low': False, 'high': True},
    {'long': True,  'low': True,  'high': True},
    {'long': False, 'low': True,  'high': True},
]


import pandas as pd

def compute_position_var(weights, p1, p, gk0_list, gk1_list, sigma, k_list):
    n       = len(k_list)
    a       = weights[0] - weights[1]
    bs_low  = np.array(weights[2:n+2])
    bs_high = np.array(weights[n+2:])
    gk0     = np.array(gk0_list)
    gk1     = np.array(gk1_list)
    k       = np.array(k_list)

    z   = (k - p1) / sigma
    fk0 = norm.cdf(z)
    fk1 = 1 - fk0
    phi = norm.pdf(z)

    gold_var = a**2 * sigma**2 / p**2
    z_min    = np.minimum(z[:, None], z[None, :])
    cov_LL   = (norm.cdf(z_min) - fk0[:, None] * fk0[None, :]) / (gk0[:, None] * gk0[None, :])
    z_max    = np.maximum(z[:, None], z[None, :])
    cov_HH   = ((1 - norm.cdf(z_max)) - fk1[:, None] * fk1[None, :]) / (gk1[:, None] * gk1[None, :])
    cov_LH   = (np.maximum(0, fk0[:, None] - fk0[None, :]) - fk0[:, None] * fk1[None, :]) \
               / (gk0[:, None] * gk1[None, :])
    pred_var = bs_low  @ cov_LL @ bs_low \
             + bs_high @ cov_HH @ bs_high \
             + 2 * bs_low @ cov_LH @ bs_high

    gold_cov_low  = a * np.sum(bs_low  * (-sigma * phi) / (p * gk0))
    gold_cov_high = a * np.sum(bs_high * ( sigma * phi) / (p * gk1))
    return gold_var + pred_var + 2 * (gold_cov_low + gold_cov_high)

def load_market_file(path, c=0.0001):
    df = pd.read_csv(path)
    rename_map = {'yes_ask.close.dollars': 'yes_ask.close'}
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    df = df.dropna(subset=['Actual', 'Forecast', 'SD', 'Realise', 'yes_ask.close', 'threshold'])
    df['no'] = 1 - df['yes_ask.close']   # kept as your original 1.1
    df = df[(df['yes_ask.close'] > 0) & (df['yes_ask.close'] < 1)]

    data = []
    for date, day in df.sort_values('threshold').groupby('Date', sort=True):
        if day.empty:
            continue
        data.append({
            'date': date,
            'p1': day['Forecast'].iloc[0],
            'p': day['Actual'].iloc[0],
            'sigma': day['SD'].iloc[0],
            'k_list': day['threshold'].tolist(),
            'gk1_list': day['yes_ask.close'].tolist(),
            'gk0_list': day['no'].tolist(),
            'realized_price': day['Realise'].iloc[0],
        })
    return data




#best = run_optimizer(p1, p, gk0_list, gk1_list, c, sigma, k_list)

#w = best.x
#a_net = w[0] - w[1]
#print(f"a_net={a_net:.4f}  ({'long' if a_net >= 0 else 'short'} gold)")
#for i, k in enumerate(k_list):
#    bl, bh = w[2+i], w[2+n+i]
#    if bl > 0.001 or bh > 0.001:
#        print(f"  k={k}  b_low={bl:.4f}  b_high={bh:.4f}")
#pure_sharpe = -objective_multi(w, p1, p, gk0_list, gk1_list, c, sigma, k_list, 0)


#print(f"Sharpe:          {pure_sharpe:.4f}")

#print(f"Sum of weights:  {np.sum(w):.4f}")
#all_b     = w[2:n+2] + w[n+2:]   # sum low+high per strike
#b_weights = all_b / (np.sum(all_b) + 1e-9)
#entropy   = -np.sum(b_weights * np.log(b_weights + 1e-9))
#print(f"Entropy:         {entropy:.4f}")



def compute_realized_return(w, p1, p, gk0_list, gk1_list, k_list, realized_price):
    n       = len(k_list)
    a_net   = w[0] - w[1]
    bs_low  = w[2:n+2]
    bs_high = w[n+2:]
    gk0     = np.array(gk0_list)
    gk1     = np.array(gk1_list)
    k       = np.array(k_list)

    # gold return
    gold_ret = a_net * (realized_price - p) / p

    # prediction market payoffs: 1 if condition met, 0 otherwise
    low_payoff  = (realized_price < k).astype(float)   # b_low wins if price ends below k
    high_payoff = (realized_price > k).astype(float)   # b_high wins if price ends above k

    pred_ret = np.sum(bs_low  / gk0 * (low_payoff  - gk0)) \
             + np.sum(bs_high / gk1 * (high_payoff - gk1))

    friction = np.sum(bs_low  * 0.07 * gk0 * (1 - gk0)) \
             + np.sum(bs_high * 0.07 * gk1 * (1 - gk1))
    

    return (gold_ret + pred_ret - friction)

def compute_gold_return(w, p1, p, gk0_list, gk1_list, k_list, realized_price):
    n       = len(k_list)
    a_net   = w[0] - w[1]
    bs_low  = w[2:n+2]
    bs_high = w[n+2:]
    gk0     = np.array(gk0_list)
    gk1     = np.array(gk1_list)
    k       = np.array(k_list)

    # gold return
    return (a_net * (realized_price - p) / p)
def compute_pm_return(w, p1, p, gk0_list, gk1_list, k_list, realized_price):
    n       = len(k_list)
    a_net   = w[0] - w[1]
    bs_low  = w[2:n+2]
    bs_high = w[n+2:]
    gk0     = np.array(gk0_list)
    gk1     = np.array(gk1_list)
    k       = np.array(k_list)

   
    # prediction market payoffs: 1 if condition met, 0 otherwise
    low_payoff  = (realized_price < k).astype(float)   # b_low wins if price ends below k
    high_payoff = (realized_price > k).astype(float)   # b_high wins if price ends above k

    pred_ret = np.sum(bs_low  / gk0 * (low_payoff  - gk0)) \
             + np.sum(bs_high / gk1 * (high_payoff - gk1))

    friction = np.sum(bs_low  * 0.07 * gk0 * (1 - gk0)) \
             + np.sum(bs_high * 0.07 * gk1 * (1 - gk1))
    

    return (pred_ret - friction)

def running_sharpe(returns):
    if len(returns) < 2:
        return np.nan
    return (np.mean(returns)-c) / (np.std(returns, ddof=1) + 1e-12)

def compute_var(returns):
    if len(returns) < 2:
        return np.nan
    return np.var(returns, ddof=1)

# --- backtest ---
# each row: (p1, p, gk0_list, gk1_list, realized_price, sigma)
# replace this with your actual data stream
def compute_max_drawdown(cumulative_returns):
    """
    Max drawdown as a FRACTION of the peak (e.g. -0.25 = a 25% decline
    from the running peak to the subsequent trough), not the raw
    dollar/level difference. Trough must occur after the peak.
    """
    cum = np.array(cumulative_returns, dtype=float)
    max_dd = 0.0
    peak = cum[0]
    for val in cum[1:]:
        if val > peak:
            peak = val          # new peak resets the reference
        elif peak > 0:
            dd = (val - peak) / peak
            if dd < max_dd:
                max_dd = dd
    return max_dd


c = 0.0001

returns      = []
bankroll   = 100
bankroll_history = [bankroll]   # track cumPnL over time for drawdown
benchmark_returns = []   # track market returns for alpha/beta calculation

def compute_alpha_beta(returns, benchmark_returns, c=0.0001):
    """
    OLS regression: strategy_return = alpha + beta * benchmark_return
    benchmark_returns must be aligned (same length) as returns.
    """
    if len(returns) < 2 or len(benchmark_returns) < 2:
        return np.nan, np.nan
    r  = np.array(returns)
    rb = np.array(benchmark_returns)
    cov = np.cov(r, rb)
    beta = cov[0,1] / (cov[1,1] + 1e-12)
    alpha = (np.mean(r) - c) - beta * (np.mean(rb) - c)
    return alpha, beta

def fetch_sp500_benchmark(all_dates):
    import yfinance as yf
    ts = pd.to_datetime(pd.Series(all_dates), utc=True)
    start = (ts.min() - pd.Timedelta(days=5)).date()
    end = (ts.max() + pd.Timedelta(days=5)).date()

    sp500 = yf.download('^GSPC', start=start.isoformat(), end=end.isoformat(), progress=False)
    if isinstance(sp500.columns, pd.MultiIndex):
        close_col = 'Close' if 'Close' in sp500.columns.get_level_values(0) else 'Adj Close'
        closes = sp500[close_col].iloc[:, 0]
    else:
        close_col = 'Close' if 'Close' in sp500.columns else 'Adj Close'
        closes = sp500[close_col]

    daily_ret = closes.pct_change().dropna()
    return {idx.date(): float(val) for idx, val in daily_ret.items()}


def gather_all_dates(paths):
    all_dates = set()
    for path in paths:
        try:
            dcol = pd.read_csv(path, usecols=['Date'])['Date']
        except (ValueError, KeyError):
            continue
        all_dates.update(pd.to_datetime(dcol, errors='coerce').dropna().dt.date.tolist())
    return sorted(all_dates)



def run_backtest_for_file(path, benchmark_lookup, c=0.0001, bankroll0=100.0):
    data = load_market_file(path, c=c)
    if not data:
        return None

    bankroll = bankroll0
    returns, bankroll_history, benchmark_returns = [], [bankroll], []
    gold_rets, pm_rets, kellys = [], [], []

    for row in data:
        best  = run_optimizer(row['p1'], row['p'], row['gk0_list'], row['gk1_list'],
                              c, row['sigma'], row['k_list'])
        w_direction     = best.x
        if np.any(np.isnan(w_direction)):
            print(f"    OPTIMIZER FAILED on date={row['date']}")
            print(f"    w={w_direction}")
            print(f"    p1={row['p1']}  p={row['p']}  sigma={row['sigma']}")
            print(f"    k_list={row['k_list']}")
            print(f"    gk0_list={row['gk0_list']}")
            print(f"    gk1_list={row['gk1_list']}")

        kelly = compute_kelly(w_direction, returns, row['p1'], row['p'], row['gk0_list'],
                              row['gk1_list'], c, row['sigma'], row['k_list'])
        w = w_direction * kelly

        ret      = compute_realized_return(w, row['p1'], row['p'], row['gk0_list'],
                                           row['gk1_list'], row['k_list'], row['realized_price'])
        gold_ret = compute_gold_return(w, row['p1'], row['p'], row['gk0_list'],
                                       row['gk1_list'], row['k_list'], row['realized_price'])
        pm_ret   = compute_pm_return(w, row['p1'], row['p'], row['gk0_list'],
                                     row['gk1_list'], row['k_list'], row['realized_price'])
        


        bankroll += ret * bankroll
        returns.append(ret)
        bankroll_history.append(bankroll)
        gold_rets.append(gold_ret)
        pm_rets.append(pm_ret)
        kellys.append(kelly)

        day_key = pd.to_datetime(row['date']).date()
        benchmark_returns.append(benchmark_lookup.get(day_key, 0.0))

    returns_arr = np.array(returns)
    print(f"n_days={len(returns_arr)}")
    print(f"min={returns_arr.min():.4f}  max={returns_arr.max():.4f}  mean={returns_arr.mean():.4f}")
    print(f"top 5 largest |returns|:")
    print(f"  pct_positive_days={np.mean(returns_arr > 0):.2%}")
    top5 = np.argsort(-np.abs(returns_arr))[:5]
    for i in top5:
        print(f"  day {i}: ret={returns_arr[i]:.4f}")

    alpha, beta = compute_alpha_beta(returns, benchmark_returns, c)
    return {
        'market':         os.path.splitext(os.path.basename(path))[0],
        'n_days':         len(data),
        'final_bankroll': bankroll,
        'total_return':   (bankroll - bankroll0) / bankroll0,
        'sharpe':         running_sharpe(returns),
        'max_drawdown':   compute_max_drawdown(bankroll_history),
        'alpha':          alpha,
        'beta':           beta,
        'avg_kelly':      np.mean(kellys),
    }

FOLDER = r"C:\Users\sunso\Downloads\directdata"   # <-- set this to your folder

paths = sorted(glob.glob(os.path.join(FOLDER, '*.csv')))
if not paths:
    raise FileNotFoundError(f'No CSV files found in {FOLDER}')

print(f"Found {len(paths)} market files. Fetching S&P 500 benchmark...")
all_dates = gather_all_dates(paths)
benchmark_lookup = fetch_sp500_benchmark(all_dates)

results = []
for i, path in enumerate(paths):
    print(f"  [{i+1}/{len(paths)}] {os.path.basename(path)}...", flush=True)
    res = run_backtest_for_file(path, benchmark_lookup, c=c, bankroll0=bankroll)
    if res:
        results.append(res)

summary = pd.DataFrame(results).sort_values('sharpe', ascending=False)
pd.set_option('display.width', 200)
pd.set_option('display.max_columns', None)
print("\n" + "=" * 100)
print(summary.to_string(index=False, float_format=lambda x: f'{x:.4f}'))
summary.to_csv('backtest_summary.csv', index=False)
