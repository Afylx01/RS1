"""
Stock Data Enrichment Tool
Enriches stock delivery data with technical indicators and formatting
Author: Stock Analysis System
Date: 2024
"""

import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
import os
import glob
from typing import Optional, Tuple, Dict
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import warnings
warnings.filterwarnings('ignore')


class TechnicalIndicators:
    """Calculate technical indicators matching TradingView's Pine Script"""
    
    @staticmethod
    def calculate_ema(data: pd.Series, period: int) -> pd.Series:
        """
        Calculate EMA using Pine Script method
        EMA = (Close * Multiplier) + (EMA_previous * (1 - Multiplier))
        where Multiplier = 2 / (period + 1)
        """
        return data.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Average True Range (ATR)
        TR = max[(high - low), abs(high - close_prev), abs(low - close_prev)]
        ATR = EMA of TR
        """
        close_prev = close.shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close_prev)
        tr3 = abs(low - close_prev)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        
        return atr


class ExcelFormatter:
    """Handle Excel formatting and styling"""
    
    # Color scheme definitions
    COLORS = {
        'header': 'E8F5E9',           # Light green header
        'positive': 'C8E6C9',          # Green for positive values
        'negative': 'FFCDD2',          # Red for negative values
        'neutral': 'FFF9C4',           # Yellow for neutral
        'ema_above': 'A5D6A7',         # Green for price above EMA
        'ema_below': 'EF9A9A',         # Red for price below EMA
        'high_volume': '81C784',       # Dark green for high volume
        'low_volume': 'FF8A65',        # Orange for low volume
        'strong_positive': '4CAF50',   # Strong green
        'strong_negative': 'F44336',   # Strong red
    }
    
    @staticmethod
    def apply_cell_color(cell, color_hex: str, font_color: str = '000000'):
        """Apply color to a cell"""
        cell.fill = PatternFill(start_color=color_hex, end_color=color_hex, fill_type='solid')
        cell.font = Font(color=font_color, bold=False)
        cell.alignment = Alignment(horizontal='center', vertical='center')
    
    @staticmethod
    def format_header(worksheet, header_row: int = 1):
        """Format header row"""
        for cell in worksheet[header_row]:
            cell.fill = PatternFill(
                start_color=ExcelFormatter.COLORS['header'],
                end_color=ExcelFormatter.COLORS['header'],
                fill_type='solid'
            )
            cell.font = Font(bold=True, size=11)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            
            # Add borders
            thin_border = Border(
                left=Side(style='thin'),
                right=Side(style='thin'),
                top=Side(style='medium'),
                bottom=Side(style='medium')
            )
            cell.border = thin_border


class StockDataEnricher:
    """Main class for enriching stock data"""
    
    def __init__(self, base_path: str = "output/"):
        self.base_path = base_path
        self.tech_indicators = TechnicalIndicators()
        self.formatter = ExcelFormatter()
    
    def get_last_trading_day(self) -> datetime:
        """Get the last complete trading day (excluding today if market is open)"""
        today = datetime.now()
        
        # Weekend handling
        if today.weekday() == 5:  # Saturday
            return today - timedelta(days=1)
        elif today.weekday() == 6:  # Sunday
            return today - timedelta(days=2)
        
        # Market hours check (Indian market closes at 3:30 PM)
        elif today.hour >= 16:
            return today
        else:
            return today - timedelta(days=1)
    
    def convert_date_format(self, date_str: str) -> str:
        """Convert ddmmyyyy to yyyymmdd format for filename"""
        try:
            # Parse from ddmmyyyy format
            date_obj = datetime.strptime(date_str, "%d%m%Y")
            # Return in yyyymmdd format
            return date_obj.strftime("%Y%m%d")
        except ValueError:
            raise ValueError(f"Invalid date format: {date_str}. Expected format: ddmmyyyy")
    
    def find_excel_file(self, date_str: str) -> Optional[str]:
        """Find the Excel file for the given date"""
        filename = f"high_delivery_stocks_{date_str}.xlsx"
        full_path = os.path.join(self.base_path, filename)
        
        if os.path.exists(full_path):
            return full_path
        
        # List available files if not found
        pattern = os.path.join(self.base_path, "high_delivery_stocks_*.xlsx")
        files = glob.glob(pattern)
        if files:
            print(f"\n❌ File not found for date {date_str}")
            print(f"📁 Available files:")
            for f in sorted(files)[-5:]:  # Show last 5 files
                print(f"   - {os.path.basename(f)}")
        return None
    
    def fetch_stock_data(self, symbol: str, end_date: datetime, days_back: int = 300) -> pd.DataFrame:
        """Fetch stock data from yfinance"""
        # Add NSE suffix if not present
        if not symbol.endswith('.NS') and not symbol.endswith('.BO'):
            symbol = f"{symbol}.NS"
        
        start_date = end_date - timedelta(days=days_back)
        
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date + timedelta(days=1))
            
            if df.empty:
                print(f"   ⚠️  No data found for {symbol}")
                return pd.DataFrame()
            
            return df
        except Exception as e:
            print(f"   ❌ Error fetching {symbol}: {str(e)[:50]}")
            return pd.DataFrame()
    
    def calculate_volume_metrics(self, current_volume: float, avg_volume: float) -> Dict[str, float]:
        """Calculate volume-related metrics"""
        if avg_volume == 0:
            return {'diff_pct': 0, 'ratio': 0}
        
        diff_pct = ((current_volume - avg_volume) / avg_volume) * 100
        ratio = current_volume / avg_volume
        
        return {
            'diff_pct': round(diff_pct, 2),
            'ratio': round(ratio, 2)
        }
    
    def enrich_stock_data(self, df_stocks: pd.DataFrame, target_date: datetime) -> pd.DataFrame:
        """Enrich the stock data with technical indicators"""
        
        print("\n" + "="*60)
        print("📊 CALCULATING TECHNICAL INDICATORS")
        print("="*60)
        
        # Initialize new columns for indicators
        indicator_columns = {
            'EMA_21': np.nan,
            'EMA_200': np.nan,
            'ATR_14': np.nan,
            'PRICE_vs_EMA21': '',
            'PRICE_vs_EMA200': '',
            'VOLUME_TODAY': np.nan,
            'AVG_VOL_20D': np.nan,
            'VOL_DIFF_%': np.nan,
            'VOL_RATIO': np.nan
        }
        
        for col, default_val in indicator_columns.items():
            df_stocks[col] = default_val
        
        total_stocks = len(df_stocks)
        
        for idx, row in df_stocks.iterrows():
            symbol = row['SYMBOL']
            print(f"\n[{idx+1}/{total_stocks}] Processing {symbol}...", end="")
            
            # Fetch historical data
            stock_data = self.fetch_stock_data(symbol, target_date, days_back=400)
            
            if stock_data.empty:
                print(" ⏭️  Skipped")
                continue
            
            # Calculate technical indicators
            stock_data['EMA_21'] = self.tech_indicators.calculate_ema(stock_data['Close'], 21)
            stock_data['EMA_200'] = self.tech_indicators.calculate_ema(stock_data['Close'], 200)
            stock_data['ATR_14'] = self.tech_indicators.calculate_atr(
                stock_data['High'], 
                stock_data['Low'], 
                stock_data['Close'], 
                14
            )
            
            # Calculate volume metrics
            stock_data['AVG_VOL_20D'] = stock_data['Volume'].rolling(window=20).mean()
            
            # Get data for target date
            target_date_str = target_date.strftime('%Y-%m-%d')
            
            if target_date_str in stock_data.index.strftime('%Y-%m-%d'):
                target_data = stock_data[stock_data.index.strftime('%Y-%m-%d') == target_date_str].iloc[0]
                
                # Update technical indicators
                ema21_val = round(target_data['EMA_21'], 2) if not pd.isna(target_data['EMA_21']) else np.nan
                ema200_val = round(target_data['EMA_200'], 2) if not pd.isna(target_data['EMA_200']) else np.nan
                atr_val = round(target_data['ATR_14'], 2) if not pd.isna(target_data['ATR_14']) else np.nan
                
                df_stocks.at[idx, 'EMA_21'] = ema21_val
                df_stocks.at[idx, 'EMA_200'] = ema200_val
                df_stocks.at[idx, 'ATR_14'] = atr_val
                
                # Price vs EMA position
                close_price = row['CLOSE_PRICE']
                if not pd.isna(ema21_val):
                    df_stocks.at[idx, 'PRICE_vs_EMA21'] = 'ABOVE' if close_price > ema21_val else 'BELOW'
                if not pd.isna(ema200_val):
                    df_stocks.at[idx, 'PRICE_vs_EMA200'] = 'ABOVE' if close_price > ema200_val else 'BELOW'
                
                # Volume metrics
                df_stocks.at[idx, 'VOLUME_TODAY'] = int(target_data['Volume'])
                
                # Calculate average volume (excluding current day)
                avg_volume_data = stock_data.iloc[:-1]['Volume'].tail(20)
                if len(avg_volume_data) > 0:
                    avg_volume = avg_volume_data.mean()
                    df_stocks.at[idx, 'AVG_VOL_20D'] = int(avg_volume)
                    
                    volume_metrics = self.calculate_volume_metrics(target_data['Volume'], avg_volume)
                    df_stocks.at[idx, 'VOL_DIFF_%'] = volume_metrics['diff_pct']
                    df_stocks.at[idx, 'VOL_RATIO'] = volume_metrics['ratio']
                
                print(" ✅ Done")
            else:
                print(" ⚠️  No data for target date")
        
        return df_stocks
    
    def apply_conditional_formatting(self, worksheet, df: pd.DataFrame):
        """Apply color coding to the worksheet"""
        
        print("\n🎨 Applying conditional formatting...")
        
        # Get column indices
        headers = [cell.value for cell in worksheet[1]]
        col_indices = {header: idx for idx, header in enumerate(headers, 1)}
        
        # Format each row (starting from row 2)
        for row_num in range(2, len(df) + 2):
            
            # Color code %CHANGE column
            if '%CHANGE' in col_indices:
                cell = worksheet.cell(row=row_num, column=col_indices['%CHANGE'])
                try:
                    value = float(str(cell.value).replace('%', ''))
                    if value > 0:
                        self.formatter.apply_cell_color(cell, self.formatter.COLORS['positive'])
                    else:
                        self.formatter.apply_cell_color(cell, self.formatter.COLORS['negative'])
                except:
                    pass
            
            # Color code PRICE_vs_EMA21
            if 'PRICE_vs_EMA21' in col_indices:
                cell = worksheet.cell(row=row_num, column=col_indices['PRICE_vs_EMA21'])
                if cell.value == 'ABOVE':
                    self.formatter.apply_cell_color(cell, self.formatter.COLORS['ema_above'], 'FFFFFF')
                elif cell.value == 'BELOW':
                    self.formatter.apply_cell_color(cell, self.formatter.COLORS['ema_below'], 'FFFFFF')
            
            # Color code PRICE_vs_EMA200
            if 'PRICE_vs_EMA200' in col_indices:
                cell = worksheet.cell(row=row_num, column=col_indices['PRICE_vs_EMA200'])
                if cell.value == 'ABOVE':
                    self.formatter.apply_cell_color(cell, self.formatter.COLORS['ema_above'], 'FFFFFF')
                elif cell.value == 'BELOW':
                    self.formatter.apply_cell_color(cell, self.formatter.COLORS['ema_below'], 'FFFFFF')
            
            # Color code VOL_DIFF_%
            if 'VOL_DIFF_%' in col_indices:
                cell = worksheet.cell(row=row_num, column=col_indices['VOL_DIFF_%'])
                try:
                    value = float(cell.value) if cell.value else 0
                    if value > 50:
                        self.formatter.apply_cell_color(cell, self.formatter.COLORS['high_volume'], 'FFFFFF')
                    elif value > 0:
                        self.formatter.apply_cell_color(cell, self.formatter.COLORS['positive'])
                    else:
                        self.formatter.apply_cell_color(cell, self.formatter.COLORS['low_volume'], 'FFFFFF')
                except:
                    pass
            
            # Color code RSI_14
            if 'RSI_14' in col_indices:
                cell = worksheet.cell(row=row_num, column=col_indices['RSI_14'])
                try:
                    value = float(cell.value) if cell.value else 50
                    if value > 70:
                        self.formatter.apply_cell_color(cell, self.formatter.COLORS['negative'])
                    elif value < 30:
                        self.formatter.apply_cell_color(cell, self.formatter.COLORS['positive'])
                    else:
                        self.formatter.apply_cell_color(cell, self.formatter.COLORS['neutral'])
                except:
                    pass
    
    def save_enriched_data(self, df: pd.DataFrame, original_file_path: str):
        """Save the enriched data back to the same Excel file with formatting"""
        
        print("\n💾 Saving enriched data...")
        
        # Reorder columns - keeping indicators together
        indicator_cols = [
            'EMA_21', 'EMA_200', 'PRICE_vs_EMA21', 'PRICE_vs_EMA200', 
            'ATR_14', 'VOLUME_TODAY', 'AVG_VOL_20D', 'VOL_DIFF_%', 'VOL_RATIO'
        ]
        
        # Define column order
        first_cols = ['SYMBOL', 'DATE', 'PREV_CLOSE', 'CLOSE_PRICE']
        middle_cols = indicator_cols
        
        # Get remaining columns
        remaining_cols = [col for col in df.columns 
                         if col not in first_cols + middle_cols]
        
        # Reorder
        column_order = first_cols + middle_cols + remaining_cols
        existing_columns = [col for col in column_order if col in df.columns]
        df = df[existing_columns]
        
        # Create temporary file first
        temp_file = original_file_path.replace('.xlsx', '_temp.xlsx')
        
        # Write to Excel with hyperlinks preserved
        with pd.ExcelWriter(temp_file, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Enriched_Data', index=False)
            worksheet = writer.sheets['Enriched_Data']
            
            # Apply formatting
            self.formatter.format_header(worksheet)
            
            # Preserve hyperlinks
            for idx, row in df.iterrows():
                row_num = idx + 2  # Excel rows start at 1, plus header
                
                # SOURCE_URL hyperlink
                if 'SOURCE_URL' in df.columns:
                    col_idx = df.columns.get_loc('SOURCE_URL') + 1
                    cell = worksheet.cell(row=row_num, column=col_idx)
                    if pd.notna(row['SOURCE_URL']):
                        cell.hyperlink = str(row['SOURCE_URL'])
                        cell.font = Font(color='0563C1', underline='single')
                
                # TRADINGVIEW_LINK hyperlink
                if 'TRADINGVIEW_LINK' in df.columns:
                    col_idx = df.columns.get_loc('TRADINGVIEW_LINK') + 1
                    cell = worksheet.cell(row=row_num, column=col_idx)
                    if pd.notna(row['TRADINGVIEW_LINK']):
                        cell.hyperlink = str(row['TRADINGVIEW_LINK'])
                        cell.font = Font(color='0563C1', underline='single')
            
            # Apply conditional formatting
            self.apply_conditional_formatting(worksheet, df)
            
            # Auto-adjust column widths
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                
                # Set width with limits
                adjusted_width = min(max(max_length + 2, 10), 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
            
            # Freeze top row
            worksheet.freeze_panes = 'A2'
        
        # Replace original file with temp file
        os.replace(temp_file, original_file_path)
        print(f"✅ File updated successfully: {original_file_path}")
    
    def display_summary(self, df: pd.DataFrame):
        """Display summary statistics"""
        
        print("\n" + "="*60)
        print("📈 SUMMARY STATISTICS")
        print("="*60)
        
        total_stocks = len(df)
        above_ema21 = (df['PRICE_vs_EMA21'] == 'ABOVE').sum()
        above_ema200 = (df['PRICE_vs_EMA200'] == 'ABOVE').sum()
        
        print(f"\n📊 Total stocks processed: {total_stocks}")
        print(f"📈 Stocks above EMA 21: {above_ema21} ({above_ema21/total_stocks*100:.1f}%)")
        print(f"📈 Stocks above EMA 200: {above_ema200} ({above_ema200/total_stocks*100:.1f}%)")
        
        if 'VOL_DIFF_%' in df.columns:
            avg_vol_diff = df['VOL_DIFF_%'].mean()
            print(f"📊 Average volume difference: {avg_vol_diff:.2f}%")
        
        # Top volume gainers
        if 'VOL_DIFF_%' in df.columns:
            print("\n🔥 TOP 5 VOLUME GAINERS:")
            print("-" * 40)
            top_volume = df.nlargest(5, 'VOL_DIFF_%')[['SYMBOL', 'VOL_DIFF_%', 'CLOSE_PRICE']]
            for _, row in top_volume.iterrows():
                print(f"   {row['SYMBOL']:12} | Vol +{row['VOL_DIFF_%']:>7.1f}% | ₹{row['CLOSE_PRICE']:>8.2f}")
        
        # Stocks above both EMAs
        both_emas = df[(df['PRICE_vs_EMA21'] == 'ABOVE') & (df['PRICE_vs_EMA200'] == 'ABOVE')]
        if len(both_emas) > 0:
            print("\n💎 STOCKS ABOVE BOTH EMAs:")
            print("-" * 40)
            for _, row in both_emas.head(5).iterrows():
                print(f"   {row['SYMBOL']:12} | ₹{row['CLOSE_PRICE']:>8.2f} | RSI: {row.get('RSI_14', 'N/A')}")


def main():
    """Main execution function"""
    
    print("\n" + "="*60)
    print("🚀 STOCK DATA ENRICHMENT TOOL")
    print("="*60)
    
    enricher = StockDataEnricher()
    
    # Get user input for date
    date_input = input("\n📅 Enter date (DDMMYYYY format) or press Enter for last trading day: ").strip()
    
    if date_input:
        try:
            # Convert from DDMMYYYY to YYYYMMDD for filename
            file_date_str = enricher.convert_date_format(date_input)
            # Parse the date for processing
            target_date = datetime.strptime(file_date_str, "%Y%m%d")
        except ValueError as e:
            print(f"\n❌ {e}")
            print("📅 Using last trading day instead...")
            target_date = enricher.get_last_trading_day()
            file_date_str = target_date.strftime("%Y%m%d")
    else:
        target_date = enricher.get_last_trading_day()
        file_date_str = target_date.strftime("%Y%m%d")
    
    print(f"\n📊 Processing data for: {target_date.strftime('%d-%m-%Y')} (File: {file_date_str})")
    
    # Find and read the Excel file
    excel_path = enricher.find_excel_file(file_date_str)
    
    if not excel_path:
        print(f"\n❌ Excel file not found for date {file_date_str}")
        return
    
    print(f"📂 Reading file: {excel_path}")
    
    try:
        df = pd.read_excel(excel_path)
        print(f"✅ Found {len(df)} stocks to process")
    except Exception as e:
        print(f"\n❌ Error reading Excel file: {e}")
        return
    
    # Enrich the data
    df_enriched = enricher.enrich_stock_data(df, target_date)
    
    # Save the enriched data
    enricher.save_enriched_data(df_enriched, excel_path)
    
    # Display summary
    enricher.display_summary(df_enriched)
    
    print("\n" + "="*60)
    print("✨ ENRICHMENT COMPLETE!")
    print("="*60)


if __name__ == "__main__":
    main()