"""
ICT/SMC Pattern Detection Utilities

Implements Inner Circle Trader / Smart Money Concepts patterns:
- Fair Value Gap (FVG) detection
- Swing point identification
- Liquidity sweep detection
- Order block zone detection
"""

from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple


class Bar(NamedTuple):
    """OHLC bar data."""
    datetime: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


@dataclass
class FairValueGap:
    """Fair Value Gap zone."""
    type: str  # 'bullish' or 'bearish'
    top: float  # Upper bound of gap
    bottom: float  # Lower bound of gap
    formation_time: datetime
    candle_index: int


@dataclass  
class SwingPoint:
    """Swing high or low."""
    type: str  # 'high' or 'low'
    price: float
    datetime: datetime
    index: int


@dataclass
class OrderBlock:
    """Order block zone."""
    type: str  # 'bullish' or 'bearish'
    top: float  # Zone high
    bottom: float  # Zone low
    formation_time: datetime
    candle_index: int


def find_fair_value_gaps(bars: list[Bar], lookback: int = 20) -> list[FairValueGap]:
    """
    Detect Fair Value Gaps (FVGs) in price action.
    
    Bullish FVG: Gap between candle[i-2].high and candle[i].low (gap up)
    Bearish FVG: Gap between candle[i-2].low and candle[i].high (gap down)
    
    Args:
        bars: List of OHLC bars (most recent last)
        lookback: Number of bars to scan for FVGs
        
    Returns:
        List of FVG zones found
    """
    if len(bars) < 3:
        return []
    
    fvgs = []
    start_idx = max(2, len(bars) - lookback)
    
    for i in range(start_idx, len(bars)):
        bar_0 = bars[i - 2]  # First candle
        bar_1 = bars[i - 1]  # Middle candle (impulse)
        bar_2 = bars[i]      # Third candle
        
        # Bullish FVG: Gap exists between bar_0.high and bar_2.low
        if bar_2.low > bar_0.high:
            fvgs.append(FairValueGap(
                type='bullish',
                top=bar_2.low,
                bottom=bar_0.high,
                formation_time=bar_2.datetime,
                candle_index=i,
            ))
        
        # Bearish FVG: Gap exists between bar_0.low and bar_2.high
        elif bar_2.high < bar_0.low:
            fvgs.append(FairValueGap(
                type='bearish',
                top=bar_0.low,
                bottom=bar_2.high,
                formation_time=bar_2.datetime,
                candle_index=i,
            ))
    
    return fvgs


def find_swing_points(bars: list[Bar], lookback: int = 10) -> list[SwingPoint]:
    """
    Identify swing highs and lows.
    
    A swing high is a bar with lower highs on both sides.
    A swing low is a bar with higher lows on both sides.
    
    Args:
        bars: List of OHLC bars
        lookback: Number of bars on each side to confirm swing
        
    Returns:
        List of swing points found
    """
    if len(bars) < lookback * 2 + 1:
        return []
    
    swings = []
    
    for i in range(lookback, len(bars) - lookback):
        bar = bars[i]
        
        # Check for swing high
        is_swing_high = True
        for j in range(1, lookback + 1):
            if bars[i - j].high >= bar.high or bars[i + j].high >= bar.high:
                is_swing_high = False
                break
        
        if is_swing_high:
            swings.append(SwingPoint(
                type='high',
                price=bar.high,
                datetime=bar.datetime,
                index=i,
            ))
        
        # Check for swing low
        is_swing_low = True
        for j in range(1, lookback + 1):
            if bars[i - j].low <= bar.low or bars[i + j].low <= bar.low:
                is_swing_low = False
                break
        
        if is_swing_low:
            swings.append(SwingPoint(
                type='low',
                price=bar.low,
                datetime=bar.datetime,
                index=i,
            ))
    
    return swings


def detect_liquidity_sweep(
    current_bar: Bar,
    swing_points: list[SwingPoint],
    sweep_tolerance: float = 0.001,
) -> dict | None:
    """
    Detect if current bar swept liquidity at a swing point then rejected.
    
    Bullish sweep: Price went below swing low, then closed above it
    Bearish sweep: Price went above swing high, then closed below it
    
    Args:
        current_bar: The current OHLC bar
        swing_points: List of recent swing points
        sweep_tolerance: How far beyond swing to consider a sweep (0.1%)
        
    Returns:
        Dict with sweep details or None if no sweep detected
    """
    for swing in swing_points:
        if swing.type == 'high':
            # Check for bearish sweep (went above, closed below)
            sweep_level = swing.price * (1 + sweep_tolerance)
            if current_bar.high > swing.price and current_bar.close < swing.price:
                return {
                    'type': 'bearish',
                    'swing_level': swing.price,
                    'wick_high': current_bar.high,
                    'close': current_bar.close,
                    'sweep_depth_pct': (current_bar.high - swing.price) / swing.price * 100,
                    'swing_datetime': swing.datetime,
                }
        
        elif swing.type == 'low':
            # Check for bullish sweep (went below, closed above)
            if current_bar.low < swing.price and current_bar.close > swing.price:
                return {
                    'type': 'bullish',
                    'swing_level': swing.price,
                    'wick_low': current_bar.low,
                    'close': current_bar.close,
                    'sweep_depth_pct': (swing.price - current_bar.low) / swing.price * 100,
                    'swing_datetime': swing.datetime,
                }
    
    return None


def find_order_blocks(
    bars: list[Bar],
    lookback: int = 20,
    min_move_pct: float = 1.0,
) -> list[OrderBlock]:
    """
    Identify order blocks (last opposing candle before strong move).
    
    Bullish OB: Last bearish candle before strong bullish move
    Bearish OB: Last bullish candle before strong bearish move
    
    Args:
        bars: List of OHLC bars
        lookback: Number of bars to scan
        min_move_pct: Minimum move % to qualify as "strong"
        
    Returns:
        List of order block zones
    """
    if len(bars) < 3:
        return []
    
    order_blocks = []
    start_idx = max(1, len(bars) - lookback)
    
    for i in range(start_idx, len(bars) - 1):
        bar = bars[i]
        next_bar = bars[i + 1]
        
        is_bearish = bar.close < bar.open
        is_bullish = bar.close > bar.open
        
        # Calculate next bar's move
        move_pct = abs(next_bar.close - next_bar.open) / next_bar.open * 100
        
        if move_pct >= min_move_pct:
            # Bullish OB: bearish candle followed by strong bullish move
            if is_bearish and next_bar.close > next_bar.open:
                order_blocks.append(OrderBlock(
                    type='bullish',
                    top=bar.high,
                    bottom=bar.low,
                    formation_time=bar.datetime,
                    candle_index=i,
                ))
            
            # Bearish OB: bullish candle followed by strong bearish move
            elif is_bullish and next_bar.close < next_bar.open:
                order_blocks.append(OrderBlock(
                    type='bearish',
                    top=bar.high,
                    bottom=bar.low,
                    formation_time=bar.datetime,
                    candle_index=i,
                ))
    
    return order_blocks


def is_price_in_fvg(price: float, fvg: FairValueGap) -> bool:
    """Check if price is within FVG zone."""
    return fvg.bottom <= price <= fvg.top


def is_price_in_order_block(price: float, ob: OrderBlock) -> bool:
    """Check if price is within order block zone."""
    return ob.bottom <= price <= ob.top


def get_recent_swing_high(swing_points: list[SwingPoint], n: int = 1) -> SwingPoint | None:
    """Get the nth most recent swing high."""
    highs = [s for s in swing_points if s.type == 'high']
    highs.sort(key=lambda x: x.index, reverse=True)
    return highs[n - 1] if len(highs) >= n else None


def get_recent_swing_low(swing_points: list[SwingPoint], n: int = 1) -> SwingPoint | None:
    """Get the nth most recent swing low."""
    lows = [s for s in swing_points if s.type == 'low']
    lows.sort(key=lambda x: x.index, reverse=True)
    return lows[n - 1] if len(lows) >= n else None
