import numpy as np
import pandas as pd
import glob
import os
import matplotlib.pyplot as plt


# =====================================================================
# BASELINE STRATEGY
# ---------------------------------------------------------------------
# Entry signal:  raw expected-value mispricing (numerator of your
#                Sharpe calc), with NO variance normalization.
# Sizing:        flat dollar stake, fixed as a fraction of STARTING
#                bankroll (not current equity -- keeps it path-independent).
# Holding:       once entered, hold until the market settles. No
#                dead-zone resizing, no early exit on signal decay.
#
# Purpose: isolate whether your variance-adjustment (Sharpe) + Kelly
# sizing actually add value over "bet a flat amount whenever the model
# says there's edge, and hold." If this baseline underperforms your
# real strategy, that's evidence the risk-adjustment/sizing machinery
# is doing real work -- not just that having *a* model beats nothing.
#
# FIXES vs the previous version (see notes at each site):
#   1. adjust_position's blended entry_price was a dollar-weighted
#      ARITHMETIC mean of price, not a true cost basis. True cost
#      basis is total_dollars / total_contracts, which is a
#      dollar-weighted HARMONIC mean of price. By AM-HM inequality the
#      old formula always overstated entry_price, which understated
#      the implied contract count ("units") used everywhere downstream
#      (mark-to-market, settlement P&L). Fixed to track contracts
#      explicitly.
#   2. There was no cap on how large a single position could grow.
#      Nothing stopped the same_direction branch from adding $5/day to
#      one market indefinitely. Combined with entry_price trending
#      toward zero on cheap tail contracts, "units = dollars/price"
#      balloons, so a small settlement move or price tick gets
#      multiplied by an enormous implied contract count -- this is
#      the actual explosion mechanism. Fixed with a hard cap of 20% of
#      STARTING_BANKROLL total exposure per market; entries beyond the
#      cap are sized down to whatever room remains, or skipped if none.
# =====================================================================


def raw_edge(fk0, gk0, gk1, c):
    """
    Returns (best_edge, side) for a single binary PM.
    Same numerator as market_sharpe(), but NOT divided by sqrt(variance).
    side: 0 = low/no contract, 1 = high/yes contract
    """
    fk1 = 1 - fk0

    edge_low  = (fk0 - gk0) / gk0 - 0.07 * gk0 * (1 - gk0) - c
    edge_high = (fk1 - gk1) / gk1 - 0.07 * gk1 * (1 - gk1) - c

    if edge_low >= edge_high:
        return edge_low, 0
    else:
        return edge_high, 1


c = 0.0001


# --- data loading (unchanged from main strategy) ---

def load_market_csv(path, c=0.0001):
    """
    Loads a single market's CSV (one row per day for that market) and
    returns a dict: { candle_date (str) -> {fk0, gk0, gk1, c, bench} }.

    Handles two export formats seen in the data folder:
      - 'yes_ask.close', 'yes_bid.close', 'open_interest', 'volume'
      - 'yes_ask.close_dollars', 'yes_bid.close_dollars', 'open_interest_fp', 'volume_fp'
    Both are renamed to the first (canonical) form below.
    """
    df = pd.read_csv(path)

    rename_map = {
        'yes_ask.close_dollars': 'yes_ask.close',
        'yes_bid.close_dollars': 'yes_bid.close',
        'open_interest_fp': 'open_interest',
        'volume_fp': 'volume',
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    required = ['fv_consensus', 'yes_ask.close', 'yes_bid.close', 'signal_on']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"{path}: still missing {missing} after rename. Columns: {df.columns.tolist()}")

    df = df.dropna(subset=required)

    out = {}
    for _, row in df.iterrows():
        date = row['datetime']
        fk1 = float(row['fv_consensus'])
        gk1 = float(row['yes_ask.close'])
        gk0 = 1.0 - float(row['yes_bid.close'])
        signal = row['signal_on']

        if not (0.0 < gk1 < 1.0) or not (0.0 < gk0 < 1.0):
            continue
        close = pd.to_datetime(row['close_time'], utc=True)
        cdate = pd.to_datetime(date, utc=True)
        days_left = max((close - cdate).total_seconds() / 86400.0, 1.0)

        out[date] = {
            'fk0': 1.0 - fk1,
            'gk0': gk0,
            'gk1': gk1,
            'c': c,
            'bench': float(row['SP500_Return']) if not pd.isna(row.get('SP500_Return', np.nan)) else 0.0,
            'horizon': days_left,
            'close_time': close,
            'signal_on': signal,          # <-- was missing entirely

        }
    return out


def load_all_markets(folder, c=c):
    paths = sorted(glob.glob(os.path.join(folder, '*.csv')))
    if not paths:
        raise FileNotFoundError(f'No CSV files found in {folder}')

    market_ids = []
    per_market = {}
    for path in paths:
        market_id = os.path.splitext(os.path.basename(path))[0]
        market_ids.append(market_id)
        per_market[market_id] = load_market_csv(path, c=c)

    all_dates = sorted({d for m in per_market.values() for d in m.keys()})

    panel = {date: {} for date in all_dates}
    for market_id, by_date in per_market.items():
        for date, vals in by_date.items():
            panel[date][market_id] = vals

    return market_ids, panel, all_dates


def mark_to_market_value(pos, snapshot):
    current = pos['position'] or 0.0
    if current == 0.0 or pos['side'] is None:
        return 0.0
    inputs = snapshot.get(pos['pm'])
    if inputs is None:
        return abs(current)
    price_now = inputs['gk0'] if pos['side'] == 0 else inputs['gk1']
    units = abs(current) / pos['entry_price']
    return units * price_now


def print_holdings(positions, t=None):
    label = f"t={t}  " if t is not None else ""
    print(f"{label}--- Current Holdings ---")
    for pos in positions:
        stake = pos['position'] or 0.0
        if stake != 0.0:
            side_label = 'NO' if pos['side'] == 0 else 'YES'
            print(f"  {pos['pm']:>20}  side={side_label}  stake=${stake:>8.2f}  entry={pos['entry_price']:.4f}")


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

def fetch_sp500_benchmark(all_dates):
    """
    Fetches daily S&P 500 (^GSPC) returns from Yahoo Finance to use as
    the benchmark for alpha/beta, since SP500_Return is unpopulated.
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


def running_sharpe(returns):
    if len(returns) < 2:
        return np.nan
    return (np.mean(returns) - c) / (np.std(returns, ddof=1) + 1e-12)


def compute_alpha_beta(returns, benchmark_returns, c=0.0001):
    if len(returns) < 2 or len(benchmark_returns) < 2:
        return np.nan, np.nan
    r = np.array(returns)
    rb = np.array(benchmark_returns)
    cov = np.cov(r, rb)
    beta = cov[0, 1] / (cov[1, 1] + 1e-12)
    alpha = (np.mean(r) - c) - beta * (np.mean(rb) - c)
    return alpha, beta


def adjust_position(pos, delta_dollars, price, bankroll):
    """
    Same interface/accounting shape as before, but the blended
    entry_price on same-direction adds is now a true cost basis
    (total dollars / total contracts) instead of a dollar-weighted
    arithmetic mean of price. These are NOT the same thing -- the old
    formula always overstated entry_price (AM >= HM), which understated
    the implied contract count used by mark_to_market_value and the
    realized-P&L calc below.
    """
    current = pos['position'] or 0.0
    realized_pnl = 0.0

    contracts_traded = abs(delta_dollars) / price          # NEW
    fee_dollars = 0.07 * price * (1.0 - price) * contracts_traded   # NEW
    bankroll -= fee_dollars                                  # NEW


    if current == 0.0:
        pos['position'] = delta_dollars
        pos['entry_price'] = price
        bankroll -= abs(delta_dollars)
        pos['side'] = 1 if delta_dollars > 0 else 0
        return bankroll, 0.0

    same_direction = (current > 0 and delta_dollars > 0) or (current < 0 and delta_dollars < 0)

    if same_direction:
        # FIX: true cost basis = total dollars spent / total contracts
        # owned, not a plain average of the two prices.
        old_contracts = abs(current) / pos['entry_price']
        new_contracts = abs(delta_dollars) / price
        total_contracts = old_contracts + new_contracts
        total_dollars = abs(current) + abs(delta_dollars)
        pos['entry_price'] = total_dollars / total_contracts
        pos['position'] = current + delta_dollars
        bankroll -= abs(delta_dollars)
    else:
        closing_amount = min(abs(delta_dollars), abs(current))
        pnl_fraction = (price - pos['entry_price']) / pos['entry_price']
        realized_pnl = pnl_fraction * closing_amount
        bankroll += realized_pnl + closing_amount

        pos['position'] = current + delta_dollars
        if pos['position'] == 0.0:
            pos['entry_price'] = None
            pos['side'] = None
        elif (pos['position'] > 0) != (current > 0):
            pos['entry_price'] = price
            pos['side'] = 1 if pos['position'] > 0 else 0
            bankroll -= abs(pos['position'])

    if pos['position'] == 0.0:
        pos['entry_price'] = None
        pos['side'] = None

    return bankroll, realized_pnl


# =====================================================================
# BASELINE PARAMETERS
# =====================================================================
# EDGE_ENTRY_THRESHOLD: raw edge required to open a position. This is
# NOT the same scale as your Sharpe entry_threshold -- edge is a raw
# EV number (e.g. 0.03 = model thinks the true price should be ~3 cents
# better than the market price), not a variance-normalized statistic.
# Tune this so trade *frequency* roughly matches your real strategy --
# otherwise you're comparing "many small flat bets" against "few
# well-timed Kelly bets," which muddies the comparison.
EDGE_ENTRY_THRESHOLD = 0.05

# Flat stake per trade, as a fraction of the STARTING bankroll (not
# current equity -- keeps sizing path-independent, unlike Kelly which
# resizes off current bankroll).
FLAT_STAKE_FRACTION = 0.05

# FIX: hard cap on total dollar exposure in any ONE market, as a
# fraction of starting bankroll. Without this, the same_direction
# branch in adjust_position lets a position grow by FLAT_STAKE every
# single day the edge signal stays above threshold. On a cheap tail
# contract (entry_price near 0), that means the implied contract count
# ("units" in mark_to_market_value) balloons, so a routine settlement
# or price move gets multiplied by an oversized position -- that's the
# explosion. Capping total per-market exposure bounds that directly.
MAX_POSITION_FRACTION = 0.20
EDGE_SCALE_CAP = 3.0


MIN_TRADE = 3  # same floor as main strategy, avoid dust trades

# =====================================================================
market_ids, panel, all_dates = load_all_markets(r"C:\Users\sunso\Downloads\v4.0\v4.0", c)
benchmark_lookup = fetch_sp500_benchmark(all_dates)   # <-- new line

STARTING_BANKROLL = 100
bankroll = STARTING_BANKROLL
flat_stake = FLAT_STAKE_FRACTION * STARTING_BANKROLL
# NOTE: MAX_POSITION is now recomputed every day inside the loop below,
# off the CURRENT bankroll, so the cap compounds along with your equity
# instead of staying pinned to STARTING_BANKROLL.

returns = []
bankroll_history = [bankroll]
benchmark_returns = []
return_dates = []
positions = []
for mid in market_ids:
    positions.append({
        'pm': mid,
        'position': None,
        'entry_price': None,
        'side': None,
    })

total_equity = bankroll

last_date_of = {}
for date in all_dates:
    for mid in panel[date]:
        last_date_of[mid] = date

print(f"{'t':>4}  {'equity':>8}  {'bankroll':>8}  {'return':>8}  {'sharpe':>8}  {'maxDD':>8}  {'alpha':>8}  {'beta':>8}")
print("-" * 60)

for t, date in enumerate(all_dates):
    snapshot = panel[date]
    starting_equity = total_equity
    # cap recomputed fresh each day off current (compounding) bankroll --
    # this is CASH on hand, not total_equity, so it does NOT include the
    # value of currently-open positions. If bankroll is temporarily low
    # because a lot of cash is locked in open positions, the cap shrinks
    # even though total wealth hasn't dropped. Flagging in case that's
    # not what you want -- swap `bankroll` for `total_equity` below if
    # you'd rather the cap track total wealth instead of free cash.
    max_position = MAX_POSITION_FRACTION * bankroll

    for pos in positions:
        pm = pos['pm']
        inputs = snapshot.get(pm)
        if inputs is None:
            continue
        if inputs.get('signal_on') is False:
            continue


        current = pos['position'] or 0.0

        # settle on the market's final day if we're holding anything
        if last_date_of.get(pm) == date and current != 0:
            settle_price = inputs['gk0'] if pos['side'] == 0 else inputs['gk1']
            bankroll, _ = adjust_position(pos, -current, settle_price, bankroll)
            continue

        # flat -- check for entry, otherwise do nothing
        edge, side = raw_edge(inputs['fk0'], inputs['gk0'], inputs['gk1'], inputs['c'])
        if side != pos['side'] and current != 0:
            bankroll = adjust_position(pos, -current, inputs['gk0'] if pos['side'] == 0 else inputs['gk1'], bankroll)[0]
            current = 0.0  # position was just fully closed above

        if edge > EDGE_ENTRY_THRESHOLD:
            price = inputs['gk0'] if side == 0 else inputs['gk1']
            room = max_position - abs(current)
            # proportional sizing: how many multiples of the entry
            # threshold is this edge? 1x threshold -> 1x flat_stake,
            # 2x threshold -> 2x flat_stake, capped at EDGE_SCALE_CAP
            # so one extreme cheap-contract reading can't run away.
            edge_multiple = min(edge / EDGE_ENTRY_THRESHOLD, EDGE_SCALE_CAP)
            desired_mag = flat_stake * edge_multiple
            stake_mag = min(desired_mag, max(room, 0.0))

            stake = stake_mag * (1 if side == 1 else -1)

            if abs(stake) > MIN_TRADE and bankroll >= abs(stake):
                bankroll, _ = adjust_position(pos, stake, price, bankroll)

    day_key = pd.to_datetime(date, utc=True).date()
    day_bench = benchmark_lookup.get(day_key, 0.0)
    benchmark_returns.append(day_bench)
    return_dates.append(day_key)

    total_equity = bankroll + sum(mark_to_market_value(pos, snapshot) for pos in positions)
    returns.append((total_equity - starting_equity) / starting_equity)
    bankroll_history.append(total_equity)
    max_dd = compute_max_drawdown(bankroll_history)
    final_sharpe = running_sharpe(returns)
    alpha, beta = compute_alpha_beta(returns, benchmark_returns, c)

    print(f"{t:>4}  {total_equity:>8.4f}  {bankroll:>8.4f}  {returns[-1]:>8.4f}  {final_sharpe:>7.4f}  {max_dd:.4f}  {alpha:>7.4f}  {beta:7.4f}")
    print_holdings(positions, t)

daily_returns_map = {}
daily_bench_map = {}
for day, r, rb in zip(return_dates, returns, benchmark_returns):
    daily_returns_map.setdefault(day, []).append(r)
    daily_bench_map[day] = rb  # same value repeated all hours in a day, harmless to overwrite

sorted_days = sorted(daily_returns_map)
daily_returns = [np.prod([1 + r for r in daily_returns_map[d]]) - 1 for d in sorted_days]
daily_bench = [daily_bench_map[d] for d in sorted_days]

final_max_dd = compute_max_drawdown(bankroll_history)
print(f"\nFinal Max Drawdown: {final_max_dd:.4f}")


running_alphas, running_betas = [], []
for i in range(len(daily_returns)):
    if i < 1:
        running_alphas.append(np.nan); running_betas.append(np.nan)
    else:
        a, b = compute_alpha_beta(daily_returns[:i+1], daily_bench[:i+1], c)
        running_alphas.append(a); running_betas.append(b)

running_sharpes_daily = []
for i in range(len(daily_returns)):
    if i < 1:
        running_sharpes_daily.append(np.nan)
    else:
        running_sharpes_daily.append(
            (np.mean(daily_returns[:i+1]) - c) / (np.std(daily_returns[:i+1], ddof=1) + 1e-12))


print(f"Final Alpha: {running_alphas[-1]:.6f}")
print(f"Final Beta:  {running_betas[-1]:.4f}")
print(f"Final Sharpe (daily): {running_sharpes_daily[-1]:.4f}")

# --- plot ---
running_sharpes = []
for i in range(len(returns)):
    if i < 1:
        running_sharpes.append(np.nan)
    else:
        running_sharpes.append((np.mean(returns[:i + 1]) - c) / (np.std(returns[:i + 1], ddof=1) + 1e-12))


import matplotlib.gridspec as gridspec

fig = plt.figure(figsize=(14, 22))
gs = gridspec.GridSpec(4, 1, figure=fig, hspace=0.6)   # hspace = vertical gap between rows

ax1 = fig.add_subplot(gs[0])
ax2 = fig.add_subplot(gs[1])
ax3 = fig.add_subplot(gs[2])
ax4 = fig.add_subplot(gs[3])

fig.suptitle('Multi-Market Allocation from Dynamic Granger Causality with Raw Edge',
             fontsize=18, fontweight='bold', y=0.995)

ax1.plot(bankroll_history, color='steelblue', linewidth=1.5)
ax1.axhline(0, color='black', linewidth=0.8, linestyle='--')
ax1.set_ylabel('Equity'); ax1.set_xlabel('Hour (t)')
ax1.set_title('Equity Over Time')
ax1.grid(True, alpha=0.3)

ax2.plot(running_sharpes, color='darkorange', linewidth=1.5)
ax2.axhline(0, color='black', linewidth=0.8, linestyle='--')
ax2.set_ylabel('Running Sharpe'); ax2.set_xlabel('Hour (t)')
ax2.set_title('Running Sharpe Ratio Over Time')
ax2.grid(True, alpha=0.3)

ax3.plot(running_alphas, color='seagreen', linewidth=1.5)
ax3.axhline(0, color='black', linewidth=0.8, linestyle='--')
ax3.set_ylabel('Running Alpha'); ax3.set_xlabel('Day')
ax3.set_title('Running Alpha vs S&P 500')
ax3.grid(True, alpha=0.3)

ax4.plot(running_betas, color='indianred', linewidth=1.5)
ax4.axhline(0, color='black', linewidth=0.8, linestyle='--')
ax4.set_ylabel('Running Beta'); ax4.set_xlabel('Day')
ax4.set_title('Running Beta vs S&P 500')
ax4.grid(True, alpha=0.3)

fig.subplots_adjust(top=0.94, bottom=0.06, left=0.08, right=0.96, hspace=0.6)
plt.show()