import os
import glob
from datetime import datetime, time
import pytz
import holidays

def find_stock_lists():
    """
    Search for files starting with 'ind_nifty'
    Prioritize column names: 'Symbol' then 'symbol'
    Let user choose from available files
    """
    files = glob.glob('ind_nifty*.csv')
    if not files:
        print("No stock list files found (e.g., 'ind_nifty*.csv').")
        return None

    print("Available stock lists:")
    for i, file in enumerate(files):
        print(f"{i + 1}. {file}")

    while True:
        try:
            choice = int(input("Enter the number of the file to use: "))
            if 1 <= choice <= len(files):
                return files[choice - 1]
            else:
                print("Invalid choice. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")


def is_market_closed():
    """
    Check if current time is past 3:30 PM IST
    Returns True if market is closed
    """
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    market_close_time = time(15, 30)
    return now.time() > market_close_time or holidays.India(years=now.year).get(now.date()) is not None

def show_date_menu():
    """
    Display interactive date selection menu
    Options:
    1. Single Date (ddmmyy format)
    2. Latest Available (default)
    3. Yesterday
    4. Today (if market closed)
    """
    print("\nSelect a date for the scan:")
    print("1. Enter a specific date (ddmmyy)")
    print("2. Use the latest available data (default)")
    print("3. Use yesterday's data")
    print("4. Use today's data (if market is closed)")

    while True:
        choice = input("Enter your choice (1-4): ")
        if choice == '1':
            while True:
                date_str = input("Enter the date in ddmmyy format: ")
                try:
                    return datetime.strptime(date_str, '%d%m%y')
                except ValueError:
                    print("Invalid date format. Please use ddmmyy.")
        elif choice == '2':
            return None  # Represents latest available data
        elif choice == '3':
            return datetime.now() - pd.Timedelta(days=1)
        elif choice == '4':
            if is_market_closed():
                return datetime.now()
            else:
                print("Market is still open. Defaulting to yesterday's data.")
                return datetime.now() - pd.Timedelta(days=1)
        else:
            print("Invalid choice. Please enter a number between 1 and 4.")
