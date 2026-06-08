import alpaca_trade_api as tradeapi
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
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
BOT            = "VWAP"

def safe_float(val):
    if hasattr(val, 'item'):
        return float(val.item())
    return float(val)

def get_daily_trend():
    df = yf.download(TICKER, period="60d", interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df['sma20'] = df['Close'].rolling(20).mean()
    return safe_float(df['Close'].iloc[-1]) > safe_float(df['sma20'].iloc[-1])

def get_intraday_data():
    df = yf.download(TICKER, period="1d", interval="5m", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
    else:
        df.index = df.index.tz_convert('America/New_York')
    return df

def calculate_vwap(df):
    tp_vol = (df['High'] + df['Low'] + df['Close']) / 3 * df['Volume']
    return tp_vol.cumsum() / df['Volume'].cumsum()

def calculate_rsi(series, period=7):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = -delta.clip(upper=0).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def is_market_open():
    et  = pytz.timezone('America/New_York')
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=45, second=0) <= now <= now.replace(hour=15, minute=30, second=0)

def check_vwap_signals():
    print(f"\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — VWAP check")
    try:
        positions    = api.list_positions()
        open_tickers = [p.symbol for p in positions]
        if TICKER in open_tickers:
            check_vwap_exit()
            return
        bull_day = get_daily_trend()
        df       = get_intraday_data()
        if df.empty or len(df) < 10:
            print("Insufficient intraday data")
            return
        df['vwap'] = calculate_vwap(df)
        df['rsi']  = calculate_rsi(df['Close'])
        latest = df.iloc[-1]
        price  = safe_float(latest['Close'])
        vwap   = safe_float(latest['vwap'])
        rsi    = safe_float(latest['rsi'])
        if np.isnan(vwap) or np.isnan(rsi):
            print("Indicators not ready")
            return
        print(f"QQQ | Price: {price:.2f} | VWAP: {vwap:.2f} | RSI: {rsi:.1f} | Bull: {bull_day}")
        account       = api.get_account()
        account_value = float(account.portfolio_value)
        risk_amount   = account_value * RISK_PER_TRADE
        if bull_day and price < vwap * 0.998 and rsi < 42:
            stop          = price * 0.995
            target        = vwap * 1.003
            stop_distance = price - stop
            shares        = int(risk_amount / stop_distance)
            if shares > 0:
                api.submit_order(symbol=TICKER, qty=shares, side="buy", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "buy", price, shares, "VWAP_MEAN_REV_LONG", stop, target)
                msg = f"BUY QQQ | Price: {price:.2f} | Shares: {shares} | Stop: {stop:.2f} | Target: {target:.2f}"
                print(f"*** {msg} ***"); notify(msg, BOT)
        elif not bull_day and price > vwap * 1.002 and rsi > 58:
            stop          = price * 1.005
            target        = vwap * 0.997
            stop_distance = stop - price
            shares        = int(risk_amount / stop_distance)
            if shares > 0:
                api.submit_order(symbol=TICKER, qty=shares, side="sell", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "sell", price, shares, "VWAP_MEAN_REV_SHORT", stop, target)
                msg = f"SHORT QQQ | Price: {price:.2f} | Shares: {shares} | Stop: {stop:.2f} | Target: {target:.2f}"
                print(f"*** {msg} ***"); notify(msg, BOT)
        else:
            print("No signal — conditions not met")
    except Exception as e:
        print(f"Error: {e}")
        notify(f"ERROR: {e}", BOT)

def check_vwap_exit():
    try:
        position    = api.get_position(TICKER)
        price       = float(position.current_price)
        entry_price = float(position.avg_entry_price)
        side        = position.side
        df          = get_intraday_data()
        df['vwap']  = calculate_vwap(df)
        vwap        = safe_float(df['vwap'].iloc[-1])
        if side == "long":
            stop   = entry_price * 0.995
            target = vwap * 1.003
            pnl    = price - entry_price
            if price >= target:
                api.submit_order(symbol=TICKER, qty=position.qty, side="sell", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "sell", price, position.qty, "VWAP_TP", pnl=pnl)
                msg = f"TAKE PROFIT | Price: {price:.2f} | Entry: {entry_price:.2f} | P&L: +{pnl:.2f}"
                print(msg); notify(msg, BOT)
            elif price <= stop:
                api.submit_order(symbol=TICKER, qty=position.qty, side="sell", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "sell", price, position.qty, "VWAP_SL", pnl=pnl)
                msg = f"STOP LOSS | Price: {price:.2f} | Entry: {entry_price:.2f} | P&L: {pnl:.2f}"
                print(msg); notify(msg, BOT)
            else:
                print(f"HOLDING LONG | Price: {price:.2f} | Entry: {entry_price:.2f} | Target: {target:.2f} | Stop: {stop:.2f}")
        elif side == "short":
            stop   = entry_price * 1.005
            target = vwap * 0.997
            pnl    = entry_price - price
            if price <= target:
                api.submit_order(symbol=TICKER, qty=position.qty, side="buy", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "buy", price, position.qty, "VWAP_TP", pnl=pnl)
                msg = f"SHORT TP | Price: {price:.2f} | Entry: {entry_price:.2f} | P&L: +{pnl:.2f}"
                print(msg); notify(msg, BOT)
            elif price >= stop:
                api.submit_order(symbol=TICKER, qty=position.qty, side="buy", type="market", time_in_force="day")
                log_trade(BOT, TICKER, "buy", price, position.qty, "VWAP_SL", pnl=pnl)
                msg = f"SHORT SL | Price: {price:.2f} | Entry: {entry_price:.2f} | P&L: {pnl:.2f}"
                print(msg); notify(msg, BOT)
            else:
                print(f"HOLDING SHORT | Price: {price:.2f} | Entry: {entry_price:.2f}")
    except Exception as e:
        print(f"Exit check error: {e}")

def eod_close():
    try:
        position    = api.get_position(TICKER)
        price       = float(position.current_price)
        entry_price = float(position.avg_entry_price)
        side_out    = "sell" if position.side == "long" else "buy"
        pnl         = (price - entry_price) if position.side == "long" else (entry_price - price)
        api.submit_order(symbol=TICKER, qty=position.qty, side=side_out, type="market", time_in_force="day")
        log_trade(BOT, TICKER, side_out, price, position.qty, "VWAP_EOD", pnl=pnl)
        msg = f"EOD CLOSE | Price: {price:.2f} | P&L: {pnl:.2f}"
        print(msg); notify(msg, BOT)
    except:
        pass

def run_if_market_open():
    et  = pytz.timezone('America/New_York')
    now = datetime.now(et)
    if now.weekday() >= 5:
        return
    eod_time = now.replace(hour=15, minute=28, second=0)
    if abs((now - eod_time).total_seconds()) < 120:
        eod_close()
        return
    if is_market_open():
        check_vwap_signals()
    else:
        print(f"{datetime.now().strftime('%H:%M:%S')} — Market closed")

print("VWAP Mean Reversion Bot — Starting up")
if is_market_open():
    check_vwap_signals()

schedule.every(15).minutes.do(run_if_market_open)

while True:
    schedule.run_pending()
    time.sleep(60)
