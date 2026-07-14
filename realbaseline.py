import matplotlib
from scipy.stats import norm
from scipy.optimize import minimize
import numpy as np
import matplotlib.pyplot as plt


def market_sharpe(fk0, gk0, gk1, c):
    """
    Returns (best_sharpe, side) for a single binary PM.
    side: 0 = low/no contract, 1 = high/yes contract
    """
    fk1 = 1 - fk0

    num_low  = (fk0 - gk0) / gk0 - 0.07 * gk0 * (1 - gk0) - c
    num_high = (fk1 - gk1) / gk1 - 0.07 * gk1 * (1 - gk1) - c

    var_low  = fk0 * (1 - fk0) / gk0**2
    var_high = fk1 * (1 - fk1) / gk1**2

    s_low  = num_low  / np.sqrt(var_low)  if var_low  > 0 else -np.inf
    s_high = num_high / np.sqrt(var_high) if var_high > 0 else -np.inf

    if s_low >= s_high:
        return s_low, 0
    else:
        return s_high, 1


    







lam = .01
bankroll = 100
c = 0.0001


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

import glob
import os


def load_market_csv(path, c=0.0001):
    """
    Loads a single market's candlesticks CSV and returns:
        { date_str -> {gk0, gk1, c} }

    Handles two export formats seen in this dataset:
      - 'yes_ask.close', 'yes_bid.close'
      - 'yes_ask.close_dollars', 'yes_bid.close_dollars'
    Both are renamed to the first (canonical) form below, then dots are
    replaced with underscores so itertuples() can expose them as real
    attribute names (dotted names aren't valid Python identifiers, so
    itertuples() silently drops them to positional fields otherwise).
    """
    df = pd.read_csv(path)

    rename_map = {
        'yes_ask.close_dollars': 'yes_ask.close',
        'yes_bid.close_dollars': 'yes_bid.close',
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    df = df.rename(columns=lambda c: c.replace('.', '_'))

    df = df.dropna(subset=['yes_ask_close', 'yes_bid_close', 'converted_time'])

    out = {}
    for row in df.itertuples(index=False):
        date = row.converted_time
        gk1 = float(row.yes_ask_close)
        gk0 = 1.0 - float(row.yes_bid_close)

        if not (0.0 < gk1 < 1.0) or not (0.0 < gk0 < 1.0):
            continue

        out[date] = {'gk0': gk0, 'gk1': gk1, 'c': c}
    return out

def mark_to_market_value(pos, snapshot):
    """
    Current dollar value of an open position, marked at today's price
    for whichever side it's actually on. Returns 0.0 if flat or if
    today's snapshot has no price for this market (frozen/missing day).
    """
    current = pos['position'] or 0.0
    if current == 0.0 or pos['side'] is None:
        return 0.0
    inputs = snapshot.get(pos['pm'])
    if inputs is None:
        # no price today -- can't mark it, fall back to what was staked
        return abs(current)
    price_now = inputs['gk0'] if pos['side'] == 0 else inputs['gk1']
    units = abs(current) / pos['entry_price']
    return units * price_now

def fetch_sp500_benchmark(all_dates):
    """
    Fetches daily S&P 500 (^GSPC) returns from Yahoo Finance to use as
    the benchmark for alpha/beta, since SP500_Return is unpopulated in
    the CSVs.
    Returns {datetime.date -> daily pct return}.
    """
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

def print_holdings(positions, t=None):
    label = f"t={t}  " if t is not None else ""
    print(f"{label}--- Current Holdings ---")
    any_open = False
    for pos in positions:
        stake = pos['position'] or 0.0
        if stake != 0.0:
            any_open = True
            side_label = 'NO' if pos['side'] == 0 else 'YES'
            print(f"  {pos['pm']:>20}  side={side_label}  stake=${stake:>8.2f}  entry={pos['entry_price']:.4f}")
    #if not any_open:
        #print("  (flat — no open positions)")


def load_all_markets(folder, c=c):
    """
    Recursively finds every *_candlesticks.csv under `folder` (skips
    market_historical/market_live metadata files, which carry no price
    data, and macOS __MACOSX/._ junk files from zip extraction).
    """
    paths = sorted(glob.glob(os.path.join(folder, '**', '*_candlesticks.csv'), recursive=True))
    paths = [p for p in paths
             if '__MACOSX' not in p and not os.path.basename(p).startswith('._')]
    if not paths:
        raise FileNotFoundError(f'No candlestick CSV files found in {folder}')

    market_ids = []
    per_market = {}
    for path in paths:
        market_id = os.path.splitext(os.path.relpath(path, folder))[0].replace(os.sep, '_')
        market_ids.append(market_id)
        per_market[market_id] = load_market_csv(path, c=c)

    all_dates = sorted({d for m in per_market.values() for d in m.keys()})

    panel = {date: {} for date in all_dates}
    for market_id, by_date in per_market.items():
        for date, vals in by_date.items():
            panel[date][market_id] = vals

    return market_ids, panel, all_dates

market_ids, panel, all_dates = load_all_markets(r"C:\Users\sunso\Downloads\data_tech_all\data_tech_all", c)
benchmark_lookup = fetch_sp500_benchmark(all_dates)   # <-- new line
#best = None
#best = run_optimizer(p1, p, gk0_list, gk1_list, c, sigma, pm_list)

#w = best.x
#a_net = w[0] - w[1]
#print(f"a_net={a_net:.4f}  ({'long' if a_net >= 0 else 'short'} gold)")
#for i, k in enumerate(pm_list):
#    bl, bh = w[2+i], w[2+n+i]
#    if bl > 0.001 or bh > 0.001:
#        print(f"  k={k}  b_low={bl:.4f}  b_high={bh:.4f}")
#pure_sharpe = -objective_multi(w, p1, p, gk0_list, gk1_list, c, sigma, pm_list, 0)


#print(f"Sharpe:          {pure_sharpe:.4f}")

#print(f"Sum of weights:  {np.sum(w):.4f}")
#all_b     = w[2:n+2] + w[n+2:]   # sum low+high per strike
#b_weights = all_b / (np.sum(all_b) + 1e-9)
#entropy   = -np.sum(b_weights * np.log(b_weights + 1e-9))
#print(f"Entropy:         {entropy:.4f}")





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



returns      = []
bankroll   = 100
bankroll_history = [bankroll]   # track cumPnL over time for drawdown
benchmark_returns = []   # track market returns for alpha/beta calculation
return_dates = []          


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

def adjust_position(pos, delta_dollars, price, bankroll):
    """
    delta_dollars: signed change in stake (positive = adding/opening, negative = reducing).
    price: the contract price at which this adjustment trades.
    side: 0 (low) or 1 (high) — the side being adjusted.
    Returns realized P&L from any portion that was a reduction (closing dollar_pnl), 0 if pure add.
    """
    current = pos['position'] or 0.0
    realized_pnl = 0.0

    if current == 0.0:
        # opening fresh
        pos['position'] = delta_dollars
        pos['entry_price'] = price
        bankroll -= abs(delta_dollars)   
        pos['side'] = 1 if delta_dollars > 0 else 0   # add this
        return bankroll, 0.0

    same_direction = (current > 0 and delta_dollars > 0) or (current < 0 and delta_dollars < 0)

    if same_direction:
        # adding to existing position — update weighted average cost basis
        total = abs(current) + abs(delta_dollars)
        pos['entry_price'] = (pos['entry_price'] * abs(current) + price * abs(delta_dollars)) / total
        pos['position'] = current + delta_dollars
        bankroll-= abs(delta_dollars)  # reduce bankroll by the amount invested
    else:
        # reducing (or flipping) — realize P&L on the portion being closed
        closing_amount = min(abs(delta_dollars), abs(current))
        pnl_fraction = (price - pos['entry_price']) / pos['entry_price']
        realized_pnl = pnl_fraction * closing_amount
        bankroll+= realized_pnl + closing_amount # add realized P&L to bankroll

        pos['position'] = current + delta_dollars
        if pos['position'] == 0.0:
            pos['entry_price'] = None
            pos['side'] = 1 if current > 0 else 0
        elif (pos['position'] > 0) != (current > 0):
            # flipped through zero to the other side — new cost basis at current price
            pos['entry_price'] = price
            pos['side'] = 1 if pos['position'] > 0 else 0
            bankroll-= abs(pos['position'])  # reduce bankroll by the amount invested
    if pos['position'] == 0.0:
        pos['entry_price'] = None
        pos['side'] = None

    return bankroll, realized_pnl


print(f"{'t':>4}  {'equity':>8}  {'bankroll':>8}  {'return' :>8}  {'maxDD':>8}  {'alpha':>8}, {'beta':>8}")
print("-" * 60)

positions = []
for mid in market_ids:
    positions.append({
        'pm': mid,
        'position': None,     # None, 'low', or 'high'
        'returns': [],        # this PM's own realized-return history, for its own var/Kelly
        'entry_price': None,   # price at which the position was entered
        'side': None
    })
entry_threshold = 0.2
exit_threshold = 0
MIN_TRADE = 3  # minimum $ amount to trade (to avoid tiny trades)
total_equity = bankroll

last_date_of = {}
for date in all_dates:
    for mid in panel[date]:
        last_date_of[mid] = date   # final date this market trades

# inside the daily loop, after trading on `date`:
    

for t, date in enumerate(all_dates):
    snapshot = panel[date] 
    starting_equity = total_equity
    for pos in positions:
        pm = pos['pm']
        inputs = snapshot.get(pm)
        if inputs is None:
            continue   # this market has no data for this date -- skip it today
        if pos['side'] is not None and pos.get('last_price'):
            price_now = inputs['gk0'] if pos['side'] == 0 else inputs['gk1']
            pos['returns'].append((price_now - pos['last_price']) / pos['last_price'])


        if inputs['gk0'] <= 0.9 and inputs['gk0'] >0.5 and (pos['position'] == None or pos['position'] == 0.0):
            bankroll, _ = adjust_position(pos, -bankroll/5, inputs['gk0'], bankroll)  
        elif inputs['gk1'] <= 0.9 and inputs['gk1'] >0.5 and (pos['position'] == None or pos['position'] == 0.0):
            bankroll, _ = adjust_position(pos, bankroll/5, inputs['gk1'], bankroll)  


        
        if last_date_of.get(pos['pm']) == date and (pos['position'] or 0) != 0:
            vals = panel[date][pos['pm']]
            settle_price = vals['gk0'] if pos['side'] == 0 else vals['gk1']
            bankroll, _ = adjust_position(pos, -pos['position'], settle_price, bankroll)
            continue
        

        # benchmark: the day's S&P 500 return (same across markets on a given date)
    day_key = pd.to_datetime(date, utc=True).date()
    day_bench = benchmark_lookup.get(day_key, 0.0)
    benchmark_returns.append(day_bench)
    return_dates.append(day_key)      # <-- new



    total_equity = bankroll + sum(mark_to_market_value(pos, snapshot) for pos in positions)
    returns.append((total_equity - starting_equity)/starting_equity)
    bankroll_history.append(total_equity)
    max_dd = compute_max_drawdown(bankroll_history)
    final_sharpe = running_sharpe(returns)
    alpha, beta = compute_alpha_beta(returns, benchmark_returns, c)


    print(f"{t:>4}  {total_equity:>8.4f}  {bankroll:>8.4f}  {returns[-1]:>8.4f}  {max_dd:.4f}  {alpha:>7.4f}  {beta:7.4f}")
    #if t%10 == 0:
    print_holdings(positions, t)
import matplotlib.pyplot as plt

daily_returns_map = {}
daily_bench_map = {}
for day, r, rb in zip(return_dates, returns, benchmark_returns):
    daily_returns_map.setdefault(day, []).append(r)
    daily_bench_map[day] = rb  # same value repeated within a day, harmless to overwrite

sorted_days = sorted(daily_returns_map)
daily_returns = [np.prod([1 + r for r in daily_returns_map[d]]) - 1 for d in sorted_days]
daily_bench = [daily_bench_map[d] for d in sorted_days]

final_max_dd = compute_max_drawdown(bankroll_history)
final_alpha, final_beta = compute_alpha_beta(daily_returns, daily_bench, c)   # <-- now daily, not raw hourly
print(f"\nFinal Max Drawdown: {final_max_dd:.4f}")
print(f"Final Alpha (daily): {final_alpha:.4f}   Final Beta (daily): {final_beta:.4f}")
print(f"Final Sharpe (hourly): {final_sharpe:.4f}")

running_alphas, running_betas = [], []
for i in range(len(daily_returns)):
    if i < 1:
        running_alphas.append(np.nan); running_betas.append(np.nan)
    else:
        a, b = compute_alpha_beta(daily_returns[:i+1], daily_bench[:i+1], c)
        running_alphas.append(a); running_betas.append(b)
running_sharpes = []
for i in range(len(returns)):
    if i < 1:
        running_sharpes.append(np.nan)
    else:
        running_sharpes.append((np.mean(returns[:i+1]) - c) / (np.std(returns[:i+1], ddof=1) + 1e-12))

print(f"Final Alpha: {running_alphas[-1]:.6f}")
print(f"Final Beta:  {running_betas[-1]:.4f}")
print(f"Final Sharpe (hourly): {final_sharpe:.4f}")


import matplotlib.gridspec as gridspec

fig = plt.figure(figsize=(14, 22))
gs = gridspec.GridSpec(4, 1, figure=fig, hspace=0.6)   # hspace = vertical gap between rows

ax1 = fig.add_subplot(gs[0])
ax3 = fig.add_subplot(gs[2])

fig.suptitle('Baseline Performance, Naive High-Probability Buy-and-Hold Strategy',
             fontsize=18, fontweight='bold', y=0.995)

ax1.plot(bankroll_history, color='steelblue', linewidth=1.5)
ax1.axhline(0, color='black', linewidth=0.8, linestyle='--')
ax1.set_ylabel('Equity'); ax1.set_xlabel('Hour (t)')
ax1.set_title('Equity Over Time')
ax1.grid(True, alpha=0.3)

ax3.plot(running_alphas, color='seagreen', linewidth=1.5)
ax3.axhline(0, color='black', linewidth=0.8, linestyle='--')
ax3.set_ylabel('Running Alpha'); ax3.set_xlabel('Day')
ax3.set_title('Running Alpha vs S&P 500')
ax3.grid(True, alpha=0.3)


fig.subplots_adjust(top=0.94, bottom=0.06, left=0.08, right=0.96, hspace=0.6)
plt.show()