import alpaca_trade_api as tradeapi
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
import schedule
import time
import pytz
import warnings
warnings.filterwarnings('ignore')

from shared_notify import notify
from trade_logger import log_trade

API_KEY    = "PKJGI26G4CG7HTKB53NLNS24VV"
API_SECRET = "CHYdqqpRe6QUALQx654p7XQWmGGzr7r96JRVvBDc7hvj"
BASE_URL   = "https://paper-api.alpaca.markets"

api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

TICKER         = "QQQ"
RISK_PER_TRADE = 0.01
ET             = pytz.timezone('America/New_York')
BOT            = "ORB"

or_high        = None
or_low         = None
or_avg_volume  = None
or_established = False
trade_taken    = False

def safe_float(val):
    if hasattr(val, 'item'):
        return float(val.item())
    return float(val)

def get_intraday(ticker="QQQ"):
    df = yf.download(ticker, period="1d", interval="1m", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
    else:
        df.index = df.index.tz_convert('America/New_York')
    return df

def get_spy_bias():
    df       = get_intraday("SPY")
    or_slice = df.between_time("9:30", "9:44")
    if or_slice.empty:
        return None
    mid   = (safe_float(or_slice['High'].max()) + safe_float(or_slice['Low'].min())) / 2
    price = safe_float(df['Close'].iloc[-1])
    return "bull" if price > mid else "bear"

def build_opening_range():
    global or_high, or_low, or_avg_volume, or_established
    print(f"\n{datetime.now(ET).strftime('%H:%M:%S')} — Building OR")
    df       = get_intraday()
    or_slice = df.between_time("9:30", "9:44")
    if len(or_slice) < 10:
        print("Not enough candles yet")
        return
    or_high        = safe_float(or_slice['High'].max())
    or_low         = safe_float(or_slice['Low'].min())
    or_avg_volume  = safe_float(or_slice['Volume'].mean())
    or_established = True
    print(f"OR | High: {or_high:.2f} | Low: {or_low:.2f} | Range: {or_high - or_low:.2f} | AvgVol: {or_avg_volume:.0f}")

def check_entry():
    global trade_taken
    if not or_established or trade_taken:
        return
    try:
        if any(p.symbol == TICKER for p in api.list_positions()):
            check_exit()
            return
        df      = get_intraday()
        latest  = df.iloc[-1]
        price   = safe_float(latest['Close'])
        volume  = safe_float(latest['Volume'])
        bias    = get_spy_bias()
        height  = or_high - or_low
        vol_ok  = volume >= or_avg_volume * 1.5
        print(f"{datetime.now(ET).strftime('%H:%M:%S')} | Price: {price:.2f} | OR: {or_low:.2f}-{or_high:.2f} | Vol: {'OK' if vol_ok else 'LOW'} | SPY: {bias}")
        account     = api.get_account()
        risk_amount = float(account.portfolio_value) * RISK_PER_TRADE
        if price > or_high and bias == "bull" and vol_ok:
            stop, target = or_low, or_high + (2 * height)
            stop_dist    = price - stop
            shares       = int(risk_amount / stop_dist) if stop_dist > 0 else 0
            if shares > 0:
                api.submit_order(symbol=TICKER, qty=shares, side="buy", type="market", time_in_force="day")
                trade_taken = True
                log_trade(BOT, TICKER, "buy", price, shares, "ORB_LONG", stop, target)
                msg = f"BUY QQQ | {price:.2f} x{shares} | Stop: {stop:.2f} | Target: {target:.2f}"
                print(f"*** {msg} ***"); notify(msg, BOT)
        elif price < or_low and bias == "bear" and vol_ok:
            stop, target = or_high, or_low - (2 * height)
            stop_dist    = stop - price
            shares       = int(risk_amount / stop_dist) if stop_dist > 0 else 0
            if shares > 0:
                api.submit_order(symbol=TICKER, qty=shares, side="sell", type="market", time_in_force="day")
                trade_taken = True
                log_trade(BOT, TICKER, "sell", price, shares, "ORB_SHORT", stop, target)
                msg = f"SHORT QQQ | {price:.2f} x{shares} | Stop: {stop:.2f} | Target: {target:.2f}"
                print(f"*** {msg} ***"); notify(msg, BOT)
        else:
            print("No breakout")
    except Exception as e:
        print(f"Entry error: {e}"); notify(f"ORB ENTRY ERROR: {e}", BOT)

def check_exit():
    if not or_established:
        return
    try:
        pos    = api.get_position(TICKER)
        price  = float(pos.current_price)
        entry  = float(pos.avg_entry_price)
        side   = pos.side
        height = or_high - or_low
        if side == "long":
            stop, target = or_low, or_high + (2 * height)
            pnl          = price - entry
            if price >= target:
                api.submit_order(symbol=TICKER, qty=pos.qty, side="sell", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "sell", price, pos.qty, "ORB_TP", pnl=pnl)
                msg = f"TAKE PROFIT | {price:.2f} | P&L: +{pnl:.2f}"; print(msg); notify(msg, BOT)
            elif price <= stop:
                api.submit_order(symbol=TICKER, qty=pos.qty, side="sell", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "sell", price, pos.qty, "ORB_SL", pnl=pnl)
                msg = f"STOP LOSS | {price:.2f} | P&L: {pnl:.2f}"; print(msg); notify(msg, BOT)
            else:
                print(f"HOLDING LONG | {price:.2f} | Stop: {stop:.2f} | Target: {target:.2f}")
        elif side == "short":
            stop, target = or_high, or_low - (2 * height)
            pnl          = entry - price
            if price <= target:
                api.submit_order(symbol=TICKER, qty=pos.qty, side="buy", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "buy", price, pos.qty, "ORB_TP", pnl=pnl)
                msg = f"SHORT TP | {price:.2f} | P&L: +{pnl:.2f}"; print(msg); notify(msg, BOT)
            elif price >= stop:
                api.submit_order(symbol=TICKER, qty=pos.qty, side="buy", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "buy", price, pos.qty, "ORB_SL", pnl=pnl)
                msg = f"SHORT SL | {price:.2f} | P&L: {pnl:.2f}"; print(msg); notify(msg, BOT)
            else:
                print(f"HOLDING SHORT | {price:.2f} | Stop: {stop:.2f} | Target: {target:.2f}")
    except Exception as e:
        print(f"Exit error: {e}")

def hard_cutoff():
    try:
        pos      = api.get_position(TICKER)
        price    = float(pos.current_price)
        entry    = float(pos.avg_entry_price)
        side_out = "sell" if pos.side == "long" else "buy"
        pnl      = (price - entry) if pos.side == "long" else (entry - price)
        api.submit_order(symbol=TICKER, qty=pos.qty, side=side_out, type="market", time_in_force="day")
        log_trade(BOT, TICKER, side_out, price, pos.qty, "ORB_1PM_CUTOFF", pnl=pnl)
        msg = f"1PM CUTOFF | {price:.2f} | P&L: {pnl:.2f}"; print(msg); notify(msg, BOT)
    except:
        pass

def daily_reset():
    global or_high, or_low, or_avg_volume, or_established, trade_taken
    or_high = or_low = or_avg_volume = None
    or_established = trade_taken = False
    print(f"\n{datetime.now(ET).strftime('%H:%M:%S')} — Daily reset")

def run():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return
    t = now.time()
    if dtime(9, 30) <= t <= dtime(9, 45):
        build_opening_range()
    elif dtime(9, 46) <= t <= dtime(13, 0):
        if any(p.symbol == TICKER for p in api.list_positions()):
            check_exit()
        else:
            check_entry()
    elif dtime(13, 0) < t <= dtime(13, 4):
        hard_cutoff()
    elif dtime(16, 0) <= t <= dtime(16, 5):
        daily_reset()
    else:
        print(f"{now.strftime('%H:%M:%S')} — Outside window")

print("ORB Day Trading Bot — Starting up")
print(f"Time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}")

run()

schedule.every(1).minutes.do(run)

while True:
    schedule.run_pending()
    time.sleep(30)
