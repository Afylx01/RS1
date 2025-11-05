"""
Advanced Trading Scanner & Position Manager
Merges HHHL (Higher High Higher Low) Trend Analysis with
ADX, ATR, RS55, RSI, EMA Strategy for Stock Universe

Author: Trading System (Merged by Gemini)
Version: 3.0
"""

import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
import os
import warnings
from typing import Dict, List, Tuple, Optional
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import glob
import re

# Imports from HHHL Scanner
from scipy.signal import find_peaks
from scipy.stats import linregress

warnings.filterwarnings('ignore')


class ColumnMapper:
    """Handle different column name variations"""
    
    @staticmethod
    def find_column(df: pd.DataFrame, possible_names: List[str]) -> Optional[str]:
        """Find column name from list of possible variations"""
        df_columns_lower = {col.lower(): col for col in df.columns}
        
        for name in possible_names:
            if name.lower() in df_columns_lower:
                return df_columns_lower[name.lower()]
        return None
    
    @staticmethod
    def get_symbol_column(df: pd.DataFrame) -> Optional[str]:
        """Get symbol column name"""
        return ColumnMapper.find_column(df, ['SYMBOL', 'Symbol', 'symbol', 'SYMBOLS'])
    
    @staticmethod
    def get_rs_column(df: pd.DataFrame) -> Optional[str]:
        """Get RS column name"""
        return ColumnMapper.find_column(df, ['RS_55', 'RS_Today', 'RS55', 'RS', 'rs_55'])
    
    @staticmethod
    def get_rsi_column(df: pd.DataFrame) -> Optional[str]:
        """Get RSI column name"""
        return ColumnMapper.find_column(df, ['RSI_14', 'RSI14', 'RSI', 'rsi_14', 'rsi'])
    
    @staticmethod
    def get_date_column(df: pd.DataFrame) -> Optional[str]:
        """Get date column name"""
        return ColumnMapper.find_column(df, ['DATE', 'Date', 'date', 'DATES'])
    
    @staticmethod
    def parse_date(date_str: str) -> datetime:
        """Parse date from various formats"""
        if pd.isna(date_str):
            return datetime.now()
        
        # Convert to string if it's not
        date_str = str(date_str)
        
        # Try different date formats
        date_formats = [
            '%d-%m-%Y %H:%M:%S',  # 24-10-2025 00:00:00
            '%Y-%m-%d',           # 2025-10-24
            '%Y-%m-%d %H:%M:%S',  # 2025-10-24 00:00:00
            '%d/%m/%Y',           # 24/10/2025
            '%Y/%m/%d',           # 2025/10/24
            '%d-%m-%Y',           # 24-10-2025
        ]
        
        for fmt in date_formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except:
                continue
        
        # If all formats fail, return current date
        print(f"⚠️ Could not parse date: {date_str}, using current date")
        return datetime.now()


class TechnicalIndicators:
    """Calculate technical indicators matching TradingView's Pine Script"""
    
    @staticmethod
    def calculate_ema(data: pd.Series, period: int) -> pd.Series:
        """Calculate EMA using Pine Script method"""
        return data.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Calculate Average True Range (ATR)"""
        close_prev = close.shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close_prev)
        tr3 = abs(low - close_prev)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        
        return atr
    
    @staticmethod
    def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI if not provided in data"""
        delta = data.diff()
        gain = (delta.where(delta > 0, 0)).fillna(0)
        loss = (-delta.where(delta < 0, 0)).fillna(0)
        
        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    @staticmethod
    def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> Dict[str, pd.Series]:
        """Calculate ADX, +DI, -DI"""
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        # When both are positive, keep only the larger
        mask = (plus_dm > 0) & (minus_dm > 0)
        plus_dm[mask & (plus_dm < minus_dm)] = 0
        minus_dm[mask & (minus_dm < plus_dm)] = 0
        
        # Calculate ATR for ADX
        close_prev = close.shift(1)
        tr1 = high - low
        tr2 = abs(high - close_prev)
        tr3 = abs(low - close_prev)
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        
        # Smooth DM
        plus_dm_smooth = plus_dm.ewm(span=period, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(span=period, adjust=False).mean()
        
        # Calculate DI
        plus_di = 100 * plus_dm_smooth / atr
        minus_di = 100 * minus_dm_smooth / atr
        
        # Calculate DX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        dx = dx.fillna(0)
        
        # Calculate ADX
        adx = dx.ewm(span=period, adjust=False).mean()
        
        return {
            'ADX': adx,
            'PLUS_DI': plus_di,
            'MINUS_DI': minus_di
        }


class HHHLScanner:
    """
    Analyzes price structure for Higher Highs and Higher Lows (HHHL).
    Integrated from HHHL Sccanner.py
    """
    def __init__(self, lookback_days=60, min_swing_pct=2.0):
        """
        Initialize Higher High Higher Low (HHHL) scanner
        
        Args:
            lookback_days: Number of days to analyze for trend
            min_swing_pct: Minimum percentage move to qualify as a swing
        """
        self.lookback_days = lookback_days
        self.min_swing_pct = min_swing_pct
        
    def download_data(self, symbol, period='3mo'):
        """Download historical data for a symbol (used as fallback)"""
        try:
            stock = yf.Ticker(symbol)
            df = stock.history(period=period)
            if len(df) > 0:
                return df
            else:
                return None
        except Exception as e:
            print(f"Error downloading {symbol}: {e}")
            return None
    
    def identify_swing_points(self, prices, dates):
        """
        Identify swing highs and lows using scipy
        
        Returns:
            Dictionary containing swing highs and lows with their indices and values
        """
        # Calculate dynamic prominence based on price volatility
        if len(prices) == 0:
            return {'highs': {'indices': [], 'values': [], 'dates': []},
                    'lows': {'indices': [], 'values': [], 'dates': []}}

        price_std = np.std(prices)
        price_mean = np.mean(prices)
        prominence = (self.min_swing_pct / 100) * price_mean
        
        # Find peaks (swing highs)
        peaks, peak_properties = find_peaks(
            prices,
            prominence=prominence,
            distance=3,  # Minimum 3 days between peaks
            width=1
        )
        
        # Find troughs (swing lows)
        troughs, trough_properties = find_peaks(
            -prices,
            prominence=prominence,
            distance=3,
            width=1
        )
        
        # Create swing points dictionary
        swing_points = {
            'highs': {
                'indices': peaks,
                'values': prices[peaks] if len(peaks) > 0 else np.array([]),
                'dates': dates[peaks] if len(peaks) > 0 else [],
                'prominences': peak_properties.get('prominences', []) if len(peaks) > 0 else []
            },
            'lows': {
                'indices': troughs,
                'values': prices[troughs] if len(troughs) > 0 else np.array([]),
                'dates': dates[troughs] if len(troughs) > 0 else [],
                'prominences': trough_properties.get('prominences', []) if len(troughs) > 0 else []
            }
        }
        
        return swing_points
    
    def analyze_trend_structure(self, swing_points):
        """
        Analyze if the price structure shows HHHL (uptrend) or LLHL (downtrend)
        
        Returns:
            Dictionary with trend analysis results
        """
        highs = swing_points['highs']['values']
        lows = swing_points['lows']['values']
        
        # Initialize default values
        result = {
            'trend': 'UNDEFINED',
            'strength': 0,
            'higher_highs': 0,
            'lower_highs': 0,
            'higher_lows': 0,
            'lower_lows': 0,
            'total_highs': len(highs),
            'total_lows': len(lows),
            'high_slope': 0,
            'low_slope': 0,
            'uptrend_score': 0,
            'downtrend_score': 0
        }
        
        # Need at least 2 points to determine trend
        if len(highs) < 2 or len(lows) < 2:
            return result
        
        # Analyze higher highs and higher lows
        higher_highs = 0
        lower_highs = 0
        higher_lows = 0
        lower_lows = 0
        
        # Check consecutive highs
        for i in range(1, len(highs)):
            if highs[i] > highs[i-1] * 1.001:  # Add small threshold to avoid noise
                higher_highs += 1
            else:
                lower_highs += 1
        
        # Check consecutive lows
        for i in range(1, len(lows)):
            if lows[i] > lows[i-1] * 1.001:  # Add small threshold to avoid noise
                higher_lows += 1
            else:
                lower_lows += 1
        
        # Calculate trend scores
        total_comparisons = (len(highs) - 1) + (len(lows) - 1)
        if total_comparisons > 0:
            uptrend_score = (higher_highs + higher_lows) / total_comparisons
            downtrend_score = (lower_highs + lower_lows) / total_comparisons
        else:
            uptrend_score = 0
            downtrend_score = 0
        
        # Determine primary trend
        if uptrend_score > 0.6:
            trend = 'UPTREND'
            strength = uptrend_score
        elif downtrend_score > 0.6:
            trend = 'DOWNTREND'
            strength = downtrend_score
        else:
            trend = 'SIDEWAYS'
            strength = 1 - abs(uptrend_score - downtrend_score)
        
        # Calculate trend line slopes using linear regression
        high_slope = 0
        low_slope = 0
        
        try:
            if len(highs) >= 2:
                x_highs = swing_points['highs']['indices']
                if len(x_highs) >= 2:
                    slope_h, _, r_value_h, _, _ = linregress(x_highs, highs)
                    high_slope = slope_h
            
            if len(lows) >= 2:
                x_lows = swing_points['lows']['indices']
                if len(x_lows) >= 2:
                    slope_l, _, r_value_l, _, _ = linregress(x_lows, lows)
                    low_slope = slope_l
        except Exception as e:
            # Squelch linregress errors on flat data
            pass
        
        # Update result dictionary
        result.update({
            'trend': trend,
            'strength': strength * 100,  # Convert to percentage
            'higher_highs': higher_highs,
            'lower_highs': lower_highs,
            'higher_lows': higher_lows,
            'lower_lows': lower_lows,
            'total_highs': len(highs),
            'total_lows': len(lows),
            'high_slope': high_slope,
            'low_slope': low_slope,
            'uptrend_score': uptrend_score * 100,
            'downtrend_score': downtrend_score * 100
        })
        
        return result
    
    def calculate_momentum(self, prices):
        """Calculate momentum indicators using scipy"""
        result = {
            'roc_20': 0,
            'macd_histogram': 0,
            'trend_r2': 0,
            'trend_slope': 0
        }
        
        try:
            # Rate of change
            if len(prices) >= 20:
                result['roc_20'] = ((prices[-1] - prices[-20]) / prices[-20]) * 100
            
            # Moving average convergence
            if len(prices) >= 26:
                price_series = pd.Series(prices)
                ema_12 = price_series.ewm(span=12, adjust=False).mean().iloc[-1]
                ema_26 = price_series.ewm(span=26, adjust=False).mean().iloc[-1]
                macd = ema_12 - ema_26
                signal = price_series.ewm(span=9, adjust=False).mean().iloc[-1]
                result['macd_histogram'] = macd - signal
            
            # Trend strength using linear regression
            if len(prices) >= 2:
                x = np.arange(len(prices))
                slope, intercept, r_value, _, _ = linregress(x, prices)
                result['trend_r2'] = (r_value ** 2) * 100  # R-squared as percentage
                result['trend_slope'] = slope
        except Exception as e:
            # Squelch errors
            pass
        
        return result
    
    def scan_single_stock(self, symbol: str, df_data: Optional[pd.DataFrame] = None):
        """
        Scan a single stock for HHHL pattern.
        Accepts a DataFrame to avoid re-downloading.
        """
        try:
            # Download data if not provided
            if df_data is None:
                df = self.download_data(symbol)
            else:
                df = df_data.copy() # Use provided data

            if df is None or len(df) < self.lookback_days:
                return None
            
            # Use recent data for analysis
            df_analysis = df.tail(self.lookback_days).copy()
            prices = df_analysis['Close'].values
            dates = df_analysis.index.values
            
            # Check if we have valid price data
            if len(prices) == 0 or np.isnan(prices).any():
                return None
            
            # Identify swing points
            swing_points = self.identify_swing_points(prices, dates)
            
            # Analyze trend structure
            trend_analysis = self.analyze_trend_structure(swing_points)
            
            # Calculate momentum
            momentum = self.calculate_momentum(prices)
            
            # Calculate price position
            current_price = prices[-1]
            min_price = np.min(prices)
            max_price = np.max(prices)
            
            if max_price > min_price:
                price_position = ((current_price - min_price) / (max_price - min_price)) * 100
            else:
                price_position = 50  # Default to middle if no range
            
            # Get last high and low values safely
            last_high = swing_points['highs']['values'][-1] if len(swing_points['highs']['values']) > 0 else current_price
            last_low = swing_points['lows']['values'][-1] if len(swing_points['lows']['values']) > 0 else current_price
            
            # Compile results
            result = {
                'symbol': symbol,
                'current_price': current_price,
                'trend': trend_analysis['trend'],
                'trend_strength': trend_analysis['strength'],
                'higher_highs': trend_analysis['higher_highs'],
                'higher_lows': trend_analysis['higher_lows'],
                'lower_highs': trend_analysis['lower_highs'],
                'lower_lows': trend_analysis['lower_lows'],
                'total_swings': trend_analysis['total_highs'] + trend_analysis['total_lows'],
                'price_position': price_position,
                'roc_20': momentum['roc_20'],
                'trend_r2': momentum['trend_r2'],
                'last_high': last_high,
                'last_low': last_low,
                'swing_points': swing_points
                # 'df' removed to avoid conflicts
            }
            
            return result
            
        except Exception as e:
            print(f"Error scanning {symbol} in HHHL: {e}")
            return None


class TradingScanner:
    """Main trading scanner implementing the mechanical strategy"""
    
    def __init__(self, portfolio_value: float = 50000, risk_per_trade: float = 0.03):
        self.portfolio_value = portfolio_value
        self.risk_per_trade = risk_per_trade
        self.max_positions = 5
        self.tech = TechnicalIndicators()
        self.mapper = ColumnMapper()
        
        # Initialize the HHHL Scanner
        self.hhhl_scanner = HHHLScanner(lookback_days=60, min_swing_pct=2.0)
        
        self.current_positions = []
        self.atr_multiplier = 2.0
        self.replacement_threshold = 0.05
        self.scan_date = None
        
    def read_input_data(self, excel_file: str) -> Tuple[pd.DataFrame, datetime]:
        """Read input Excel file and extract data with flexible column names"""
        try:
            df_input = pd.read_excel(excel_file)
            print(f"✅ Loaded {len(df_input)} rows from Excel file")
            
            # Get column names
            symbol_col = self.mapper.get_symbol_column(df_input)
            if not symbol_col:
                print("❌ Could not find SYMBOL column")
                return pd.DataFrame(), datetime.now()
            
            # Get date from first row if available
            date_col = self.mapper.get_date_column(df_input)
            if date_col and len(df_input) > 0:
                scan_date = self.mapper.parse_date(df_input[date_col].iloc[0])
            else:
                scan_date = datetime.now()
            
            print(f"📅 Scan date: {scan_date.strftime('%Y-%m-%d')}")
            
            return df_input, scan_date
            
        except Exception as e:
            print(f"❌ Error reading Excel: {e}")
            return pd.DataFrame(), datetime.now()
    
    def fetch_market_data(self, symbols: List[str], end_date: datetime, days_back: int = 400) -> Dict[str, pd.DataFrame]:
        """Fetch market data for all symbols up to specified date"""
        start_date = end_date - timedelta(days=days_back)
        
        market_data = {}
        benchmark_data = None
        
        print("\n" + "="*60)
        print("📊 FETCHING MARKET DATA")
        print("="*60)
        
        # Fetch benchmark (NIFTY50) first
        try:
            benchmark = yf.Ticker("^NSEI")
            benchmark_data = benchmark.history(start=start_date, end=end_date + timedelta(days=1))
            print("✅ Benchmark (NIFTY50) data fetched")
        except:
            print("⚠️ Could not fetch benchmark data")
        
        # Fetch individual stock data
        total = len(symbols)
        for idx, symbol in enumerate(symbols, 1):
            ticker_symbol = f"{symbol}.NS" if not symbol.endswith('.NS') else symbol
            print(f"[{idx}/{total}] Fetching {symbol}...", end="")
            
            try:
                ticker = yf.Ticker(ticker_symbol)
                df = ticker.history(start=start_date, end=end_date + timedelta(days=1))
                
                if not df.empty:
                    market_data[symbol] = df
                    print(" ✅")
                else:
                    print(" ❌ No data")
            except Exception as e:
                print(f" ❌ Error: {str(e)[:30]}")
        
        return market_data, benchmark_data
    
    def calculate_indicators(self, symbol: str, data: pd.DataFrame, 
                           rs_value: Optional[float] = None, 
                           rsi_value: Optional[float] = None,
                           scan_date: Optional[datetime] = None) -> Dict:
        """Calculate technical indicators for a symbol"""
        
        if scan_date:
            # Filter data up to scan date
            scan_date_str = scan_date.strftime('%Y-%m-%d')
            data = data[data.index <= scan_date_str]
        
        if data.empty or len(data) < 20:
            return None
        
        # Basic indicators
        data['EMA_21'] = self.tech.calculate_ema(data['Close'], 21)
        data['EMA_200'] = self.tech.calculate_ema(data['Close'], 200)
        data['ATR_14'] = self.tech.calculate_atr(data['High'], data['Low'], data['Close'], 14)
        
        # Use provided RSI or calculate
        if rsi_value is not None and not pd.isna(rsi_value):
            data['RSI_14'] = rsi_value
        else:
            data['RSI_14'] = self.tech.calculate_rsi(data['Close'], 14)
        
        # ADX and DI
        adx_data = self.tech.calculate_adx(data['High'], data['Low'], data['Close'], 14)
        data['ADX'] = adx_data['ADX']
        data['PLUS_DI'] = adx_data['PLUS_DI']
        data['MINUS_DI'] = adx_data['MINUS_DI']
        
        # Volume metrics
        data['AVG_VOL_20'] = data['Volume'].rolling(window=20).mean()
        data['VOL_RATIO'] = data['Volume'] / data['AVG_VOL_20']
        
        # 10-day high for breakout detection
        data['HIGH_10D'] = data['High'].rolling(window=10).max()
        
        # Get latest values
        latest = data.iloc[-1]
        
        # Use provided RS or default
        if rs_value is not None and not pd.isna(rs_value):
            # Normalize RS to 0-0.3 range if needed
            if rs_value > 1:
                rs_normalized = min(0.3, rs_value / 100)  # Assume percentage
            else:
                rs_normalized = min(0.3, rs_value)
        else:
            rs_normalized = 0
        
        return {
            'symbol': symbol,
            'close': latest['Close'],
            'ema_21': latest['EMA_21'],
            'ema_200': latest['EMA_200'],
            'atr_14': latest['ATR_14'],
            'rsi_14': rsi_value if rsi_value is not None else latest['RSI_14'],
            'adx': latest['ADX'],
            'plus_di': latest['PLUS_DI'],
            'minus_di': latest['MINUS_DI'],
            'volume': latest['Volume'],
            'avg_vol_20': latest['AVG_VOL_20'],
            'vol_ratio': latest['VOL_RATIO'],
            'high_10d': latest['HIGH_10D'],
            'rs55': rs_normalized,
            'above_ema21': latest['Close'] > latest['EMA_21'],
            'above_ema200': latest['Close'] > latest['EMA_200'],
            'trend_up': latest['PLUS_DI'] > latest['MINUS_DI'],
            'breakout': latest['Close'] > latest['HIGH_10D']
        }
    
    def calculate_score(self, indicators: Dict) -> float:
        """Calculate composite score for ranking"""
        
        # Normalize components
        norm_rs = min(indicators['rs55'] / 0.3, 1.0) if indicators['rs55'] else 0
        norm_adx = min(indicators['adx'] / 50, 1.0) if indicators['adx'] else 0
        norm_rsi = max(0, min(1, (indicators['rsi_14'] - 40) / 60)) if indicators['rsi_14'] else 0
        norm_vol = min(indicators['vol_ratio'] / 2, 1.0) if indicators['vol_ratio'] else 0
        
        # Weighted score
        score = (0.45 * norm_rs + 
                0.30 * norm_adx + 
                0.15 * norm_rsi + 
                0.10 * norm_vol)
        
        return round(score, 4)
    
    def apply_filters(self, indicators: Dict) -> bool:
        """Apply trend filters for tradeable stocks"""
        
        if not indicators:
            return False
        
        # --- MERGED FILTER 1: HHHL TREND ---
        # Only proceed if the HHHL trend is UPTREND
        if indicators.get('hhhl_trend', 'UNDEFINED') != 'UPTREND':
            return False
        
        # --- ORIGINAL FILTERS ---
        
        # Basic filters
        if not indicators.get('above_ema200', False):
            return False
        
        if indicators.get('adx', 0) < 20:
            return False
        
        if not indicators.get('trend_up', False):  # +DI > -DI
            return False
        
        if indicators.get('rsi_14', 0) < 40:
            return False
        
        return True
    
    def check_entry_signals(self, indicators: Dict) -> Dict[str, bool]:
        """Check for entry signals (breakout or pullback)"""
        
        signals = {
            'breakout_entry': False,
            'pullback_entry': False,
            'entry_type': None,
            'entry_strength': 0
        }
        
        if not indicators:
            return signals
        
        # Breakout entry conditions
        if (indicators.get('breakout', False) and 
            indicators.get('above_ema21', False) and 
            indicators.get('adx', 0) >= 25 and 
            indicators.get('trend_up', False) and 
            indicators.get('rsi_14', 100) < 80):
            
            signals['breakout_entry'] = True
            signals['entry_type'] = 'BREAKOUT'
            signals['entry_strength'] = 3  # Strong signal
        
        # Pullback entry conditions
        elif (indicators.get('above_ema21', False) and 
              abs(indicators.get('close', 0) - indicators.get('ema_21', 0)) <= indicators.get('atr_14', float('inf')) and
              indicators.get('adx', 0) >= 20 and 
              indicators.get('trend_up', False) and 
              indicators.get('rsi_14', 0) >= 45):
            
            signals['pullback_entry'] = True
            signals['entry_type'] = 'PULLBACK'
            signals['entry_strength'] = 2  # Medium signal
        
        return signals
    
    def calculate_position_size(self, entry_price: float, atr: float) -> Dict:
        """Calculate position size based on risk management rules"""
        
        risk_amount = self.portfolio_value * self.risk_per_trade
        stop_distance = atr * self.atr_multiplier
        stop_price = entry_price - stop_distance
        
        # Calculate shares
        shares = int(risk_amount / stop_distance) if stop_distance > 0 else 0
        
        # Calculate position value
        position_value = shares * entry_price
        
        # Apply position size cap (max 25% of portfolio)
        max_position = self.portfolio_value * 0.25
        if position_value > max_position:
            shares = int(max_position / entry_price)
            position_value = shares * entry_price
        
        # Calculate targets
        target_1r = entry_price + stop_distance
        target_2r = entry_price + (stop_distance * 2)
        target_3r = entry_price + (stop_distance * 3)
        
        return {
            'shares': shares,
            'position_value': round(position_value, 2),
            'stop_price': round(stop_price, 2),
            'stop_distance': round(stop_distance, 2),
            'target_1r': round(target_1r, 2),
            'target_2r': round(target_2r, 2),
            'target_3r': round(target_3r, 2),
            'risk_amount': round(risk_amount, 2),
            'position_pct': round((position_value / self.portfolio_value) * 100, 2) if self.portfolio_value > 0 else 0
        }
    
    def scan_universe(self, excel_file: str = None) -> pd.DataFrame:
        """Main scanning function"""
        
        # Read input data
        df_input, scan_date = self.read_input_data(excel_file)
        self.scan_date = scan_date
        
        if df_input.empty:
            print("❌ No valid input data")
            return pd.DataFrame()
        
        # Get column names
        symbol_col = self.mapper.get_symbol_column(df_input)
        rs_col = self.mapper.get_rs_column(df_input)
        rsi_col = self.mapper.get_rsi_column(df_input)
        
        # Extract symbols
        symbols = df_input[symbol_col].dropna().unique().tolist()
        print(f"📊 Processing {len(symbols)} symbols")
        
        # Create lookup dictionaries for RS and RSI values
        rs_lookup = {}
        rsi_lookup = {}
        
        if rs_col:
            rs_lookup = dict(zip(df_input[symbol_col], df_input[rs_col]))
        if rsi_col:
            rsi_lookup = dict(zip(df_input[symbol_col], df_input[rsi_col]))
        
        # Fetch market data
        market_data, benchmark_data = self.fetch_market_data(symbols, scan_date)
        
        # Calculate indicators for all stocks
        results = []
        
        print("\n" + "="*60)
        print("📈 CALCULATING INDICATORS & SCORES")
        print("="*60)
        
        for idx, symbol in enumerate(symbols, 1):
            if symbol not in market_data:
                continue
            
            print(f"[{idx}/{len(symbols)}] Processing {symbol}...", end="")
            
            try:
                stock_data_full = market_data[symbol]

                # --- MERGED LOGIC 1: RUN HHHL SCAN ---
                hhhl_result = self.hhhl_scanner.scan_single_stock(symbol, df_data=stock_data_full)
                
                if not hhhl_result:
                    hhhl_trend = 'UNDEFINED'
                    hhhl_strength = 0
                else:
                    hhhl_trend = hhhl_result.get('trend', 'UNDEFINED')
                    hhhl_strength = hhhl_result.get('trend_strength', 0)

                # --- MERGED LOGIC 2: CALCULATE TRADITIONAL INDICATORS ---
                rs_value = rs_lookup.get(symbol, None)
                rsi_value = rsi_lookup.get(symbol, None)
                
                indicators = self.calculate_indicators(
                    symbol, 
                    stock_data_full, # Use the full data
                    rs_value=rs_value,
                    rsi_value=rsi_value,
                    scan_date=scan_date
                )
                
                if not indicators:
                    print(" ⏭️ Skipped (insufficient data for indicators)")
                    continue
                
                # --- MERGED LOGIC 3: COMBINE RESULTS ---
                indicators['hhhl_trend'] = hhhl_trend
                indicators['hhhl_strength'] = hhhl_strength
                
                # Calculate score
                score = self.calculate_score(indicators)
                indicators['score'] = score
                
                # Apply filters (now includes HHHL filter)
                tradeable = self.apply_filters(indicators)
                indicators['tradeable'] = tradeable
                
                # Check entry signals
                if tradeable:
                    signals = self.check_entry_signals(indicators)
                    indicators.update(signals)
                else:
                    indicators['entry_type'] = None
                    indicators['entry_strength'] = 0
                
                # Calculate position sizing if entry signal exists
                if indicators.get('entry_type'):
                    position_info = self.calculate_position_size(
                        indicators['close'], 
                        indicators['atr_14']
                    )
                    indicators.update(position_info)
                
                results.append(indicators)
                
                if not tradeable and hhhl_trend != 'UPTREND':
                    print(f" ⏭️ Skipped (HHHL: {hhhl_trend})")
                elif not tradeable:
                    print(" ⏭️ Skipped (Filters)")
                else:
                    print(" ✅")
                
            except Exception as e:
                print(f" ❌ Error: {str(e)[:30]}")
        
        # Create DataFrame and sort by score
        df_results = pd.DataFrame(results)
        if not df_results.empty:
            df_results = df_results.sort_values('score', ascending=False)
        
        return df_results
    
    def generate_trading_signals(self, df_scan: pd.DataFrame) -> pd.DataFrame:
        """Generate final trading signals and recommendations"""
        
        if df_scan.empty:
            return pd.DataFrame()
        
        # Filter tradeable stocks (this now includes HHHL check)
        df_tradeable = df_scan[df_scan['tradeable'] == True].copy()
        
        if df_tradeable.empty:
            return pd.DataFrame()
        
        # Get top 5 by score
        df_top5 = df_tradeable.head(5).copy()
        
        # Add ranking
        df_top5['rank'] = range(1, len(df_top5) + 1)
        
        # Add recommendation
        def get_recommendation(row):
            if row.get('entry_type') == 'BREAKOUT':
                return '🔥 STRONG BUY (Breakout)'
            elif row.get('entry_type') == 'PULLBACK':
                return '📈 BUY (Pullback)'
            elif row['score'] > 0.6:
                return '👀 WATCH (High Score)'
            else:
                return '⏸️ HOLD'
        
        df_top5['recommendation'] = df_top5.apply(get_recommendation, axis=1)
        
        return df_top5
    
    def save_scan_results(self, df_scan: pd.DataFrame, df_signals: pd.DataFrame, input_file: str = None):
        """Save scan results to Excel with formatting"""
        
        # Create unique filename with date and time
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Extract base name from input file if provided
        if input_file:
            base_name = os.path.splitext(os.path.basename(input_file))[0]
            filename = f"scan_results_{base_name}_{timestamp}.xlsx"
        else:
            filename = f"scan_results_{timestamp}.xlsx"
        
        # Create output directory if it doesn't exist
        output_dir = "scan_output"
        os.makedirs(output_dir, exist_ok=True)
        
        output_path = os.path.join(output_dir, filename)
        
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Main scan results
            if not df_scan.empty:
                df_scan.to_excel(writer, sheet_name='Full_Scan', index=False)
            
            # Top 5 signals
            if not df_signals.empty:
                df_signals.to_excel(writer, sheet_name='Top_5_Signals', index=False)
            
                # Portfolio allocation
                if 'shares' in df_signals.columns:
                    # Added hhhl_trend and hhhl_strength
                    portfolio_cols = ['symbol', 'rank', 'score', 'hhhl_trend', 'hhhl_strength', 
                                    'entry_type', 'close', 'shares', 'position_value', 
                                    'stop_price', 'target_1r', 'target_2r', 
                                    'position_pct', 'recommendation']
                    available_cols = [col for col in portfolio_cols if col in df_signals.columns]
                    portfolio_df = df_signals[available_cols]
                    portfolio_df.to_excel(writer, sheet_name='Portfolio_Allocation', index=False)
            
            # Format worksheets
            for sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                
                # Format headers
                for cell in worksheet[1]:
                    cell.fill = PatternFill(start_color='4CAF50', end_color='4CAF50', fill_type='solid')
                    cell.font = Font(bold=True, color='FFFFFF')
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                
                # Auto-adjust columns
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width
                
                # Freeze top row
                worksheet.freeze_panes = 'A2'
        
        print(f"\n✅ Results saved to: {output_path}")
        
        return output_path
    
    def display_summary(self, df_scan: pd.DataFrame, df_signals: pd.DataFrame):
        """Display summary of scan results"""
        
        print("\n" + "="*60)
        print("📊 SCAN SUMMARY")
        print("="*60)
        
        if df_scan.empty:
            print("❌ No scan results to display")
            return
        
        print(f"\n📈 Total stocks scanned: {len(df_scan)}")
        
        if 'hhhl_trend' in df_scan.columns:
            hhhl_uptrends = (df_scan['hhhl_trend'] == 'UPTREND').sum()
            print(f"📈 Stocks in HHHL Uptrend: {hhhl_uptrends}")
            
        print(f"✅ Tradeable stocks (HHHL + Filters): {df_scan['tradeable'].sum() if 'tradeable' in df_scan.columns else 0}")
        
        # Entry signals summary
        if 'entry_type' in df_scan.columns:
            breakouts = (df_scan['entry_type'] == 'BREAKOUT').sum()
            pullbacks = (df_scan['entry_type'] == 'PULLBACK').sum()
            print(f"🔥 Breakout signals: {breakouts}")
            print(f"📈 Pullback signals: {pullbacks}")
        
        if df_signals.empty:
            print("\n⚠️ No trading candidates found meeting all criteria")
            return
        
        print("\n" + "="*60)
        print("🎯 TOP 5 TRADING CANDIDATES")
        print("="*60)
        
        for _, row in df_signals.iterrows():
            print(f"\n{row['rank']}. {row['symbol']}")
            print(f"   Score: {row['score']:.3f} | Price: ₹{row['close']:.2f}")
            # Added HHHL Info
            print(f"   HHHL Trend: {row['hhhl_trend']} (Strength: {row.get('hhhl_strength', 0):.1f}%)")
            print(f"   ADX: {row['adx']:.1f} | RSI: {row['rsi_14']:.1f} | RS55: {row['rs55']:.3f}")
            
            if row.get('entry_type'):
                print(f"   Signal: {row['entry_type']} 🎯")
                if 'shares' in row and row['shares'] > 0:
                    print(f"   Position: {row['shares']} shares @ ₹{row['position_value']:,.0f} ({row['position_pct']}%)")
                    print(f"   Stop: ₹{row['stop_price']:.2f} | T1: ₹{row['target_1r']:.2f} | T2: ₹{row['target_2r']:.2f}")
            
            print(f"   {row['recommendation']}")
        
        # Risk summary
        if not df_signals.empty and 'position_value' in df_signals.columns:
            total_allocation = df_signals['position_value'].sum()
            total_risk = df_signals.get('risk_amount', pd.Series([0])).sum()
            
            print("\n" + "="*60)
            print("💰 PORTFOLIO ALLOCATION")
            print("="*60)
            print(f"Portfolio Value: ₹{self.portfolio_value:,.0f}")
            print(f"Total Allocation: ₹{total_allocation:,.0f} ({(total_allocation/self.portfolio_value)*100:.1f}%)")
            print(f"Total Risk: ₹{total_risk:,.0f} ({(total_risk/self.portfolio_value)*100:.2f}%)")
            print(f"Risk per Trade: {self.risk_per_trade*100}%")
            print(f"ATR Multiplier: {self.atr_multiplier}x")
            
            if self.scan_date:
                print(f"Scan Date: {self.scan_date.strftime('%Y-%m-%d')}")


class DailyTradingSystem:
    """Complete daily trading system with checklist"""
    
    def __init__(self):
        # Initialize scanner with default portfolio, can be overridden
        self.scanner = TradingScanner(portfolio_value=1000000, risk_per_trade=0.01)
        
    def find_excel_files(self, directory: str = ".") -> List[str]:
        """Find available Excel files in the directory and subdirectories"""
        excel_files = []
        
        # Search in current directory and subdirectories
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.endswith('.xlsx') or file.endswith('.xls'):
                    excel_files.append(os.path.join(root, file))
        
        return sorted(excel_files, reverse=True)[:10]  # Return last 10 files
    
    def run_daily_scan(self, excel_file: str = None):
        """Run complete daily scanning routine"""
        
        print("\n" + "🌟"*30)
        print("🚀 DAILY TRADING SCANNER (HHHL + MECHANICAL)")
        print("🌟"*30)
        print(f"\n📅 System Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        if excel_file:
            print(f"📂 Input File: {os.path.basename(excel_file)}")
        
        # Get portfolio value from user
        portfolio_input = input("\n💰 Enter portfolio value (default ₹10,00,000): ").strip()
        if portfolio_input:
            try:
                self.scanner.portfolio_value = float(portfolio_input)
            except:
                print("Using default portfolio value (₹10,00,000)")
                self.scanner.portfolio_value = 1000000
        else:
             print("Using default portfolio value (₹10,00,000)")
             self.scanner.portfolio_value = 1000000
        
        # Get risk percentage
        risk_input = input("📊 Enter risk per trade % (default 1%): ").strip()
        if risk_input:
            try:
                self.scanner.risk_per_trade = float(risk_input) / 100
            except:
                print("Using default risk percentage (1%)")
                self.scanner.risk_per_trade = 0.01
        else:
            print("Using default risk percentage (1%)")
            self.scanner.risk_per_trade = 0.01
        
        print(f"\n📋 Settings:")
        print(f"   Portfolio: ₹{self.scanner.portfolio_value:,.0f}")
        print(f"   Risk/Trade: {self.scanner.risk_per_trade*100}%")
        print(f"   Max Positions: {self.scanner.max_positions}")
        
        # Run scan
        df_scan = self.scanner.scan_universe(excel_file)
        
        if df_scan.empty:
            print("\n❌ No valid scan results. Please check your input file.")
            return pd.DataFrame(), pd.DataFrame()
        
        # Generate signals
        df_signals = self.scanner.generate_trading_signals(df_scan)
        
        # Save results automatically
        output_file = self.scanner.save_scan_results(df_scan, df_signals, excel_file)
        
        # Display summary
        self.scanner.display_summary(df_scan, df_signals)
        
        # Generate daily checklist
        self.generate_daily_checklist(df_signals)
        
        return df_scan, df_signals
    
    def generate_daily_checklist(self, df_signals: pd.DataFrame):
        """Generate daily trading checklist"""
        
        print("\n" + "="*60)
        print("📝 DAILY TRADING CHECKLIST")
        print("="*60)
        
        checklist = """
✅ Pre-Market Checklist:
   □ Review overnight global markets
   □ Check for news on portfolio stocks
   □ Review scan results and top 5 candidates
   □ Verify entry signals are still valid
   □ Calculate exact position sizes
   □ Set limit/market orders

✅ Market Hours Checklist:
   □ Monitor entry signals for top 5
   □ Execute trades on confirmed signals
   □ Set stop-loss orders immediately
   □ Set alerts for profit targets
   □ Monitor existing positions
   □ Check for exit signals

✅ Post-Market Checklist:
   □ Review day's trades
   □ Update trading journal
   □ Run EOD scan for next day
   □ Adjust stops to breakeven if +1R reached
   □ Review portfolio allocation
   □ Plan next day's trades
        """
        print(checklist)
        
        if not df_signals.empty and 'entry_type' in df_signals.columns:
            active_signals = df_signals[df_signals['entry_type'].notna()]
            if not active_signals.empty:
                print("\n🎯 SPECIFIC ACTIONS FOR TOMORROW:")
                print("-" * 40)
                
                for _, row in active_signals.iterrows():
                    print(f"\n{row['symbol']}:")
                    print(f"  • Entry Type: {row['entry_type']}")
                    print(f"  • Entry Price: Around ₹{row['close']:.2f}")
                    if 'shares' in row and row['shares'] > 0:
                        print(f"  • Position Size: {row['shares']} shares")
                        print(f"  • Stop Loss: ₹{row['stop_price']:.2f}")
                        print(f"  • Target 1 (+1R): ₹{row['target_1r']:.2f}")
                        print(f"  • Target 2 (+2R): ₹{row['target_2r']:.2f}")


def main():
    """Main execution function"""
    
    system = DailyTradingSystem()
    
    # Check for input Excel file
    print("\n📁 FILE SELECTION:")
    print("-" * 40)
    
    excel_input = input("Enter Excel file path (or press Enter to browse): ").strip()
    
    # Remove quotes if present
    excel_input = excel_input.strip('"').strip("'")
    
    excel_file = None
    
    if excel_input:
        if os.path.exists(excel_input):
            excel_file = excel_input
            print(f"✅ Using file: {os.path.basename(excel_file)}")
        else:
            print(f"⚠️ File not found: {excel_input}")
            
            # Try to find similar files
            directory = os.path.dirname(excel_input) if os.path.dirname(excel_input) else "."
            available_files = system.find_excel_files(directory)
            
            if available_files:
                print("\n📂 Available Excel files in directory:")
                for i, file in enumerate(available_files[:5], 1):
                    print(f"  {i}. {os.path.basename(file)}")
                
                choice = input("\nEnter choice number (or 0 to exit): ").strip()
                if choice.isdigit() and 0 < int(choice) <= len(available_files):
                    excel_file = available_files[int(choice) - 1]
                    print(f"✅ Selected: {os.path.basename(excel_file)}")
    else:
        # Browse for files
        available_files = system.find_excel_files()
        
        if available_files:
            print("\n📂 Recent Excel files found:")
            for i, file in enumerate(available_files[:10], 1):
                print(f"  {i}. {os.path.basename(file)} ({os.path.dirname(file)})")
            
            choice = input("\nEnter choice number (or 0 to exit): ").strip()
            if choice.isdigit() and 0 < int(choice) <= len(available_files):
                excel_file = available_files[int(choice) - 1]
                print(f"✅ Selected: {os.path.basename(excel_file)}")
    
    if not excel_file:
        print("\n❌ No valid Excel file selected. Exiting.")
        return
    
    # Run daily scan
    df_scan, df_signals = system.run_daily_scan(excel_file)
    
    print("\n" + "🌟"*30)
    print("✨ SCAN COMPLETE! Results automatically saved.")
    print("🌟"*30)


if __name__ == "__main__":
    main()
