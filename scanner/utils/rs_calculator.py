import pandas as pd

def calculate_relative_strength(stock_data, nifty_data, period=55):
    """
    Calculate RS = (Stock_Return / Benchmark_Return) - 1
    """
    stock_ret = stock_data['close'] / stock_data['close'].shift(period)
    nifty_ret = nifty_data['close'] / nifty_data['close'].shift(period)

    rs = (stock_ret / nifty_ret) - 1

    return rs
