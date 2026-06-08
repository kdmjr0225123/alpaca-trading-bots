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

ET             = pytz.timezone('America/New_York')
BOT            = "MOMENTUM"
RISK_PER_TRADE = 0.01
MAX_POSITIONS  = 3
MAX_HOLD_DAYS  = 10

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "JPM", "LLY",
    "UNH", "XOM", "V", "MA", "HD", "PG", "COST", "MRK", "ABBV", "CVX",
    "CRM", "ORCL", "AMD", "NFLX", "UBER", "NOW", "PANW", "AMAT", "MU", "KLAC",
    "LRCX", "SNPS", "CDNS", "ADI", "MCHP", "QCOM", "TXN", "INTC", "ARM", "SMCI",
    "GS", "MS", "BAC", "WFC", "BLK", "SCHW", "AXP", "SPGI", "MCO", "ICE",
    "LIN", "SHW", "APD", "ECL", "NEM", "FCX", "VMC", "MLM", "URI", "PWR",
    "UNP", "CSX", "NSC", "FDX", "UPS", "DAL", "UAL", "LUV", "JBHT", "CHRW",
    "DHR", "TMO", "A", "WAT", "ZBH", "SYK", "BSX", "MDT", "ABT", "EW",
]

entry_dates = {}

def safe_float(val):
    if hasattr(val, 'item'):
        return float(val.item())
    return float(val)

def get_spy_regime():
    df = yf.download("SPY", period="60d", interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    sma50 = df['Close'].rolling(50).mean()
    return safe_float(df['Close'].iloc[-1]) > safe_float(sma50.iloc[-1])

def scan_for_breakouts():
    print(f"\n{datetime.now(ET).strftime('%H:%M:%S')} — Scanning {len(UNIVERSE)} tickers")
    signals = []
    for ticker in UNIVERSE:
        try:
            df = yf.download(ticker, period="1y", interval="1d", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) < 60:
                continue
            price     = safe_float(df['Close'].iloc[-1])
            high_52w  = safe_float(df['High'].max())
            vol_today = safe_float(df['Volume'].iloc[-1])
            vol_20avg = safe_float(df['Volume'].rolling(20).mean().iloc[-1])
            sma50     = safe_float(df['Close'].rolling(50).mean().iloc[-1])
            sma200    = safe_float(df['Close'].rolling(200).mean().iloc[-1])
            delta = df['Close'].diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = -delta.clip(upper=0).rolling(14).mean()
            rs    = gain / loss
            rsi   = safe_float((100 - (100 / (1 + rs))).iloc[-1])
            if any(np.isnan(v) for v in [price, high_52w, vol_20avg, sma50, sma200, rsi]):
                continue
            if (price >= high_52w * 0.99 and vol_today >= vol_20avg * 2.0 and
                    55 <= rsi <= 70 and price > sma50 and price > sma200):
                vol_ratio = vol_today / vol_20avg
                signals.append({"ticker": ticker, "price": price, "rsi": rsi, "vol_ratio": vol_ratio})
                print(f"  SIGNAL: {ticker} | Price: {price:.2f} | RSI: {rsi:.1f} | Vol: {vol_ratio:.1f}x")
        except:
            continue
    signals.sort(key=lambda x: x["vol_ratio"], reverse=True)
    return signals

def check_entries():
    if not get_spy_regime():
        print("SPY below 50 SMA — no entries")
        return
    try:
        positions    = api.list_positions()
        open_tickers = [p.symbol for p in positions]
        open_count   = len(open_tickers)
        if open_count >= MAX_POSITIONS:
            print(f"Max positions reached ({open_count}/{MAX_POSITIONS})")
            return
        signals     = scan_for_breakouts()
        if not signals:
            print("No signals today")
            return
        account     = api.get_account()
        risk_amount = float(account.portfolio_value) * RISK_PER_TRADE
        for signal in signals:
            if open_count >= MAX_POSITIONS:
                break
            ticker = signal["ticker"]
            if ticker in open_tickers:
                continue
            price     = signal["price"]
            stop      = price * 0.985
            target    = price * 1.04
            stop_dist = price - stop
            shares    = int(risk_amount / stop_dist) if stop_dist > 0 else 0
            if shares > 0:
                api.submit_order(symbol=ticker, qty=shares, side="buy", type="market", time_in_force="day")
                entry_dates[ticker] = datetime.now(ET)
                open_tickers.append(ticker)
                open_count += 1
                log_trade(BOT, ticker, "buy", price, shares, "MOMENTUM_BREAKOUT", stop, target)
                msg = f"BUY {ticker} | {price:.2f} x{shares} | RSI: {signal['rsi']:.1f} | Vol: {signal['vol_ratio']:.1f}x"
                print(f"*** {msg} ***"); notify(msg, BOT)
    except Exception as e:
        print(f"Entry error: {e}"); notify(f"MOMENTUM ERROR: {e}", BOT)

def check_exits():
    try:
        positions = api.list_positions()
        if not positions:
            return
        for pos in positions:
            ticker      = pos.symbol
            price       = float(pos.current_price)
            entry_price = float(pos.avg_entry_price)
            pnl         = price - entry_price
            pnl_pct     = (pnl / entry_price) * 100
            stop        = entry_price * 0.985
            target      = entry_price * 1.04
            entry_dt    = entry_dates.get(ticker)
            days_held   = (datetime.now(ET) - entry_dt).days if entry_dt else 0
            print(f"  {ticker} | {price:.2f} | P&L: {pnl_pct:.2f}% | Days: {days_held}")
            reason = None
            if price >= target:
                reason = "MOMENTUM_TP"
            elif price <= stop:
                reason = "MOMENTUM_SL"
            elif days_held >= MAX_HOLD_DAYS:
                reason = "MOMENTUM_TIME"
            if reason:
                api.submit_order(symbol=ticker, qty=pos.qty, side="sell", type="market", time_in_force="day")
                log_trade(BOT, ticker, "sell", price, pos.qty, reason, pnl=pnl)
                entry_dates.pop(ticker, None)
                msg = f"{reason} {ticker} | {price:.2f} | P&L: {pnl_pct:.2f}%"
                print(msg); notify(msg, BOT)
    except Exception as e:
        print(f"Exit error: {e}")

def run():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return
    t = now.time()
    if dtime(10, 0) <= t <= dtime(10, 5):
        check_entries()
    elif dtime(9, 45) <= t <= dtime(15, 55):
        check_exits()
    elif dtime(15, 55) < t <= dtime(16, 0):
        try:
            for pos in api.list_positions():
                price = float(pos.current_price)
                entry = float(pos.avg_entry_price)
                api.submit_order(symbol=pos.symbol, qty=pos.qty, side="sell", type="market", time_in_force="day")
                log_trade(BOT, pos.symbol, "sell", price, pos.qty, "MOMENTUM_EOD", pnl=price-entry)
                msg = f"EOD CLOSE {pos.symbol} | {price:.2f}"
                print(msg); notify(msg, BOT)
        except Exception as e:
            print(f"EOD error: {e}")
    else:
        print(f"{now.strftime('%H:%M:%S')} — Outside window")

print("Momentum Breakout Bot — Starting up")
run()
schedule.every(30).minutes.do(run)
while True:
    schedule.run_pending()
    time.sleep(60)
