"""
Signal Quality Assessment Module.

Evaluates trigger quality based on technical confirmations.
Provides scoring, flags, and explanations to help agents filter signals.
"""

from typing import Any


def assess_signal_quality(trigger_details: dict[str, Any]) -> dict[str, Any]:
    """
    Assess trigger signal quality based on confirmations.
    
    Evaluates:
    - MA trend alignment (bullish if both MA20 and MA50 above)
    - Money flow (CMF indicates accumulation/distribution)
    - Options sentiment (PCR indicates bullish/bearish positioning)
    - RSI positioning (healthy momentum vs overbought/oversold)
    
    Args:
        trigger_details: Dictionary from trigger.details containing:
            - ma20_state, ma50_state: Trend indicators
            - cmf_20: Chaikin Money Flow
            - mfi_14: Money Flow Index
            - options_pcr: Put/Call Ratio
            - rsi14: Daily RSI
            - rsi_hourly: Hourly RSI (if available)
            
    Returns:
        Dictionary with:
            - quality_score: float (0.0-1.0)
            - quality_flags: list[str] of notable conditions
            - quality_explanation: str describing score
            - confirmations: dict of boolean checks
    """
    score = 0.5  # Neutral baseline
    flags = []
    confirmations = {}
    
    # Extract indicators
    ma20_state = trigger_details.get("ma20_state")
    ma50_state = trigger_details.get("ma50_state")
    cmf = trigger_details.get("cmf_20")
    mfi = trigger_details.get("mfi_14")
    pcr = trigger_details.get("options_pcr")
    rsi_daily = trigger_details.get("rsi14")
    rsi_hourly = trigger_details.get("rsi_hourly")
    
    # 1. MA Trend Alignment (+0.20 if bullish)
    if ma20_state == "ABOVE" and ma50_state == "ABOVE":
        score += 0.20
        flags.append("strong_trend")
        confirmations["trend_aligned"] = True
    elif ma20_state == "BELOW" and ma50_state == "BELOW":
        score -= 0.10
        flags.append("weak_trend")
        confirmations["trend_aligned"] = False
    else:
        confirmations["trend_aligned"] = None  # Mixed
    
    # 2. Money Flow - CMF (Chaikin Money Flow)
    if cmf is not None:
        if cmf > 0.05:
            score += 0.15
            flags.append("accumulation")
            confirmations["money_flow_positive"] = True
        elif cmf < -0.10:
            score -= 0.15
            flags.append("distribution")
            confirmations["money_flow_positive"] = False
        else:
            confirmations["money_flow_positive"] = None  # Neutral
    
    # 3. Money Flow - MFI (supplementary)
    if mfi is not None:
        if mfi > 80:
            score -= 0.05
            flags.append("mfi_overbought")
        elif mfi < 20:
            score += 0.05
            flags.append("mfi_oversold_bounce_potential")
    
    # 4. Options Sentiment - PCR (Put/Call Ratio)
    if pcr is not None:
        if pcr > 1.2:
            score -= 0.10
            flags.append("bearish_options")
            confirmations["options_neutral"] = False
        elif pcr < 0.8:
            score += 0.05
            flags.append("bullish_options")
            confirmations["options_neutral"] = True
        else:
            confirmations["options_neutral"] = True  # Neutral is fine
    
    # 5. RSI Positioning
    rsi = rsi_daily if rsi_daily is not None else rsi_hourly
    if rsi is not None:
        if 45 < rsi < 70:
            score += 0.10
            flags.append("healthy_momentum")
            confirmations["rsi_healthy"] = True
        elif rsi > 80:
            score -= 0.10
            flags.append("overbought")
            confirmations["rsi_healthy"] = False
        elif rsi < 30:
            score += 0.05
            flags.append("oversold_bounce_potential")
            confirmations["rsi_healthy"] = None
        else:
            confirmations["rsi_healthy"] = True  # Acceptable range
    
    # Clamp score to [0.0, 1.0]
    score = max(0.0, min(1.0, score))
    
    # Generate explanation
    if score >= 0.75:
        quality_level = "high"
    elif score >= 0.55:
        quality_level = "decent"
    elif score >= 0.40:
        quality_level = "neutral"
    else:
        quality_level = "low"
    
    explanation = f"Signal quality: {quality_level} ({score:.2f})"
    if flags:
        explanation += f" - {', '.join(flags)}"
    
    return {
        "quality_score": round(score, 3),
        "quality_level": quality_level,
        "quality_flags": flags,
        "quality_explanation": explanation,
        "confirmations": confirmations,
    }


def get_confluence_boost(trigger_details: dict[str, Any]) -> dict[str, Any]:
    """
    Check if multiple triggers fired for the same symbol (confluence).
    
    Args:
        trigger_details: Dictionary from trigger.details containing:
            - consolidated_triggers: list of other triggers (if consolidated)
            - trigger_count: number of total triggers
            
    Returns:
        Dictionary with:
            - has_confluence: bool
            - trigger_count: int
            - confluence_boost: float (0.0-0.2)
            - confluence_types: list[str] of trigger types
    """
    consolidated = trigger_details.get("consolidated_triggers", [])
    trigger_count = trigger_details.get("trigger_count", 1)
    
    if trigger_count > 1:
        # Multiple triggers = stronger signal
        confluence_boost = min(0.20, (trigger_count - 1) * 0.10)
        trigger_types = [t.get("type") for t in consolidated if t.get("type")]
        
        return {
            "has_confluence": True,
            "trigger_count": trigger_count,
            "confluence_boost": confluence_boost,
            "confluence_types": trigger_types,
        }
    
    return {
        "has_confluence": False,
        "trigger_count": 1,
        "confluence_boost": 0.0,
        "confluence_types": [],
    }


def assess_full_signal_quality(trigger_details: dict[str, Any]) -> dict[str, Any]:
    """
    Comprehensive signal quality assessment including confluence.
    
    Combines:
    - Basic signal quality (confirmations)
    - Confluence boost (multiple triggers)
    
    Args:
        trigger_details: Complete trigger details dictionary
        
    Returns:
        Merged quality assessment with final adjusted score
    """
    base_quality = assess_signal_quality(trigger_details)
    confluence = get_confluence_boost(trigger_details)
    
    # Adjust score with confluence boost
    base_score = base_quality["quality_score"]
    final_score = min(1.0, base_score + confluence["confluence_boost"])
    
    # Update explanation if confluence detected
    explanation = base_quality["quality_explanation"]
    if confluence["has_confluence"]:
        trigger_types = ", ".join(confluence["confluence_types"])
        explanation += f" | Confluence detected: {confluence['trigger_count']} triggers ({trigger_types})"
    
    return {
        **base_quality,
        "quality_score": round(final_score, 3),
        "quality_score_base": base_score,
        "quality_explanation": explanation,
        **confluence,
    }
