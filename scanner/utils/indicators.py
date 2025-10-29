import pandas as pd
import pandas_ta as ta

def calculate_indicators(df, config):
    """
    Calculates all technical indicators based on the config.

    Args:
        df (pd.DataFrame): DataFrame with OHLCV data for a single stock.
        config (configparser.ConfigParser): Configuration object.

    Returns:
        pd.DataFrame: DataFrame with all indicator columns.
    """
    # Ensure columns are lowercase for consistency
    df.columns = [col.lower() for col in df.columns]

    # Daily RSI
    if config.getboolean('Filters', 'daily_rsi_enabled'):
        df.ta.rsi(length=int(config['Thresholds']['daily_rsi_period']), append=True)

    # Weekly RSI
    if config.getboolean('Filters', 'weekly_rsi_enabled'):
        # Resample to weekly data
        weekly_df = df.resample('W-FRI').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        weekly_rsi = weekly_df.ta.rsi(length=int(config['Thresholds']['daily_rsi_period']))
        # Map weekly RSI back to daily data
        df['weekly_rsi'] = weekly_rsi.reindex(df.index, method='ffill')

    # EMAs
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)

    # SMA for slope calculation
    sma200 = df.ta.sma(length=200)

    # SMA 200 Slope
    sma200_rising = (sma200 > sma200.shift(1)).rolling(window=int(config['Thresholds']['sma200_window'])).sum()
    df['sma200_rising_days'] = sma200_rising

    # Supertrend
    st_length = int(config['Thresholds']['supertrend_length'])
    st_multiplier = float(config['Thresholds']['supertrend_multiplier'])
    supertrend_df = df.ta.supertrend(length=st_length, multiplier=st_multiplier)
    supertrend_col_name = f'SUPERT_{st_length}_{st_multiplier}.0'
    if supertrend_col_name in supertrend_df.columns:
        df['supertrend'] = supertrend_df[supertrend_col_name]
    else:
        df['supertrend'] = supertrend_df.iloc[:, 0]

    # 52-week high
    df['52w_high'] = df['high'].rolling(window=252).max()

    return df
