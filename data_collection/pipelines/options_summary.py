"""
Options Data Collector using YFinance (FREE).
Collects daily put/call ratios, IV, and max pain for watchlist symbols.
"""

import asyncio
import logging
from datetime import datetime
import yfinance as yf

from data_collection.common.db import notify_ingest
from data_collection.common.settings import load_config
from data_collection.db.connection import get_connection

logger = logging.getLogger(__name__)

_YF_SYMBOL_MAP: dict[str, str] | None = None


def _load_symbol_map() -> dict[str, str]:
    global _YF_SYMBOL_MAP
    if _YF_SYMBOL_MAP is not None:
        return _YF_SYMBOL_MAP
    try:
        config = load_config()
        mapping = config.get("yfinance", {}).get("symbol_map", {})
        _YF_SYMBOL_MAP = {k.upper(): v for k, v in mapping.items()}
    except Exception:
        _YF_SYMBOL_MAP = {}
    return _YF_SYMBOL_MAP


def _yfinance_symbol(symbol: str) -> str:
    """Convert internal symbol to yfinance format (e.g. BRK.B -> BRK-B)."""
    mapping = _load_symbol_map()
    mapped = mapping.get(symbol.upper())
    if mapped:
        return mapped
    return symbol.replace(".", "-")


def collect_options_for_symbol(symbol: str) -> dict | None:
    """Collect options summary for one symbol via YFinance."""
    try:
        yf_sym = _yfinance_symbol(symbol)
        ticker = yf.Ticker(yf_sym)
        
        # Get available expirations
        expirations = ticker.options
        if not expirations:
            logger.debug(f"{symbol}: No options available")
            return None
        
        # Find next monthly expiration (7-45 days out)
        target_exp = None
        for exp_str in expirations:
            exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
            days_to_exp = (exp_date - datetime.now().date()).days
            if 7 <= days_to_exp <= 45:
                target_exp = exp_str
                break
        
        if not target_exp:
            target_exp = expirations[0]  # Fallback to nearest
        
        # Get option chain
        chain = ticker.option_chain(target_exp)
        calls = chain.calls
        puts = chain.puts
        
        if calls.empty or puts.empty:
            logger.debug(f"{symbol}: Empty option chain")
            return None
        
        # Get current price
        hist = ticker.history(period='1d')
        if hist.empty:
            logger.debug(f"{symbol}: No price history")
            return None
        spot_price = float(hist['Close'].iloc[-1])
        
        # Calculate metrics
        total_call_vol = int(calls['volume'].sum())
        total_put_vol = int(puts['volume'].sum())
        total_call_oi = int(calls['openInterest'].sum())
        total_put_oi = int(puts['openInterest'].sum())
        
        pcr_vol = total_put_vol / total_call_vol if total_call_vol > 0 else None
        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else None
        
        # ATM IV (find strike closest to spot)
        calls_copy = calls.copy()
        calls_copy['strike_diff'] = abs(calls_copy['strike'] - spot_price)
        atm_call = calls_copy.loc[calls_copy['strike_diff'].idxmin()]
        atm_iv = float(atm_call['impliedVolatility']) if 'impliedVolatility' in atm_call else None
        
        # Max pain calculation
        max_pain = calculate_max_pain(calls, puts)
        
        # Highest OI strikes
        max_call_strike = float(calls.loc[calls['openInterest'].idxmax()]['strike']) if not calls.empty else None
        max_put_strike = float(puts.loc[puts['openInterest'].idxmax()]['strike']) if not puts.empty else None
        
        return {
            'symbol': symbol,
            'put_call_ratio_volume': pcr_vol,
            'put_call_ratio_oi': pcr_oi,
            'total_call_volume': total_call_vol,
            'total_put_volume': total_put_vol,
            'total_call_oi': total_call_oi,
            'total_put_oi': total_put_oi,
            'atm_iv': atm_iv,
            'max_pain_strike': max_pain,
            'highest_oi_call_strike': max_call_strike,
            'highest_oi_put_strike': max_put_strike,
            'spot_price': spot_price,
            'expiration_date': datetime.strptime(target_exp, '%Y-%m-%d').date(),
        }
    
    except Exception as e:
        logger.warning(f"Failed to collect options for {symbol}: {e}")
        return None


def calculate_max_pain(calls, puts) -> float | None:
    """Calculate max pain strike (where option holders lose most)."""
    try:
        all_strikes = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
        
        if not all_strikes:
            return None
        
        min_loss = float('inf')
        max_pain_strike = all_strikes[len(all_strikes)//2]  # Default to middle
        
        for strike in all_strikes:
            # ITM calls lose intrinsic value
            call_loss = calls[calls['strike'] < strike].apply(
                lambda x: (strike - x['strike']) * x['openInterest'], axis=1
            ).sum()
            
            # ITM puts lose intrinsic value
            put_loss = puts[puts['strike'] > strike].apply(
                lambda x: (x['strike'] - strike) * x['openInterest'], axis=1
            ).sum()
            
            total_loss = call_loss + put_loss
            if total_loss < min_loss:
                min_loss = total_loss
                max_pain_strike = strike
        
        return float(max_pain_strike)
    except Exception as e:
        logger.debug(f"Max pain calculation failed: {e}")
        return None


def run(limit: int | None = None):
    """Main collection routine."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    conn = get_connection()
    try:
        # Get all universe symbols (not just watchlist)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT symbol 
                FROM universe_member 
                WHERE active = true
                ORDER BY symbol
            """)
            symbols = [row[0] for row in cursor.fetchall()]
        
        if limit:
            symbols = symbols[:limit]
        
        logger.info(f"Collecting options data for {len(symbols)} symbols...")
        
        collected = 0
        errors = 0
        
        # Collect in batches to avoid rate limits
        batch_size = 5
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            
            for symbol in batch:
                data = collect_options_for_symbol(symbol)
                if not data:
                    errors += 1
                    continue
                
                # Save to database
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO options_daily_summary (
                            symbol, date, put_call_ratio_volume, put_call_ratio_oi,
                            total_call_volume, total_put_volume, total_call_oi, total_put_oi,
                            atm_iv, max_pain_strike, highest_oi_call_strike, highest_oi_put_strike,
                            spot_price, expiration_date
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, date) DO UPDATE SET
                            put_call_ratio_volume = EXCLUDED.put_call_ratio_volume,
                            atm_iv = EXCLUDED.atm_iv,
                            collected_at = NOW()
                    """, (
                        data['symbol'], datetime.now().date(), 
                        data['put_call_ratio_volume'], data['put_call_ratio_oi'],
                        data['total_call_volume'], data['total_put_volume'],
                        data['total_call_oi'], data['total_put_oi'],
                        data['atm_iv'], data['max_pain_strike'],
                        data['highest_oi_call_strike'], data['highest_oi_put_strike'],
                        data['spot_price'], data['expiration_date']
                    ))
                    conn.commit()
                
                collected += 1
                pcr_str = f"{data['put_call_ratio_volume']:.2f}" if data['put_call_ratio_volume'] else "N/A"
                iv_str = f"{data['atm_iv']*100:.1f}%" if data['atm_iv'] else "N/A"
                logger.info(f"✓ {symbol}: PCR={pcr_str}, IV={iv_str}")
            
            # Rate limit: wait between batches
            if i + batch_size < len(symbols):
                import time
                time.sleep(12)  # 5 symbols per 12 seconds = ~25 symbols/minute
        
        logger.info(f"Collection complete: {collected} collected, {errors} errors")
        
        if collected > 0:
            notify_ingest(conn, 'eiqora_ingest', {
                'source': 'options_daily_summary',
                'new_count': collected,
            })
            conn.commit()
    
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='Limit number of symbols')
    args = parser.parse_args()
    
    run(limit=args.limit)
