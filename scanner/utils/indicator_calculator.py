import pandas_ta as ta
import pandas as pd

def calculate_indicators(df, config):
    """
    Calculate all technical indicators using pandas_ta based on config file
    """
    # RSI
    rsi_period = config.getint('Indicators', 'rsi_period')
    df[f'rsi_{rsi_period}'] = ta.rsi(df['close'], length=rsi_period)

    # Supertrend
    st_period = config.getint('Indicators', 'supertrend_period')
    st_multiplier = config.getfloat('Indicators', 'supertrend_multiplier')
    st = ta.supertrend(df['high'], df['low'], df['close'], length=st_period, multiplier=st_multiplier)
    df['supertrend'] = st[f'SUPERT_{st_period}_{st_multiplier}']

    # EMAs
    ema_short_period = config.getint('Indicators', 'ema_short')
    ema_long_period = config.getint('Indicators', 'ema_long')
    ema_trend_period = config.getint('Indicators', 'ema_trend')
    df[f'ema_{ema_short_period}'] = ta.ema(df['close'], length=ema_short_period)
    df[f'ema_{ema_long_period}'] = ta.ema(df['close'], length=ema_long_period)
    df[f'ema_{ema_trend_period}'] = ta.ema(df['close'], length=ema_trend_period)

    # Highs for lookback periods
    df['high_1d_ago'] = df['high'].shift(1)
    df['high_2d_ago'] = df['high'].shift(2)
    df['high_3d_ago'] = df['high'].shift(3)

    # 252-day high
    lookback_period = config.getint('Indicators', 'lookback_period')
    df[f'max_{lookback_period}d_high'] = df['high'].rolling(window=lookback_period).max().shift(1)

    # EMA 200 increasing count
    trend_count_days = config.getint('Indicators', 'trend_count_days')
    df[f'ema_{ema_trend_period}_increasing'] = df[f'ema_{ema_trend_period}'] > df[f'ema_{ema_trend_period}'].shift(1)
    df[f'count_ema_{ema_trend_period}_increasing'] = df[f'ema_{ema_trend_period}_increasing'].rolling(window=trend_count_days).sum()

    return df
