import csv
import os
from datetime import datetime

LOG_FILE = os.environ.get("TRADE_LOG_PATH", "trades.csv")
HEADERS = ["timestamp", "bot", "ticker", "side", "price", "shares", "reason", "stop", "target", "pnl"]

def log_trade(bot, ticker, side, price, shares, reason, stop=None, target=None, pnl=None):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(HEADERS)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            bot, ticker, side, f"{price:.2f}", shares, reason,
            f"{stop:.2f}" if stop is not None else "",
            f"{target:.2f}" if target is not None else "",
            f"{pnl:.2f}" if pnl is not None else "",
        ])
    print(f"  [LOG] {bot} | {side.upper()} {ticker} @ {price:.2f} x{shares} | {reason}")
