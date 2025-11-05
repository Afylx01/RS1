#!/usr/bin/env python3
"""
nse_high_delivery_scanner.py
Enhanced NSE High-Delivery Scanner with RS, RSI Analysis and Telegram Integration.
Supports fetching Market Cap from Screener.in or yfinance.
"""
import argparse
import logging
import os
import sys
import time
import requests
import re
import pickle
import json
import pyotp
from datetime import datetime, timedelta, date as datetime_date
from typing import Dict, List, Tuple, Optional, Any
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, inspect
from SmartApi import SmartConnect
import yfinance as yf
import configparser
import math
import concurrent.futures
from tqdm import tqdm
import threading # Import threading for rate limiting control

# ============================================================================
# RATE LIMITING CONTROL OBJECTS (Added near the top)
# ============================================================================
# Global lock for managing API calls
api_lock = threading.Lock()
# Global state for rate limiting (requests per second)
last_reset_time = time.time()
request_count = 0
MAX_REQUESTS_PER_SECOND = 3 # As per Angel One's limit for getCandleData

def rate_limit_check():
    """Thread-safe check and increment for API rate limiting."""
    global last_reset_time, request_count
    with api_lock:
        current_time = time.time()
        # If more than 1 second has passed since the last reset, reset the counter
        if current_time - last_reset_time >= 1.0:
            last_reset_time = current_time
            request_count = 0

        # Check if we are under the limit
        if request_count >= MAX_REQUESTS_PER_SECOND:
            # Calculate sleep time until the next second starts
            sleep_time = 1.0 - (current_time - last_reset_time)
            if sleep_time > 0:
                time.sleep(sleep_time)
            # After sleeping, reset the timer and counter
            last_reset_time = time.time()
            request_count = 0

        # Increment the counter
        request_count += 1

# ============================================================================
# CONFIGURATION LOADING SECTION
# ============================================================================
config = configparser.ConfigParser()
config_path = 'config.ini' # Assumes config.ini is in the same directory
if not os.path.exists(config_path):
    print(f"Configuration file {config_path} not found. Please create it.")
    sys.exit(1)
config.read(config_path)
# Load configuration from config.ini
try:
    # Delivery Filters
    MIN_DELIVERY_QUANTITY = config.getint('DEFAULT', 'min_delivery_quantity')
    MIN_PERCENT_CHANGE = config.getfloat('DEFAULT', 'min_percent_change')
    MIN_DELIVERY_TIMES = config.getfloat('DEFAULT', 'min_delivery_times')
    # Market Cap Filter
    MIN_MARKET_CAP_CR = config.getfloat('DEFAULT', 'min_market_cap_cr')
    MARKET_CAP_SOURCE = config.get('DEFAULT', 'market_cap_source').lower()
    # Number of workers for market cap fetching
    MARKET_CAP_WORKERS = config.getint('DEFAULT', 'market_cap_workers')
    # Technical Indicators
    RS_PERIOD = config.getint('DEFAULT', 'rs_period')
    RSI_PERIOD = config.getint('DEFAULT', 'rsi_period')
    # System Defaults
    DEFAULTS = {
        "days_back": config.getint('DEFAULT', 'days_back'),
        "max_retries_today": config.getint('DEFAULT', 'max_retries_today'),
        "download_dir": config.get('DEFAULT', 'download_dir'),
        "output_dir": config.get('DEFAULT', 'output_dir'),
        "cache_dir": config.get('DEFAULT', 'cache_dir'),
        "user_agent": config.get('DEFAULT', 'user_agent'),
        "timeout": config.getint('DEFAULT', 'timeout'),
        "sqlite_db": config.get('DEFAULT', 'sqlite_db'),
    }
    # Telegram Configuration
    TELEGRAM_BOT_TOKEN = config.get('DEFAULT', 'telegram_bot_token')
    TELEGRAM_CHAT_ID = config.get('DEFAULT', 'telegram_chat_id')
    # Angel One API Configuration
    ANGEL_API_KEY = config.get('DEFAULT', 'angel_api_key')
    ANGEL_USERNAME = config.get('DEFAULT', 'angel_username')
    ANGEL_PIN = config.get('DEFAULT', 'angel_pin')
    ANGEL_TOTP_SECRET = config.get('DEFAULT', 'angel_totp_secret')
    BENCHMARK_SYMBOL = config.get('DEFAULT', 'benchmark_symbol')
    BENCHMARK_TOKEN = config.get('DEFAULT', 'benchmark_token')
except configparser.Error as e:
    print(f"Error reading configuration file {config_path}: {e}")
    sys.exit(1)
# Validate MARKET_CAP_SOURCE
if MARKET_CAP_SOURCE not in ['screener', 'yfinance']:
    print(f"Invalid market_cap_source '{MARKET_CAP_SOURCE}' in config.ini. Must be 'screener' or 'yfinance'.")
    sys.exit(1)
BASE_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================
def ensure_dir(path: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(path, exist_ok=True)

def setup_logging(log_path: Optional[str] = None) -> logging.Logger:
    """Setup logging configuration."""
    ensure_dir(os.path.dirname(log_path) or ".")
    logger = logging.getLogger("nse_scanner")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    if log_path:
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception as e:
            print(f"Failed to setup file logging: {e}")
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger

def date_to_filename(d: datetime) -> str:
    """Convert date to NSE filename format."""
    return f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"

def is_weekend(d: datetime) -> bool:
    """Check if date is weekend."""
    return d.weekday() >= 5
# ============================================================================
# USER INPUT FUNCTIONS
# ============================================================================
def get_user_target_date() -> datetime:
    """Prompt user to input target date in DDMMYY format."""
    print("" + "="*60)
    print("TARGET DATE SELECTION")
    print("="*60)
    print(f"Leave blank to use TODAY: {datetime.now().strftime('%d/%m/%Y')}")
    print("Enter Target Date in DDMMYY format (e.g., 190925 for Sep 19, 2025)")
    print("NOTE: Data must be available for the selected date")
    print("="*60)
    try:
        date_str = input("Enter Target Date (DDMMYY): ").strip()
        if not date_str:
            # Default to the last weekday
            target_date = datetime.now()
            # If today is weekend, go back to Friday
            while target_date.weekday() >= 5:
                target_date = target_date - timedelta(days=1)
            print(f"Using last trading day: {target_date.strftime('%Y-%m-%d')}")
            return target_date
        if len(date_str) != 6:
            raise ValueError("Date must be exactly 6 digits")
        day = int(date_str[0:2])
        month = int(date_str[2:4])
        year = int(date_str[4:6])
        # Convert 2-digit year to 4-digit
        year = year + 2000 if year < 50 else year + 1900
        target_date = datetime(year, month, day)
        # Warn if date is weekend
        if target_date.weekday() >= 5:
            print(f"⚠️  Warning: {target_date.strftime('%Y-%m-%d')} is a weekend. Data may not be available.")
        print(f"Target Date set to: {target_date.strftime('%Y-%m-%d')}")
        return target_date
    except Exception as e:
        print(f"Error: {e}. Using last trading day instead.")
        target_date = datetime.now()
        while target_date.weekday() >= 5:
            target_date = target_date - timedelta(days=1)
        return target_date
# ============================================================================
# DATA DOWNLOAD FUNCTIONS
# ============================================================================
def download_bhavcopy_for_date(
    date: datetime,
    download_dir: str,
    headers: Dict[str, str],
    timeout: int,
    logger: Optional[logging.Logger] = None
) -> Tuple[Optional[str], bool]:
    """Download bhavcopy for specific date."""
    filename = date_to_filename(date)
    url = BASE_URL.format(date=date.strftime("%d%m%Y"))
    local_path = os.path.join(download_dir, filename)
    if os.path.exists(local_path):
        if logger:
            logger.info(f"Found existing file for {date.date()} -> {local_path}")
        return local_path, True
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and resp.content:
            ensure_dir(download_dir)
            with open(local_path, "wb") as f:
                f.write(resp.content)
            if logger:
                logger.info(f"Downloaded {filename}")
            return local_path, True
        else:
            if logger:
                logger.info(f"No file for {date.date()} (HTTP {resp.status_code})")
            return None, False
    except requests.RequestException as e:
        if logger:
            logger.warning(f"Failed to fetch {url}: {e}")
        return None, False

def collect_last_n_trading_days_from_target(
    n: int,
    target_date: datetime,
    download_dir: str,
    headers: Dict[str, str],
    timeout: int,
    max_lookback: int = 365,
    logger: Optional[logging.Logger] = None
) -> List[Tuple[datetime, Optional[str], bool]]:
    """Collect the last N trading days before and including target date."""
    results = []
    collected = 0
    lookback = 0
    d = target_date
    while collected < n and lookback < max_lookback:
        if is_weekend(d):
            d = d - timedelta(days=1)
            lookback += 1
            continue
        local_path, ok = download_bhavcopy_for_date(d, download_dir, headers, timeout, logger)
        results.append((d, local_path, ok))
        if ok:
            collected += 1
        d = d - timedelta(days=1)
        lookback += 1
    results.reverse()  # Chronological order
    if logger:
        logger.info(f"Collected {collected} successful sessions up to {target_date.date()}")
    return results
# ============================================================================
# CSV PROCESSING FUNCTIONS - FIXED
# ============================================================================
def read_and_clean_bhavcopy(csv_path: str, logger: Optional[logging.Logger] = None) -> pd.DataFrame:
    """Read and clean NSE bhavcopy CSV file."""
    read_attempts = [
        {"encoding": "utf-8", "sep": ","},
        {"encoding": "latin1", "sep": ","},
        {"encoding": "latin1", "sep": ";"},
        {"encoding": "latin1", "sep": "|"},
        {"encoding": "latin1", "sep": "\t"},
    ]
    df = None
    for opt in read_attempts:
        try:
            df = pd.read_csv(csv_path, **opt, engine="python")
            if logger:
                logger.info(f"Read {os.path.basename(csv_path)} with options {opt}")
            break
        except Exception:
            continue
    if df is None:
        raise ValueError(f"Failed to read {csv_path}")
    # Normalize column names
    df.columns = [c.strip().upper() for c in df.columns]
    # Find date column (could be DATE1 or DATE)
    date_col = None
    for c in ["DATE1", "DATE", "TIMESTAMP"]:
        if c in df.columns:
            date_col = c
            break
    if date_col:
        df["DATE"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
    else:
        df["DATE"] = pd.NaT
    # Normalize SERIES
    if "SERIES" in df.columns:
        df["SERIES"] = df["SERIES"].astype(str).str.strip().str.upper()
    else:
        df["SERIES"] = ""
    # Filter EQ series
    df_eq = df[df["SERIES"] == "EQ"].copy()
    # Convert numeric columns
    numeric_cols = [
        "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE",
        "LAST_PRICE", "CLOSE_PRICE", "AVG_PRICE", "TTL_TRD_QNTY",
        "TURNOVER_LACS", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER",
    ]
    for c in numeric_cols:
        if c in df_eq.columns:
            df_eq[c] = df_eq[c].astype(str).str.replace(",", "", regex=False)
            df_eq[c] = pd.to_numeric(df_eq[c], errors="coerce")
    # Keep only relevant columns for database (EXCLUDE DATE1 or other date columns)
    keep_cols = ["SYMBOL", "SERIES", "DATE", "PREV_CLOSE", "OPEN_PRICE",
                 "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE", "CLOSE_PRICE",
                 "AVG_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "NO_OF_TRADES",
                 "DELIV_QTY", "DELIV_PER"]
    # Only keep columns that exist
    present_cols = [c for c in keep_cols if c in df_eq.columns]
    df_eq = df_eq[present_cols].copy()
    # Filter valid rows
    df_eq = df_eq[df_eq["SYMBOL"].notna() & df_eq["DATE"].notna()].copy()
    # Convert DATE to date object
    df_eq["DATE"] = pd.to_datetime(df_eq["DATE"]).dt.date
    if logger:
        logger.info(f"Cleaned {os.path.basename(csv_path)} -> {len(df_eq)} EQ rows")
    return df_eq
# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================
def persist_to_sqlite(df: pd.DataFrame, db_path: str, logger: Optional[logging.Logger] = None) -> None:
    """Persist DataFrame to SQLite database."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            df.to_sql("daily_bhav", conn, if_exists="append", index=False)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_date ON daily_bhav (SYMBOL, DATE)")
            except Exception:
                pass
        if logger:
            logger.info(f"Persisted {len(df)} rows to {db_path}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to persist data to {db_path}: {e}")

def dedupe_db_by_symbol_date(db_path: str, logger: Optional[logging.Logger] = None) -> None:
    """Deduplicate database keeping row with largest DELIV_QTY per SYMBOL+DATE."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        df = pd.read_sql_table("daily_bhav", engine)
    except Exception:
        if logger:
            logger.warning("daily_bhav table not found for deduplication")
        return
    if df.empty:
        return
    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce").dt.date
    if "DELIV_QTY" in df.columns:
        df_sorted = df.sort_values(["SYMBOL", "DATE", "DELIV_QTY"], ascending=[True, True, False])
    else:
        df_sorted = df.sort_values(["SYMBOL", "DATE"])
    df_dedup = df_sorted.drop_duplicates(subset=["SYMBOL", "DATE"], keep="first").reset_index(drop=True)
    try:
        with engine.begin() as conn:
            df_dedup.to_sql("daily_bhav", conn, if_exists="replace", index=False)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_date ON daily_bhav (SYMBOL, DATE)")
            except Exception:
                pass
        if logger:
            logger.info(f"Deduplicated DB: {len(df_dedup)} unique SYMBOL+DATE rows")
    except Exception as e:
        if logger:
            logger.error(f"Failed to deduplicate database: {e}")
# ============================================================================
# ANALYSIS FUNCTIONS
# ============================================================================
def compute_avg_deliv_qty_5_prior(
    db_path: str,
    target_date: Any,
    logger: Optional[logging.Logger] = None
) -> pd.DataFrame:
    """Calculate average delivery quantity for 5 prior trading days."""
    # Normalize target_date
    if isinstance(target_date, pd.Timestamp):
        target_date = target_date.date()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()
    elif isinstance(target_date, str):
        target_date = pd.to_datetime(target_date).date()
    elif not isinstance(target_date, datetime_date):
        target_date = pd.to_datetime(target_date).date()
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        df = pd.read_sql_table("daily_bhav", engine)
    except Exception:
        if logger:
            logger.warning("daily_bhav table not accessible")
        return pd.DataFrame(columns=["SYMBOL", "AVG_DELIV_QTY_5D_PRIOR"])
    if df.empty:
        return pd.DataFrame(columns=["SYMBOL", "AVG_DELIV_QTY_5D_PRIOR"])
    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce").dt.date
    df_prior = df[df["DATE"] < target_date].copy()
    results = []
    for sym, g in df_prior.groupby("SYMBOL"):
        g_sorted = g.sort_values("DATE", ascending=False)
        g_unique_dates = g_sorted.drop_duplicates(subset="DATE", keep="first")
        vals = g_unique_dates["DELIV_QTY"].dropna().astype(float).head(5).values
        if len(vals) >= 5:
            avg = float(pd.Series(vals).mean())
            results.append({"SYMBOL": sym, "AVG_DELIV_QTY_5D_PRIOR": avg})
    out = pd.DataFrame(results)
    if logger:
        logger.info(f"Computed 5-prior avg for {len(out)} symbols")
    return out

def create_daily_snapshot_for_date(
    db_path: str,
    target_date: Any,
    logger: Optional[logging.Logger] = None
) -> pd.DataFrame:
    """Create snapshot for specific date with calculations."""
    # Normalize target_date
    if isinstance(target_date, pd.Timestamp):
        target_date = target_date.date()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()
    elif isinstance(target_date, str):
        target_date = pd.to_datetime(target_date).date()
    elif not isinstance(target_date, datetime_date):
        target_date = pd.to_datetime(target_date).date()
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        df = pd.read_sql_table("daily_bhav", engine)
    except Exception:
        if logger:
            logger.error("daily_bhav table is missing")
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce").dt.date
    # Check available dates
    available_dates = sorted(df["DATE"].unique())
    if logger:
        logger.info(f"Available dates in database: {available_dates[-5:] if len(available_dates) > 5 else available_dates}")
    # If target date not available, use the latest available date
    if target_date not in available_dates:
        if available_dates:
            latest_date = available_dates[-1]
            if logger:
                logger.warning(f"Target date {target_date} not found. Using latest available date: {latest_date}")
            target_date = latest_date
        else:
            if logger:
                logger.error("No data available in database")
            return pd.DataFrame()
    snapshot = df[df["DATE"] == target_date].copy()
    if snapshot.empty:
        if logger:
            logger.error(f"No rows for target date {target_date}")
        return pd.DataFrame()
    # Deduplicate by SYMBOL
    if "DELIV_QTY" in snapshot.columns:
        snapshot_sorted = snapshot.sort_values(["SYMBOL", "DELIV_QTY"], ascending=[True, False])
    else:
        snapshot_sorted = snapshot.sort_values("SYMBOL")
    snapshot = snapshot_sorted.drop_duplicates(subset=["SYMBOL"], keep="first").reset_index(drop=True)
    # Add 5-day average
    avg5 = compute_avg_deliv_qty_5_prior(db_path, target_date, logger=logger)
    merged = snapshot.merge(avg5, how="left", on="SYMBOL")
    # Calculate metrics
    merged["%CHANGE"] = ((merged["CLOSE_PRICE"] - merged["PREV_CLOSE"]) / merged["PREV_CLOSE"]) * 100
    merged["DELIVERY_TIMES"] = merged["DELIV_QTY"] / merged["AVG_DELIV_QTY_5D_PRIOR"]
    merged["DELIVERY_VALUE_LACS"] = (merged["DELIV_QTY"] * merged["CLOSE_PRICE"]) / 100000 # Delivery value in Lacs
    # Select columns
    cols = ["SYMBOL", "DATE", "PREV_CLOSE", "CLOSE_PRICE", "DELIV_QTY",
            "DELIV_PER", "%CHANGE", "AVG_DELIV_QTY_5D_PRIOR", "DELIVERY_TIMES", "DELIVERY_VALUE_LACS"]
    for c in cols:
        if c not in merged.columns:
            merged[c] = pd.NA
    return merged[cols]

def filter_snapshot(df_snapshot: pd.DataFrame, logger: Optional[logging.Logger] = None) -> pd.DataFrame:
    """Apply user-configurable filters to the snapshot."""
    if df_snapshot.empty:
        return df_snapshot
    filtered = df_snapshot.copy()
    initial_count = len(filtered)
    # Apply filters
    filtered = filtered[filtered["DELIV_QTY"].fillna(0) >= MIN_DELIVERY_QUANTITY]
    if logger:
        logger.info(f"After DELIV_QTY >= {MIN_DELIVERY_QUANTITY}: {len(filtered)} stocks")
    filtered = filtered[filtered["%CHANGE"].fillna(-9999) > MIN_PERCENT_CHANGE]
    if logger:
        logger.info(f"After %CHANGE > {MIN_PERCENT_CHANGE}: {len(filtered)} stocks")
    filtered = filtered[filtered["DELIVERY_TIMES"].fillna(0) >= MIN_DELIVERY_TIMES]
    if logger:
        logger.info(f"After DELIVERY_TIMES >= {MIN_DELIVERY_TIMES}: {len(filtered)} stocks")
    filtered = filtered[filtered["AVG_DELIV_QTY_5D_PRIOR"].notna()]
    if logger:
        logger.info(f"Final filtered: {initial_count} -> {len(filtered)} rows")
    return filtered

# ============================================================================
# TECHNICAL INDICATORS
# ============================================================================
def calculate_rsi(prices: pd.Series, period: int = 14) -> float:
    if len(prices) < period + 1:
        return np.nan
    deltas = prices.diff()
    gains = deltas.where(deltas > 0, 0.0)
    losses = -deltas.where(deltas < 0, 0.0)
    # Initial average gain/loss: SMA over first 'period' values
    avg_gain = gains.iloc[1:period+1].mean()
    avg_loss = losses.iloc[1:period+1].mean()
    # Wilder's smoothing for remaining values
    for i in range(period + 1, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses.iloc[i]) / period
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def get_technical_indicators(
    symbols: List[str],
    target_date: Any,
    logger: Optional[logging.Logger] = None
) -> Dict[str, Dict[str, float]]:
    """
    Calculate technical indicators (RS and RSI) for given symbols.
    Optimized to fetch historical data for multiple symbols in parallel,
    respecting the Angel One API rate limit (3 req/sec for getCandleData).
    Returns dictionary with symbol as key and dict of indicators as value.
    """
    ensure_dir(DEFAULTS["cache_dir"])

    def get_totp_token():
        totp = pyotp.TOTP(ANGEL_TOTP_SECRET)
        return totp.now()

    def login_to_angel():
        smart_api = SmartConnect(api_key=ANGEL_API_KEY)
        try:
            totp = get_totp_token()
            data = smart_api.generateSession(ANGEL_USERNAME, ANGEL_PIN, totp)
            if not data['status']:
                raise Exception(f"Login failed: {data}")
            if logger:
                logger.info("Angel One login successful for technical analysis")
            return smart_api
        except Exception as e:
            if logger:
                logger.error(f"Angel One login failed: {e}")
            raise

    def get_cache_key(symbol, from_date, to_date, indicator_type):
        # Include indicator type in cache key
        return f"{symbol}_{from_date.strftime('%Y-%m-%d')}_{to_date.strftime('%Y-%m-%d')}_{indicator_type}.pkl"

    def fetch_and_cache_data(smart_api, symbol, token, from_date, to_date, indicator_type):
        cache_key = get_cache_key(symbol, from_date, to_date, indicator_type)
        cache_path = os.path.join(DEFAULTS["cache_dir"], cache_key)
        # Check cache
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'rb') as f:
                    return pickle.load(f)
            except Exception:
                if logger:
                    logger.warning(f"Cache load failed for {cache_key}, fetching fresh data.")
                pass # If cache fails, proceed to fetch

        # Fetch from API
        try:
            # Rate limit check BEFORE making the API call
            rate_limit_check()
            historic_param = {
                "exchange": "NSE",
                "symboltoken": token,
                "interval": "ONE_DAY",
                "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
                "todate": to_date.strftime("%Y-%m-%d %H:%M")
            }
            # time.sleep(1)  # Rate limiting - REMOVED, replaced by rate_limit_check()
            candle_data = smart_api.getCandleData(historic_param)
            if candle_data['status'] and candle_data['data']:
                df = pd.DataFrame(
                    candle_data['data'],
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df.set_index('timestamp', inplace=True)
                # Cache the data
                with open(cache_path, 'wb') as f:
                    pickle.dump(df, f)
                return df
            else:
                if logger:
                    logger.warning(f"No data returned from API for {symbol} ({indicator_type})")
                return None
        except Exception as e:
            if logger:
                logger.error(f"Error fetching data for {symbol} ({indicator_type}): {e}")
            return None

    def calculate_relative_strength(stock_df, benchmark_df, period):
        if stock_df is None or benchmark_df is None:
            return np.nan
        combined = stock_df[['close']].join(
            benchmark_df[['close']],
            lsuffix='_stock',
            rsuffix='_bench',
            how='inner'
        )
        if len(combined) < period + 1:
            return np.nan
        stock_return = combined['close_stock'] / combined['close_stock'].shift(period)
        bench_return = combined['close_bench'] / combined['close_bench'].shift(period)
        rs_series = (stock_return / bench_return) - 1
        return rs_series.iloc[-1] if not pd.isna(rs_series.iloc[-1]) else np.nan

    def download_scrip_master():
        url = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers, timeout=30)
            scrip_data = response.json()
            symbol_token_map = {}
            for item in scrip_data:
                if item.get('exch_seg') == 'NSE':
                    symbol = item.get('symbol', '')
                    token = item.get('token', '')
                    if symbol and token:
                        symbol_token_map[symbol] = token
                        if symbol.endswith('-EQ'):
                            clean_symbol = symbol[:-3]
                            symbol_token_map[clean_symbol] = token
            return symbol_token_map
        except Exception as e:
            if logger:
                logger.error(f"Error downloading scrip master: {e}")
            return {}

    # Main logic
    if not symbols:
        return {}
    results = {}

    # Login to Angel One
    try:
        smart_api = login_to_angel()
    except Exception:
        if logger:
            logger.error("Failed to login to Angel One")
        return {}

    # Calculate date range
    if isinstance(target_date, datetime_date) and not isinstance(target_date, datetime):
        end_date = datetime.combine(target_date, datetime.min.time())
    else:
        end_date = target_date if isinstance(target_date, datetime) else datetime.now()
    # Need more data for RSI calculation
    start_date = end_date - timedelta(days=max(RS_PERIOD, RSI_PERIOD) + 30)

    # Fetch benchmark data FIRST
    if logger:
        logger.info("Fetching benchmark data for technical analysis...")
    benchmark_df = fetch_and_cache_data(smart_api, BENCHMARK_SYMBOL, BENCHMARK_TOKEN, start_date, end_date, "BENCHMARK")
    if benchmark_df is None:
        if logger:
            logger.error("Failed to fetch benchmark data")
        return {}

    # Get scrip master
    scrip_master = download_scrip_master()
    if not scrip_master:
        if logger:
            logger.error("Failed to download scrip master")
        return {}

    # Map symbols to tokens
    symbol_token_map = {}
    for symbol in symbols:
        clean_symbol = symbol.strip().upper()
        formats = [clean_symbol, f"{clean_symbol}-EQ"]
        for fmt in formats:
            if fmt in scrip_master:
                symbol_token_map[clean_symbol] = scrip_master[fmt]
                break

    # Prepare list of symbols with tokens for parallel fetching
    symbols_to_fetch = []
    for symbol in symbols:
        if symbol in symbol_token_map:
            token = symbol_token_map[symbol]
            symbols_to_fetch.append((symbol, token))
        else:
            if logger:
                logger.warning(f"Symbol {symbol} not found in scrip master, skipping.")
            # Add a placeholder result for missing symbols
            results[symbol] = {f'RS_{RS_PERIOD}': np.nan, f'RSI_{RSI_PERIOD}': np.nan}

    if not symbols_to_fetch:
        if logger:
            logger.info("No symbols found in scrip master for fetching historical data.")
        return results

    # Define the worker function for parallel execution
    def fetch_and_calculate_indicators(args):
        symbol, token = args
        try:
            stock_df = fetch_and_cache_data(smart_api, symbol, token, start_date, end_date, "STOCK")
            if stock_df is None or len(stock_df) < max(RS_PERIOD, RSI_PERIOD) + 1:
                if logger:
                    logger.warning(f"Insufficient data for {symbol}, skipping.")
                return symbol, {f'RS_{RS_PERIOD}': np.nan, f'RSI_{RSI_PERIOD}': np.nan}

            # Calculate RS
            rs_value = calculate_relative_strength(stock_df, benchmark_df, RS_PERIOD)
            # Calculate RSI
            rsi_value = calculate_rsi(stock_df['close'], RSI_PERIOD)

            if logger:
                logger.info(f"Calculated indicators for {symbol}: RS={rs_value:.4f}, RSI={rsi_value:.2f}")
            return symbol, {f'RS_{RS_PERIOD}': rs_value, f'RSI_{RSI_PERIOD}': rsi_value}
        except Exception as e:
            if logger:
                logger.error(f"Error calculating indicators for {symbol}: {e}")
            return symbol, {f'RS_{RS_PERIOD}': np.nan, f'RSI_{RSI_PERIOD}': np.nan}


    # Use ThreadPoolExecutor for parallel fetching and calculation
    # The rate_limit_check inside fetch_and_cache_data ensures the 3 req/sec limit is respected
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_REQUESTS_PER_SECOND) as executor:
        # Submit all tasks
        future_to_symbol = {executor.submit(fetch_and_calculate_indicators, sym_tok): sym_tok[0] for sym_tok in symbols_to_fetch}
        # Process completed tasks
        for future in concurrent.futures.as_completed(future_to_symbol):
            symbol, indicators = future.result()
            results[symbol] = indicators

    if logger:
        valid_rs = sum(1 for v in results.values() if not pd.isna(v.get(f'RS_{RS_PERIOD}')))
        valid_rsi = sum(1 for v in results.values() if not pd.isna(v.get(f'RSI_{RSI_PERIOD}')))
        logger.info(f"Technical analysis completed for {len(results)} symbols. Valid RS: {valid_rs}, Valid RSI: {valid_rsi}")
    return results

def add_technical_indicators_to_df(
    df: pd.DataFrame,
    target_date: Any,
    logger: Optional[logging.Logger] = None
) -> pd.DataFrame:
    """Add RS and RSI columns to DataFrame."""
    if df.empty:
        return df
    symbols = df['SYMBOL'].tolist()
    if logger:
        logger.info(f"Calculating technical indicators for {len(symbols)} stocks...")
    indicators = get_technical_indicators(symbols, target_date, logger=logger)
    # Add columns with proper formatting
    df[f'RS_{RS_PERIOD}'] = df['SYMBOL'].apply(
        lambda x: indicators.get(x, {}).get(f'RS_{RS_PERIOD}', np.nan)
    )
    df[f'RSI_{RSI_PERIOD}'] = df['SYMBOL'].apply(
        lambda x: indicators.get(x, {}).get(f'RSI_{RSI_PERIOD}', np.nan)
    )
    # Format the values
    df[f'RS_{RS_PERIOD}'] = pd.to_numeric(df[f'RS_{RS_PERIOD}'], errors='coerce')
    df[f'RSI_{RSI_PERIOD}'] = pd.to_numeric(df[f'RSI_{RSI_PERIOD}'], errors='coerce')
    if logger:
        valid_rs = df[f'RS_{RS_PERIOD}'].notna().sum()
        valid_rsi = df[f'RSI_{RSI_PERIOD}'].notna().sum()
        logger.info(f"Added RS values for {valid_rs} stocks, RSI values for {valid_rsi} stocks")
    return df
# ============================================================================
# MARKET DATA ENRICHMENT
# ============================================================================
def get_market_cap_screener(stock_slug: str, logger: Optional[logging.Logger] = None) -> Tuple[Optional[str], Optional[float]]:
    """Fetch Market Cap from Screener.in."""
    url = f"https://www.screener.in/company/{stock_slug}/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            if logger:
                logger.debug(f"Screener.in request failed for {stock_slug} (Status: {resp.status_code})")
            return None, None
    except requests.RequestException as e:
        if logger:
            logger.debug(f"Network error fetching Screener.in for {stock_slug}: {e}")
        return None, None
    soup = BeautifulSoup(resp.text, "html.parser")
    for li in soup.find_all("li"):
        if li.find(string=lambda text: text and "Market Cap" in text.strip()):
            num_span = li.find("span", {"class": "number"})
            if num_span:
                cap_str = num_span.text.strip()
            else:
                full_text = li.get_text().strip()
                cap_str = full_text.replace("Market Cap", "", 1).strip(": \t")
            cap_clean = re.sub(r'[^\d.,]', '', cap_str).replace(',', '')
            try:
                cap_value = float(cap_clean)
            except ValueError:
                cap_value = None
            return cap_str, cap_value
    if logger:
        logger.debug(f"Market Cap not found on Screener.in page for {stock_slug}")
    return None, None

def get_market_cap_yfinance(symbol: str, logger: Optional[logging.Logger] = None) -> Tuple[Optional[str], Optional[float]]:
    """Fetch Market Cap from yfinance."""
    # yfinance expects NSE symbols like 'SBIN.NS'
    yf_symbol = f"{symbol}.NS"
    try:
        stock = yf.Ticker(yf_symbol)
        info = stock.info
        if 'marketCap' in info and info['marketCap'] is not None:
            market_cap_raw = info['marketCap']
            market_cap_cr = market_cap_raw / 10_000_000  # Convert to Crores
            market_cap_str = f"₹{market_cap_cr:,.2f} Cr"
            return market_cap_str, market_cap_cr
        else:
            if logger:
                logger.debug(f"Market Cap not found in yfinance info for {symbol}")
            return None, None
    except Exception as e:
        if logger:
            logger.debug(f"Error fetching market cap from yfinance for {symbol}: {e}")
        return None, None

def fetch_market_cap_for_symbol(symbol_info: Tuple[str, str], logger: Optional[logging.Logger] = None) -> Tuple[str, Optional[str], Optional[float], Optional[str], Optional[str]]:
    """Helper function to fetch market cap for a single symbol."""
    symbol, source = symbol_info
    market_cap_str, market_cap_num = None, None
    source_url = None
    tradingview_url = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}" #https://in.tradingview.com/chart/ob4F1XSN/?symbol=NSE:{symbol}
    if source == 'screener':
        market_cap_str, market_cap_num = get_market_cap_screener(symbol, logger=logger)
        source_url = f"https://www.screener.in/company/{symbol}/"
    elif source == 'yfinance':
        market_cap_str, market_cap_num = get_market_cap_yfinance(symbol, logger=logger)
        source_url = f"https://finance.yahoo.com/quote/{symbol}.NS"
    return symbol, market_cap_str, market_cap_num, source_url, tradingview_url

def enrich_with_market_cap(
    input_df: pd.DataFrame,
    logger: Optional[logging.Logger] = None
) -> pd.DataFrame:
    """Enrich DataFrame with market cap data based on configuration."""
    if input_df.empty:
        return input_df
    input_df['MARKET_CAP'] = None
    input_df['MARKET_CAP_CR'] = None
    input_df['SOURCE_URL'] = None
    input_df['TRADINGVIEW_LINK'] = None
    symbols_to_fetch = [(row['SYMBOL'], MARKET_CAP_SOURCE) for _, row in input_df.iterrows()]
    # Prepare results dictionary
    results = {}
    # Use ThreadPoolExecutor for concurrent fetching
    with concurrent.futures.ThreadPoolExecutor(max_workers=MARKET_CAP_WORKERS) as executor: # Using MARKET_CAP_WORKERS as specified
        # Submit all tasks
        future_to_symbol = {executor.submit(fetch_market_cap_for_symbol, sym_info, logger): sym_info[0] for sym_info in symbols_to_fetch}
        # Use tqdm to show progress
        for future in tqdm(concurrent.futures.as_completed(future_to_symbol), total=len(future_to_symbol), desc="Fetching Market Cap", unit="symbol"):
            symbol, cap_str, cap_num, src_url, tv_url = future.result()
            results[symbol] = {'MARKET_CAP': cap_str, 'MARKET_CAP_CR': cap_num, 'SOURCE_URL': src_url, 'TRADINGVIEW_LINK': tv_url}
    # Update the DataFrame with results
    for index, row in input_df.iterrows():
        symbol = row['SYMBOL']
        data = results.get(symbol, {})
        input_df.at[index, 'MARKET_CAP'] = data.get('MARKET_CAP')
        input_df.at[index, 'MARKET_CAP_CR'] = data.get('MARKET_CAP_CR')
        input_df.at[index, 'SOURCE_URL'] = data.get('SOURCE_URL')
        input_df.at[index, 'TRADINGVIEW_LINK'] = data.get('TRADINGVIEW_LINK')
    # Filter by market cap
    df_filtered = input_df[input_df['MARKET_CAP_CR'] >= MIN_MARKET_CAP_CR].copy()
    df_filtered = df_filtered.drop(columns=['MARKET_CAP_CR']) # Drop the numeric column used only for filtering
    if logger:
        logger.info(f"Market Cap enrichment completed using {MARKET_CAP_SOURCE}. {len(df_filtered)} stocks (>= ₹{MIN_MARKET_CAP_CR} Cr)")
    return df_filtered
# ============================================================================
# OUTPUT FUNCTIONS
# ============================================================================
def send_csv_to_telegram(csv_path: str, logger: Optional[logging.Logger] = None) -> bool:
    """Send CSV file to Telegram."""
    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(csv_path, "rb") as f:
            files = {"document": (os.path.basename(csv_path), f)}
            data = {"chat_id": TELEGRAM_CHAT_ID}
            resp = requests.post(send_url, data=data, files=files, timeout=30)
        if resp.status_code == 200:
            if logger:
                logger.info(f"Sent {os.path.basename(csv_path)} to Telegram")
            return True
        else:
            if logger:
                logger.warning(f"Telegram API error: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        if logger:
            logger.error(f"Failed to send to Telegram: {e}")
        return False

def save_to_excel(df: pd.DataFrame, excel_path: str, logger: Optional[logging.Logger] = None) -> None:
    """Save DataFrame to Excel with formatting."""
    try:
        # Round numeric columns for better display
        numeric_cols = ['PREV_CLOSE', 'CLOSE_PRICE', 'DELIV_QTY', 'DELIV_PER',
                       '%CHANGE', 'AVG_DELIV_QTY_5D_PRIOR', 'DELIVERY_TIMES', 'DELIVERY_VALUE_LACS',
                       f'RS_{RS_PERIOD}', f'RSI_{RSI_PERIOD}']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').round(2)
        with pd.ExcelWriter(excel_path, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='High Delivery Stocks', index=False)
            # Get workbook and worksheet
            workbook = writer.book
            worksheet = writer.sheets['High Delivery Stocks']
            # Add formats
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'fg_color': '#D7E4BD',
                'border': 1
            })
            number_format = workbook.add_format({'num_format': '#,##0.00'})
            percent_format = workbook.add_format({'num_format': '0.00"%"'})
            # Write headers with format
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
            # Apply number formatting and auto-adjust column widths
            for i, col in enumerate(df.columns):
                column_width = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(i, i, min(column_width, 50))
                # Apply specific number formatting based on column name
                if col in ['PREV_CLOSE', 'CLOSE_PRICE', 'DELIVERY_VALUE_LACS']:
                    worksheet.set_column(i, i, min(column_width, 50), number_format)
                elif col in ['DELIV_PER', '%CHANGE']:
                    worksheet.set_column(i, i, min(column_width, 50), percent_format)
                elif col in [f'RS_{RS_PERIOD}', f'RSI_{RSI_PERIOD}']:
                    worksheet.set_column(i, i, min(column_width, 50), number_format)
        if logger:
            logger.info(f"Saved Excel file to {excel_path}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to save Excel file: {e}")
# ============================================================================
# MAIN FUNCTION
# ============================================================================
def main(argv=None):
    """Main execution function."""
    global MARKET_CAP_SOURCE # Move global declaration to the top of the function
    parser = argparse.ArgumentParser(description="NSE High-Delivery Scanner with Technical Analysis")
    parser.add_argument("--days_back", type=int, help="Number of trading days to look back for data")
    parser.add_argument("--download_dir", type=str, help="Directory to download raw CSV files")
    parser.add_argument("--output_dir", type=str, help="Directory to save output files")
    parser.add_argument("--market_cap_source", choices=['screener', 'yfinance'], help="Source for market cap data")
    args = parser.parse_args(argv)
    # Override config defaults with command-line arguments if provided
    if args.days_back is not None:
        days_back = args.days_back
    else:
        days_back = DEFAULTS["days_back"]
    if args.download_dir is not None:
        download_dir = args.download_dir
    else:
        download_dir = DEFAULTS["download_dir"]
    if args.output_dir is not None:
        output_dir = args.output_dir
    else:
        output_dir = DEFAULTS["output_dir"]
    if args.market_cap_source is not None:
        market_cap_source = args.market_cap_source
    else:
        market_cap_source = MARKET_CAP_SOURCE # Use the value loaded from config
    # Validate command-line arguments if provided
    if args.days_back is not None and args.days_back <= 0:
        print("Error: --days_back must be a positive integer.")
        return
    if args.days_back is not None and args.days_back > 30:
        print("Warning: --days_back is high, this might take a while.")
    # Setup directories
    ensure_dir(download_dir)
    ensure_dir(output_dir)
    ensure_dir(DEFAULTS["cache_dir"])
    # Update global variable if command-line argument overrides it
    if args.market_cap_source:
        MARKET_CAP_SOURCE = market_cap_source # This now works because 'global' was declared at the start
    # Display configuration
    print("" + "="*60)
    print("CONFIGURATION SETTINGS:")
    print("="*60)
    print(f"Minimum Delivery Quantity: {MIN_DELIVERY_QUANTITY:,}")
    print(f"Minimum Percentage Change: {MIN_PERCENT_CHANGE}%")
    print(f"Minimum Delivery Times: {MIN_DELIVERY_TIMES}x")
    print(f"Minimum Market Cap: ₹{MIN_MARKET_CAP_CR} Cr")
    print(f"Market Cap Source: {MARKET_CAP_SOURCE.upper()}")
    print(f"Market Cap Workers: {MARKET_CAP_WORKERS}")
    print(f"RS Period: {RS_PERIOD} days")
    print(f"RSI Period: {RSI_PERIOD} days")
    print("="*60)
    # Get target date
    target_date = get_user_target_date()
    run_date = target_date.strftime("%Y%m%d")
    # Setup logging
    log_path = os.path.join(output_dir, f"scan_{run_date}.log")
    logger = setup_logging(log_path)
    logger.info("="*60)
    logger.info("NSE HIGH-DELIVERY SCANNER WITH TECHNICAL ANALYSIS")
    logger.info("="*60)
    logger.info(f"Target Date: {target_date.strftime('%Y-%m-%d')}")
    logger.info(f"Market Cap Source: {MARKET_CAP_SOURCE.upper()}")
    logger.info(f"Configuration: days_back={days_back}, download_dir={download_dir}, output_dir={output_dir}")
    # Database path
    sqlite_db = os.path.join(download_dir, DEFAULTS["sqlite_db"])
    headers = {"User-Agent": DEFAULTS["user_agent"]}
    # Download bhavcopies
    logger.info(f"Downloading bhavcopies for last {days_back} trading days...")
    downloaded = collect_last_n_trading_days_from_target(
        days_back,
        target_date,
        download_dir,
        headers,
        DEFAULTS["timeout"],
        logger=logger
    )
    # Process and persist data
    for d, path, ok in downloaded:
        if not ok or not path:
            continue
        try:
            cleaned = read_and_clean_bhavcopy(path, logger=logger)
            if not cleaned.empty:
                persist_to_sqlite(cleaned, sqlite_db, logger=logger)
        except Exception as e:
            logger.error(f"Error processing {path}: {e}")
    # Deduplicate database
    dedupe_db_by_symbol_date(sqlite_db, logger=logger)
    # Create snapshot
    target_date_normalized = target_date.date()
    snapshot = create_daily_snapshot_for_date(sqlite_db, target_date_normalized, logger=logger)
    if snapshot.empty:
        logger.error(f"No data available for {target_date_normalized}")
        return
    # Apply filters
    logger.info("Applying filters...")
    filtered = filter_snapshot(snapshot, logger=logger)
    if filtered.empty:
        logger.info("No stocks meet the filtering criteria")
        return
    # Enrich with market cap
    logger.info("Enriching with market cap data...")
    with_market_cap = enrich_with_market_cap(filtered, logger=logger)
    if with_market_cap.empty:
        logger.info("No stocks meet the market cap filter after enrichment.")
        return
    # Add technical indicators
    logger.info("Calculating technical indicators...")
    final_df = add_technical_indicators_to_df(with_market_cap, target_date_normalized, logger=logger)
    # Sort by delivery times (descending)
    final_df = final_df.sort_values('DELIVERY_TIMES', ascending=False)
    # Save outputs
    csv_path = os.path.join(output_dir, f"high_delivery_stocks_{run_date}.csv")
    excel_path = os.path.join(output_dir, f"high_delivery_stocks_{run_date}.xlsx")
    final_df.to_csv(csv_path, index=False)
    logger.info(f"Saved CSV to {csv_path}")
    save_to_excel(final_df, excel_path, logger=logger)
    # Send to Telegram
    send_csv_to_telegram(csv_path, logger=logger)
    # Summary
    total_processed = len(snapshot)
    total_kept = len(final_df)
    logger.info("="*60)
    logger.info("SCAN COMPLETED")
    logger.info("="*60)
    logger.info(f"Total stocks processed: {total_processed}")
    logger.info(f"Stocks meeting criteria: {total_kept}")
    logger.info(f"Output files: {csv_path}, {excel_path}")
    print("" + "="*60)
    print("✅ SCAN COMPLETED SUCCESSFULLY!")
    print("="*60)
    print(f"📊 Results: {total_kept} stocks found")
    print(f"📁 CSV Output: {csv_path}")
    print(f"📁 Excel Output: {excel_path}")
    print("="*60)

if __name__ == "__main__":
    main()