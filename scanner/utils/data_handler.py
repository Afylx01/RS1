import yfinance as yf
import pandas as pd
import os
from datetime import datetime, timedelta

CACHE_DIR = 'data_cache'
CACHE_EXPIRY = timedelta(days=1)
NIFTY_SYMBOL = "^NSEI"

def process_stock_symbols(symbol):
    """Automatically append .NS suffix to symbols"""
    if not symbol.endswith('.NS'):
        return f"{symbol}.NS"
    return symbol

def get_stock_data(symbol, start_date, end_date):
    """
    Fetch stock data from yfinance with caching.
    """
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

    symbol_sanitized = symbol.replace('^', '') # For NIFTY symbol
    cache_file = os.path.join(CACHE_DIR, f"{symbol_sanitized}_data.pkl")

    if os.path.exists(cache_file):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if datetime.now() - file_mod_time < CACHE_EXPIRY:
            try:
                data = pd.read_pickle(cache_file)
                # Check if cached data covers the required date range
                if data.index.min() <= start_date and data.index.max() >= end_date:
                    return data
            except Exception as e:
                print(f"Could not read cache file for {symbol}. Refetching. Error: {e}")


    try:
        data = yf.download(symbol, start=start_date, end=end_date, progress=False)
        if data.empty:
            print(f"No data found for {symbol}. It might be delisted or the symbol is incorrect.")
            return None
        data.to_pickle(cache_file)
        return data
    except Exception as e:
        print(f"Failed to download data for {symbol}. Error: {e}")
        return None
