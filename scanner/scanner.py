import pandas as pd
import yfinance as yf
import pandas_ta as ta
import configparser
from datetime import datetime, timedelta, date
import pytz
import holidays
import glob
import os
import pickle
from utils.rs_calculator import calculate_rs
from utils.indicators import calculate_indicators

CACHE_FILE = "data_cache.pkl"
CACHE_EXPIRY_HOURS = 12
INDIA_TZ = pytz.timezone('Asia/Kolkata')
MARKET_CLOSE_TIME = "15:30"
INDIA_HOLIDAYS = holidays.India()
MIN_DATA_DAYS = 260

def print_banner():
    """Prints the application banner."""
    print("==================================================")
    print("📈 Momentum Breakout Stock Scanner v1.1 (RS-Enhanced)")
    print("==================================================")

def select_symbol_list():
    """
    Finds CSV files matching 'ind_nifty*.csv', prompts the user to select one,
    and returns the list of stock symbols.
    """
    csv_files = sorted(glob.glob('ind_nifty*.csv'))
    if not csv_files:
        print("\nError: No 'ind_nifty*.csv' file found.")
        return None
    if len(csv_files) > 1:
        print("\nMultiple symbol lists found. Please choose one:")
        for i, file in enumerate(csv_files): print(f"{i + 1}. {file}")
        while True:
            try:
                choice = int(input(f"Enter choice (1-{len(csv_files)}): ")) - 1
                if 0 <= choice < len(csv_files):
                    selected_file = csv_files[choice]
                    break
                else: print("Invalid choice.")
            except ValueError: print("Invalid input.")
    else: selected_file = csv_files[0]
    print(f"\nUsing symbol list: {selected_file}")
    try:
        df = pd.read_csv(selected_file)
        if 'Symbol' in df.columns: return df['Symbol'].tolist()
        elif 'symbol' in df.columns: return df['symbol'].tolist()
        else: print(f"Error: No 'Symbol' or 'symbol' column in {selected_file}.")
    except Exception as e: print(f"Error reading {selected_file}: {e}")
    return None

def get_data(symbols):
    """
    Downloads and caches historical stock and NIFTY 50 data with robust error handling.
    """
    if os.path.exists(CACHE_FILE) and datetime.now() - datetime.fromtimestamp(os.path.getmtime(CACHE_FILE)) < timedelta(hours=CACHE_EXPIRY_HOURS):
        print("Loading data from cache...")
        with open(CACHE_FILE, 'rb') as f: return pickle.load(f)

    print("Downloading 5 years of historical data...")
    all_stocks = []
    failed_symbols = []

    try:
        nifty_ticker = yf.Ticker('^NSEI')
        nifty_df = nifty_ticker.history(period="5y", interval="1d", auto_adjust=True)
        if nifty_df.empty:
            print("\nError: Could not download NIFTY 50 data. Cannot proceed.")
            return None, None
        nifty_df = nifty_df[['Close']].rename(columns={'Close': 'nifty_close'})
        nifty_df.index = pd.to_datetime(nifty_df.index).tz_localize(None) # FIX: Make tz-naive
    except Exception as e:
        print(f"\nFatal Error downloading NIFTY 50 data: {e}")
        return None, None

    print(f"Downloading data for {len(symbols)} stocks...")
    for i, symbol in enumerate(symbols):
        try:
            print(f"\rDownloading: {i+1}/{len(symbols)} ({symbol})", end="", flush=True)
            stock_ticker = yf.Ticker(symbol + ".NS")
            stock_df = stock_ticker.history(period="5y", interval="1d", auto_adjust=True)
            if not stock_df.empty:
                stock_df.columns = [col.lower() for col in stock_df.columns]
                stock_df['symbol'] = symbol
                all_stocks.append(stock_df)
            else:
                failed_symbols.append(symbol)
        except Exception:
            failed_symbols.append(symbol)

    print("\n")

    if failed_symbols:
        print(f"Warning: Failed to download data for {len(failed_symbols)} symbols: {', '.join(failed_symbols)}")

    if not all_stocks:
        print("\nError: Could not download data for any of the provided stock symbols.")
        return None, None

    stocks_df = pd.concat(all_stocks).reset_index()
    stocks_df.rename(columns={'Date': 'date'}, inplace=True)
    stocks_df['date'] = pd.to_datetime(stocks_df['date']).dt.tz_localize(None)

    with open(CACHE_FILE, 'wb') as f:
        pickle.dump((stocks_df, nifty_df), f)
    print("Data downloaded and cached.")
    return stocks_df, nifty_df

def is_market_closed_for_today():
    now_india = datetime.now(INDIA_TZ)
    if now_india.weekday() >= 5 or now_india.date() in INDIA_HOLIDAYS: return True
    market_close = INDIA_TZ.localize(datetime.strptime(f"{now_india.strftime('%Y-%m-%d')} {MARKET_CLOSE_TIME}", "%Y-%m-%d %H:%M"))
    return now_india > market_close

def get_scan_dates(available_dates):
    print("\n==================================================")
    print("📅 DATE SELECTION MENU")
    print("==================================================")
    print("1. Single Date (e.g., 150124 for 15-Jan-2024)")
    print("2. Date Range (e.g., 010124 150124 for Jan 1–15, 2024)")
    print("3. Latest Available (most recent trading day) ← DEFAULT")
    print("4. Yesterday (previous trading day)")
    if is_market_closed_for_today(): print("5. Today (current day)")
    print("==================================================")
    last_date = available_dates.max()
    while True:
        choice = input("Enter your choice (or press Enter for default): ").strip()
        if not choice: return [last_date]
        parts = choice.split()
        option = parts[0]
        try:
            if option == '1' and len(parts) == 2: return [pd.to_datetime(datetime.strptime(parts[1], '%d%m%y').date())]
            elif option == '2' and len(parts) == 3:
                start = pd.to_datetime(datetime.strptime(parts[1], '%d%m%y').date())
                end = pd.to_datetime(datetime.strptime(parts[2], '%d%m%y').date())
                return available_dates[(available_dates >= start) & (available_dates <= end)].tolist()
            elif option == '3': return [last_date]
            elif option == '4': return [available_dates[available_dates < last_date].max()]
            elif option == '5' and is_market_closed_for_today(): return [pd.to_datetime(date.today())]
            else: print("Invalid input.")
        except ValueError: print("Invalid date format.")

def run_scan(all_stocks_df, nifty_df, symbols, scan_dates, config):
    results = []
    valid_symbols = all_stocks_df['symbol'].unique()
    print(f"\nScanning {len(valid_symbols)} valid stocks...")
    for symbol in valid_symbols:
        stock_df = all_stocks_df[all_stocks_df['symbol'] == symbol].copy().set_index('date')
        if len(stock_df) < MIN_DATA_DAYS: continue
        merged_df = stock_df.join(nifty_df, how='inner')
        merged_df = calculate_indicators(merged_df, config)
        merged_df = calculate_rs(merged_df)
        merged_df.columns = [col.lower() for col in merged_df.columns]
        scan_dates_dt = [pd.to_datetime(d) for d in scan_dates]
        scan_df = merged_df[merged_df.index.isin(scan_dates_dt)]
        for _, row in scan_df.iterrows():
            conditions = []
            if config.getboolean('Filters', 'rs_55d_enabled'):
                rs_mode = config['General']['rs_mode']
                if rs_mode == 'crossover': conditions.append(row['rs55'] > 0 and row['rs55_yesterday'] <= 0)
                elif rs_mode == 'positive': conditions.append(row['rs55'] > 0)
            if config.getboolean('Filters', 'price_action_enabled'):
                prev_idx = merged_df.index.get_loc(row.name) - 1
                if prev_idx > 2:
                    prev_highs = merged_df.iloc[prev_idx-2:prev_idx+1]['high']
                    conditions.append(row['close'] > row['open'] and (row['close'] > prev_highs).all())
            if config.getboolean('Filters', 'supertrend_enabled'): conditions.append(row['close'] > row['supertrend'])
            if config.getboolean('Filters', 'ema_alignment_enabled'): conditions.append(row['close'] > row['ema_50'] and row['close'] > row['ema_200'] and row['ema_50'] > row['ema_200'])
            if config.getboolean('Filters', 'near_52w_high_enabled'): conditions.append(row['close'] >= config.getfloat('Thresholds', 'near_52w_high_ratio') * row['52w_high'])
            if config.getboolean('Filters', 'sma200_slope_enabled'): conditions.append(row['sma200_rising_days'] >= config.getint('Thresholds', 'sma200_rising_days'))
            if config.getboolean('Filters', 'daily_rsi_enabled'): conditions.append(row[f'rsi_{config["Thresholds"]["daily_rsi_period"]}'] > config.getfloat('Thresholds', 'daily_rsi_min'))
            if config.getboolean('Filters', 'weekly_rsi_enabled'): conditions.append(row['weekly_rsi'] > config.getfloat('Thresholds', 'daily_rsi_min'))
            if all(conditions):
                results.append({'date': row.name.strftime('%Y-%m-%d'), 'symbol': symbol, 'close': row['close'], 'RS55': row['rs55'], 'RS55_Yesterday': row['rs55_yesterday']})
    return pd.DataFrame(results)

def save_results_to_excel(df):
    if df.empty: return
    df['TradingView Link'] = df['symbol'].apply(lambda s: f'=HYPERLINK("https://www.tradingview.com/chart/?symbol=NSE:{s}", "View Chart")')
    filename = f"scan_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    try:
        writer = pd.ExcelWriter(filename, engine='openpyxl')
        df.to_excel(writer, index=False, sheet_name='Breakouts')
        worksheet = writer.sheets['Breakouts']
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length: max_length = len(cell.value)
                except: pass
            adjusted_width = (max_length + 2)
            worksheet.column_dimensions[column_letter].width = adjusted_width
        writer.close()
        print(f"\nResults saved to {filename}")
    except Exception as e: print(f"\nError saving results to Excel: {e}")

def main():
    print_banner()
    config = configparser.ConfigParser()
    config.read('scanner/config.ini')
    symbols = select_symbol_list()
    if not symbols: return
    all_stocks_df, nifty_df = get_data(symbols)
    if all_stocks_df is None or all_stocks_df.empty:
        print("\nCould not retrieve any valid stock data. Exiting.")
        return
    available_dates = nifty_df.index
    while True:
        scan_dates = get_scan_dates(available_dates)
        if not scan_dates: continue
        results_df = run_scan(all_stocks_df, nifty_df, symbols, scan_dates, config)
        if not results_df.empty:
            print("\n📈 Breakout Stocks Found:")
            print(results_df.to_string())
            save_results_to_excel(results_df)
        else:
            print("\nNo breakout stocks found for the selected date(s).")
        another_scan = input("\nScan another date range? (y/n): ").strip().lower()
        if another_scan != 'y': break

if __name__ == '__main__':
    main()
