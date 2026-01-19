"""
Quantitative scoring functions for profile generation.
Provides deterministic base scores before LLM adjustment.
"""

from typing import Any
import logging

logger = logging.getLogger(__name__)


def calculate_earnings_score(signals: dict[str, Any]) -> tuple[float, str]:
    """
    Calculate earnings quality score (0.0-1.0).
    
    Returns: (score, explanation)
    """
    earnings = signals.get('earnings', {})
    quarters = earnings.get('quarters_analyzed', 0)
    beat_rate = earnings.get('beat_rate', 0)
    avg_surprise = earnings.get('avg_surprise_pct', 0)
    
    if quarters == 0:
        return 0.5, "No earnings data available"
    
    # Base score from beat rate
    score = beat_rate  # 0.0 to 1.0
    
    # Bonus for consistent beats
    if beat_rate >= 0.75:
        score += 0.1
    
    # Bonus for large positive surprises
    if avg_surprise and avg_surprise > 5:
        score += 0.1
    elif avg_surprise and avg_surprise < -5:
        score -= 0.1
    
    score = max(0.0, min(1.0, score))
    
    explanation = f"Beat {int(beat_rate*100)}% of quarters"
    if avg_surprise:
        explanation += f", avg surprise {avg_surprise:+.1f}%"
    
    return score, explanation


def calculate_insider_score(signals: dict[str, Any]) -> tuple[float, str]:
    """
    Calculate insider sentiment score (0.0-1.0).
    
    Returns: (score, explanation)
    """
    insider = signals.get('insider', {})
    
    if not insider.get('available'):
        return 0.5, "No insider data"
    
    net_value = insider.get('net_value', 0)
    buy_count = insider.get('buy_count', 0)
    sell_count = insider.get('sell_count', 0)
    ceo_net = insider.get('ceo_net_value', 0)
    
    # Base score from net value
    if net_value > 1_000_000:
        score = 0.8
        explanation = f"Heavy buying (${net_value/1e6:.1f}M net)"
    elif net_value > 100_000:
        score = 0.7
        explanation = f"Moderate buying (${net_value/1e3:.0f}K net)"
    elif net_value > -100_000:
        score = 0.5
        explanation = "Balanced activity"
    elif net_value > -1_000_000:
        score = 0.3
        explanation = f"Moderate selling (${abs(net_value)/1e3:.0f}K net)"
    else:
        score = 0.2
        explanation = f"Heavy selling (${abs(net_value)/1e6:.1f}M net)"
    
    # CEO activity is most important
    if ceo_net > 500_000:
        score += 0.1
        explanation += ", CEO buying"
    elif ceo_net < -500_000:
        score -= 0.1
        explanation += ", CEO selling"
    
    score = max(0.0, min(1.0, score))
    return score, explanation


def calculate_sentiment_score(signals: dict[str, Any]) -> tuple[float, str]:
    """
    Calculate news sentiment score (0.0-1.0).
    
    Returns: (score, explanation)
    """
    sentiment = signals.get('sentiment', {})
    
    avg_sentiment = sentiment.get('avg_sentiment')
    positive_count = sentiment.get('positive_count', 0)
    negative_count = sentiment.get('negative_count', 0)
    total_count = sentiment.get('article_count_90d', 0)
    
    if not avg_sentiment or total_count == 0:
        return 0.5, "No sentiment data"
    
    # Map -10 to +10 scale to 0.0 to 1.0
    # avg_sentiment typically ranges from -5 to +5
    normalized = (avg_sentiment + 5) / 10  # -5 → 0.0, +5 → 1.0
    score = max(0.0, min(1.0, normalized))
    
    # Adjust based on article balance
    if total_count > 5:
        pos_ratio = positive_count / total_count
        neg_ratio = negative_count / total_count
        
        if pos_ratio > 0.6:
            score += 0.05
        elif neg_ratio > 0.6:
            score -= 0.05
    
    score = max(0.0, min(1.0, score))
    
    explanation = f"Avg sentiment {avg_sentiment:+.1f}"
    if positive_count > negative_count:
        explanation += f" ({positive_count} pos vs {negative_count} neg)"
    elif negative_count > positive_count:
        explanation += f" ({negative_count} neg vs {positive_count} pos)"
    
    return score, explanation


def calculate_options_score(options_data: list[dict]) -> tuple[float, str]:
    """
    Calculate options sentiment score from recent PCR data (0.0-1.0).
    
    Returns: (score, explanation)
    """
    if not options_data:
        return 0.5, "No options data"
    
    # Get average PCR over recent period
    pcr_values = [o.get('put_call_ratio_volume') for o in options_data if o.get('put_call_ratio_volume')]
    
    if not pcr_values:
        return 0.5, "No PCR data"
    
    avg_pcr = sum(pcr_values) / len(pcr_values)
    
    # PCR interpretation (inverted - lower PCR = more calls = bullish)
    if avg_pcr < 0.6:
        score = 0.8
        explanation = f"Bullish options flow (PCR {avg_pcr:.2f})"
    elif avg_pcr < 0.8:
        score = 0.65
        explanation = f"Moderately bullish options (PCR {avg_pcr:.2f})"
    elif avg_pcr < 1.2:
        score = 0.5
        explanation = f"Neutral options flow (PCR {avg_pcr:.2f})"
    elif avg_pcr < 1.5:
        score = 0.35
        explanation = f"Moderately bearish options (PCR {avg_pcr:.2f})"
    else:
        score = 0.2
        explanation = f"Bearish options flow (PCR {avg_pcr:.2f})"
    
    return score, explanation


def calculate_money_flow_trend_score(money_flow_data: list[dict]) -> tuple[float, str]:
    """
    Calculate 30-day money flow trend score.
    
    Uses average CMF over 30 days to detect sustained accumulation/distribution.
    
    Returns: (score, explanation)
    """
    if not money_flow_data:
        return 0.5, "No money flow data"
    
    # Extract CMF values
    cmf_values = [d.get('cmf_20') for d in money_flow_data if d.get('cmf_20') is not None]
    
    if not cmf_values:
        return 0.5, "No CMF data available"
    
    # Calculate 30-day average
    avg_cmf = sum(cmf_values) / len(cmf_values)
    
    # Get latest MFI for context
    latest_mfi = None
    for d in money_flow_data:
        if d.get('mfi_14') is not None:
            latest_mfi = d.get('mfi_14')
            break
    
    # Score based on sustained trend
    if avg_cmf > 0.10:
        score = 0.8
        explanation = f"Strong accumulation (30d avg CMF {avg_cmf:+.3f})"
    elif avg_cmf > 0.05:
        score = 0.7
        explanation = f"Moderate accumulation (30d avg CMF {avg_cmf:+.3f})"
    elif avg_cmf > 0:
        score = 0.6
        explanation = f"Slight accumulation trend"
    elif avg_cmf > -0.05:
        score = 0.4
        explanation = f"Slight distribution trend"
    elif avg_cmf > -0.10:
        score = 0.3
        explanation = f"Moderate distribution (30d avg CMF {avg_cmf:-.3f})"
    else:
        score = 0.2
        explanation = f"Strong distribution (30d avg CMF {avg_cmf:-.3f})"
    
    # Add MFI context if available
    if latest_mfi is not None:
        if latest_mfi > 80:
            explanation += f", MFI overbought ({latest_mfi:.0f})"
        elif latest_mfi < 20:
            explanation += f", MFI oversold ({latest_mfi:.0f})"
    
    return score, explanation


def calculate_quantitative_base_score(
    signals: dict[str, Any],
    options_data: list[dict] | None = None,
    money_flow_data: list[dict] | None = None
) -> tuple[float, dict[str, Any]]:
    """
    Calculate quantitative base score from all signals.
    
    Returns: (base_score, breakdown)
    """
    earnings_score, earnings_explain = calculate_earnings_score(signals)
    insider_score, insider_explain = calculate_insider_score(signals)
    sentiment_score, sentiment_explain = calculate_sentiment_score(signals)
    
    # Options data (if available)
    if options_data:
        options_score, options_explain = calculate_options_score(options_data)
    else:
        options_score, options_explain = 0.5, "No options data"
    
    # Money flow trend (30-day)
    if money_flow_data:
        money_flow_score, money_flow_explain = calculate_money_flow_trend_score(money_flow_data)
    else:
        money_flow_score, money_flow_explain = 0.5, "No money flow data"
    
    # Weighted combination (adjusted to include money flow)
    weights = {
        'earnings': 0.30,
        'sentiment': 0.25,
        'insider': 0.20,     # Reduced from 0.25
        'options': 0.15,     # Reduced from 0.20
        'money_flow': 0.10,  # NEW
    }
    
    base_score = (
        earnings_score * weights['earnings'] +
        sentiment_score * weights['sentiment'] +
        insider_score * weights['insider'] +
        options_score * weights['options'] +
        money_flow_score * weights['money_flow']
    )
    
    breakdown = {
        'earnings_score': round(earnings_score, 3),
        'earnings_explain': earnings_explain,
        'insider_score': round(insider_score, 3),
        'insider_explain': insider_explain,
        'sentiment_score': round(sentiment_score, 3),
        'sentiment_explain': sentiment_explain,
        'options_score': round(options_score, 3),
        'options_explain': options_explain,
        'money_flow_score': round(money_flow_score, 3),
        'money_flow_explain': money_flow_explain,
        'weights': weights,
        'base_score': round(base_score, 3),
    }
    
    return base_score, breakdown
