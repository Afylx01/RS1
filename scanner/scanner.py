import pandas as pd
import configparser
from datetime import datetime, timedelta
from utils.helpers import find_stock_lists, show_date_menu
from utils.data_handler import get_stock_data, process_stock_symbols, NIFTY_SYMBOL
from utils.indicator_calculator import calculate_indicators
from utils.rs_calculator import calculate_relative_strength

def run_scanner():
    """
    Main function to run the stock scanner.
    """
    config = configparser.ConfigParser()
    config.read('scanner/config.ini')

    stock_list_file = find_stock_lists()
    if not stock_list_file:
        return

    try:
        df_stocks = pd.read_csv(stock_list_file)
        # Prioritize 'Symbol' then 'symbol'
        if 'Symbol' in df_stocks.columns:
            symbols = df_stocks['Symbol'].tolist()
        elif 'symbol' in df_stocks.columns:
            symbols = df_stocks['symbol'].tolist()
        else:
            print("CSV file must contain a 'Symbol' or 'symbol' column.")
            return
    except Exception as e:
        print(f"Error reading the stock list file: {e}")
        return

    scan_date = show_date_menu()
    if scan_date is None:
        scan_date = datetime.now()

    # Data fetching period
    start_date = scan_date - timedelta(days=500) # Enough data for 252-day high and other indicators
    end_date = scan_date

    # Fetch Nifty data first
    nifty_data = get_stock_data(NIFTY_SYMBOL, start_date, end_date)
    if nifty_data is None:
        print("Could not fetch NIFTY data. Exiting.")
        return

    nifty_data.columns = [c[0] if isinstance(c, tuple) else c for c in nifty_data.columns]
    nifty_data.columns = [col.lower().replace(' ', '_') for col in nifty_data.columns]
    nifty_data.index = pd.to_datetime(nifty_data.index)


    screened_stocks = []
    failed_symbols = []

    print(f"\nScanning {len(symbols)} stocks...")
    for i, symbol in enumerate(symbols):
        print(f"({i+1}/{len(symbols)}) Processing: {symbol}")

        stock_symbol = process_stock_symbols(symbol)
        stock_data = get_stock_data(stock_symbol, start_date, end_date)

        if stock_data is None:
            failed_symbols.append(symbol)
            continue

        stock_data.columns = [c[0] if isinstance(c, tuple) else c for c in stock_data.columns]
        stock_data.columns = [col.lower().replace(' ', '_') for col in stock_data.columns]
        stock_data.index = pd.to_datetime(stock_data.index)

        # Calculate indicators
        stock_data = calculate_indicators(stock_data, config)

        # Calculate Relative Strength
        rs_period = config.getint('RelativeStrength', 'period')
        stock_data['rs'] = calculate_relative_strength(stock_data, nifty_data, rs_period)

        # Get the latest data for screening
        latest_data = stock_data.iloc[-1]

        # --- Apply Filters ---
        conditions_met = True

        # RSI Filter
        if config.getboolean('Filters', 'enable_rsi'):
            rsi_period = config.getint('Indicators', 'rsi_period')
            rsi_threshold = config.getfloat('Filters', 'rsi_threshold')
            if latest_data[f'rsi_{rsi_period}'] <= rsi_threshold:
                conditions_met = False

        # Supertrend Filter
        if config.getboolean('Filters', 'enable_supertrend'):
            if latest_data['supertrend'] >= latest_data['close']:
                conditions_met = False

        # EMA Filter
        if config.getboolean('Filters', 'enable_ema'):
            ema_short = config.getint('Indicators', 'ema_short')
            ema_long = config.getint('Indicators', 'ema_long')
            if not (latest_data['close'] > latest_data[f'ema_{ema_short}'] and \
                    latest_data['close'] > latest_data[f'ema_{ema_long}'] and \
                    latest_data[f'ema_{ema_short}'] > latest_data[f'ema_{ema_long}']):
                conditions_met = False

        # Price vs 52w High Filter
        lookback = config.getint('Indicators', 'lookback_period')
        price_vs_high_pct = config.getfloat('Filters', 'price_vs_52w_high_pct')
        if latest_data['close'] < (latest_data[f'max_{lookback}d_high'] * price_vs_high_pct):
            conditions_met = False

        # EMA 200 Trend Filter
        ema_trend_period = config.getint('Indicators', 'ema_trend')
        ema_trend_min_days = config.getint('Filters', 'ema_trend_min_days')
        if latest_data[f'count_ema_{ema_trend_period}_increasing'] < ema_trend_min_days:
             conditions_met = False

        # Relative Strength Filter
        if config.getboolean('Filters', 'enable_relative_strength'):
            rs_mode = config.get('RelativeStrength', 'mode')
            rs_threshold = config.getfloat('Filters', 'rs_threshold')
            if rs_mode == 'positive':
                if latest_data['rs'] <= rs_threshold:
                    conditions_met = False
            elif rs_mode == 'crossover':
                rs_yesterday = stock_data['rs'].iloc[-2]
                if not (latest_data['rs'] > rs_threshold and rs_yesterday <= rs_threshold):
                    conditions_met = False

        if conditions_met:
            screened_stocks.append({
                'Symbol': symbol,
                'Close': latest_data['close'],
                f'RSI_{rsi_period}': latest_data[f'rsi_{rsi_period}'],
                'RS': latest_data['rs'],
                'Volume': latest_data['volume']
            })

    # --- Display and Export Results ---
    if screened_stocks:
        results_df = pd.DataFrame(screened_stocks)

        # Add TradingView Link
        results_df['TradingView'] = results_df['Symbol'].apply(lambda s: f"https://www.tradingview.com/chart/?symbol=NSE:{s}")

        print("\n--- Screened Stocks ---")
        print(results_df.to_string(index=False))

        # Export to Excel
        export_choice = input("\nDo you want to export the results to Excel? (y/n): ").lower()
        if export_choice == 'y':
            excel_filename = f"scan_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            try:
                results_df.to_excel(excel_filename, index=False, engine='openpyxl')
                print(f"Results exported to {excel_filename}")
            except Exception as e:
                print(f"Failed to export to Excel. Error: {e}")

    else:
        print("\nNo stocks met the screening criteria.")

    if failed_symbols:
        print("\n--- Failed to Download Data For ---")
        for symbol in failed_symbols:
            print(symbol)

if __name__ == "__main__":
    while True:
        run_scanner()
        another_scan = input("\nDo you want to run another scan? (y/n): ").lower()
        if another_scan != 'y':
            break
