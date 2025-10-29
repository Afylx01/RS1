import pandas as pd

def calculate_rs(df):
    """
    Calculates 55-day Relative Strength (RS) vs NIFTY 50.

    Args:
        df (pd.DataFrame): DataFrame with 'close' and 'nifty_close' columns,
                           indexed by date.

    Returns:
        pd.DataFrame: DataFrame with 'RS55' and 'RS55_Yesterday' columns.
    """
    # Calculate 55-day returns for stock and Nifty
    stock_ret_55 = df['close'] / df['close'].shift(55)
    nifty_ret_55 = df['nifty_close'] / df['nifty_close'].shift(55)

    # Calculate Relative Strength (RS)
    rs_55 = (stock_ret_55 / nifty_ret_55) - 1

    df['RS55'] = rs_55
    df['RS55_Yesterday'] = rs_55.shift(1)

    return df
