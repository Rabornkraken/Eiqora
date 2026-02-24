"""Service layer for Symbol Detail API"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from eiqora_v2.tools.db import get_connection
from eiqora_v2.api.models.symbol import (
    SymbolQuote,
    PriceBar,
    IntradayBar,
    TechnicalIndicators,
    SymbolNewsItem,
    EarningsEvent,
    AnalystRating,
    OptionsSnapshot,
    FundamentalSummary,
    SymbolDetailResponse,
)

logger = logging.getLogger(__name__)


def _float(val) -> Optional[float]:
    """Safely convert a DB numeric to float."""
    return float(val) if val is not None else None


def _int(val) -> Optional[int]:
    """Safely convert a DB value to int."""
    return int(val) if val is not None else None


class SymbolService:
    async def get_symbol_detail(
        self, symbol: str, days: int = 90
    ) -> Optional[SymbolDetailResponse]:
        """Fetch comprehensive symbol data in a single DB connection."""
        symbol = symbol.upper()
        start_date = date.today() - timedelta(days=days)

        try:
            async with get_connection() as conn:
                # 1. Price history (90 days)
                price_rows = await conn.fetch(
                    """
                    SELECT date, open, high, low, close, volume
                    FROM market_bar_daily
                    WHERE symbol = $1 AND date >= $2
                    ORDER BY date ASC
                    """,
                    symbol,
                    start_date,
                )

                if not price_rows:
                    return None

                # Build quote from last 2 bars
                last = price_rows[-1]
                prev_close = _float(price_rows[-2]["close"]) if len(price_rows) >= 2 else None
                price = _float(last["close"])
                change = round(price - prev_close, 2) if prev_close else None
                change_pct = round((change / prev_close) * 100, 2) if prev_close and change is not None else None

                quote = SymbolQuote(
                    symbol=symbol,
                    price=price,
                    prev_close=prev_close,
                    change=change,
                    change_pct=change_pct,
                    volume=_int(last["volume"]),
                    date=last["date"],
                )

                price_history = [
                    PriceBar(
                        date=r["date"],
                        open=_float(r["open"]),
                        high=_float(r["high"]),
                        low=_float(r["low"]),
                        close=_float(r["close"]),
                        volume=_int(r["volume"]),
                    )
                    for r in price_rows
                ]

                # 1b. Intraday 1-minute bars (today)
                intraday_rows = await conn.fetch(
                    """
                    SELECT datetime, open, high, low, close, volume
                    FROM market_bar_1min
                    WHERE symbol = $1
                      AND datetime::date = CURRENT_DATE
                    ORDER BY datetime ASC
                    """,
                    symbol,
                )

                intraday_history = [
                    IntradayBar(
                        datetime=r["datetime"],
                        open=_float(r["open"]),
                        high=_float(r["high"]),
                        low=_float(r["low"]),
                        close=_float(r["close"]),
                        volume=_int(r["volume"]),
                    )
                    for r in intraday_rows
                ]

                # Use latest 1-min bar as live quote price if available
                if intraday_rows:
                    latest_intraday = intraday_rows[-1]
                    intraday_price = _float(latest_intraday["close"])
                    if intraday_price and intraday_price > 0:
                        change = round(intraday_price - prev_close, 2) if prev_close else None
                        change_pct = round((change / prev_close) * 100, 2) if prev_close and change is not None else None
                        quote = SymbolQuote(
                            symbol=symbol,
                            price=intraday_price,
                            prev_close=prev_close,
                            change=change,
                            change_pct=change_pct,
                            volume=_int(latest_intraday["volume"]),
                            date=last["date"],
                        )
                        price = intraday_price

                # 2. Latest technical indicators
                ind_row = await conn.fetchrow(
                    """
                    SELECT date, rsi_14, macd, macd_signal, macd_hist,
                           adx_14, atr_14, mfi_14, cmf_20,
                           stochastic_k, stochastic_d,
                           bb_upper_20, bb_middle_20, bb_lower_20, bb_width,
                           support_level, resistance_level, volume_z_20
                    FROM market_bar_daily
                    WHERE symbol = $1 AND rsi_14 IS NOT NULL
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    symbol,
                )

                indicators = None
                if ind_row:
                    indicators = TechnicalIndicators(
                        date=ind_row["date"],
                        rsi_14=_float(ind_row["rsi_14"]),
                        macd=_float(ind_row["macd"]),
                        macd_signal=_float(ind_row["macd_signal"]),
                        macd_hist=_float(ind_row["macd_hist"]),
                        adx_14=_float(ind_row["adx_14"]),
                        atr_14=_float(ind_row["atr_14"]),
                        mfi_14=_float(ind_row["mfi_14"]),
                        cmf_20=_float(ind_row["cmf_20"]),
                        stochastic_k=_float(ind_row["stochastic_k"]),
                        stochastic_d=_float(ind_row["stochastic_d"]),
                        bb_upper_20=_float(ind_row["bb_upper_20"]),
                        bb_middle_20=_float(ind_row["bb_middle_20"]),
                        bb_lower_20=_float(ind_row["bb_lower_20"]),
                        bb_width=_float(ind_row["bb_width"]),
                        support_level=_float(ind_row["support_level"]),
                        resistance_level=_float(ind_row["resistance_level"]),
                        volume_z_20=_float(ind_row["volume_z_20"]),
                    )

                # 3. Recent news (10 articles)
                news_rows = await conn.fetch(
                    """
                    SELECT n.doc_id, n.title, n.published_at, n.provider, n.url, r.score
                    FROM yfinance_news n
                    LEFT JOIN yfinance_news_relevance r ON n.doc_id = r.doc_id
                    WHERE n.ticker = $1
                    ORDER BY n.published_at DESC
                    LIMIT 10
                    """,
                    symbol,
                )

                news = [
                    SymbolNewsItem(
                        doc_id=r["doc_id"],
                        title=r["title"],
                        published_at=r["published_at"],
                        provider=r["provider"],
                        url=r["url"],
                        relevance_score=_float(r["score"]),
                    )
                    for r in news_rows
                ]

                # 4. Earnings (last 2 past + next 2 upcoming)
                earnings_rows = await conn.fetch(
                    """
                    (
                        SELECT earnings_date, time_of_day, eps_est, eps_actual,
                               revenue_est, revenue_actual
                        FROM earnings_event
                        WHERE symbol = $1 AND earnings_date < CURRENT_DATE
                        ORDER BY earnings_date DESC
                        LIMIT 2
                    )
                    UNION ALL
                    (
                        SELECT earnings_date, time_of_day, eps_est, eps_actual,
                               revenue_est, revenue_actual
                        FROM earnings_event
                        WHERE symbol = $1 AND earnings_date >= CURRENT_DATE
                        ORDER BY earnings_date ASC
                        LIMIT 2
                    )
                    """,
                    symbol,
                )

                earnings = [
                    EarningsEvent(
                        earnings_date=r["earnings_date"],
                        time_of_day=r["time_of_day"],
                        eps_est=_float(r["eps_est"]),
                        eps_actual=_float(r["eps_actual"]),
                        revenue_est=_float(r["revenue_est"]),
                        revenue_actual=_float(r["revenue_actual"]),
                    )
                    for r in earnings_rows
                ]

                # 5. Analyst ratings (10 most recent)
                rating_rows = await conn.fetch(
                    """
                    SELECT firm, rating_date, action, to_grade, from_grade, price_target
                    FROM analyst_rating
                    WHERE symbol = $1
                    ORDER BY rating_date DESC
                    LIMIT 10
                    """,
                    symbol,
                )

                analyst_ratings = [
                    AnalystRating(
                        firm=r["firm"],
                        rating_date=r["rating_date"],
                        action=r["action"],
                        to_grade=r["to_grade"],
                        from_grade=r["from_grade"],
                        price_target=_float(r["price_target"]),
                    )
                    for r in rating_rows
                ]

                # 6. Options snapshot (latest)
                opt_row = await conn.fetchrow(
                    """
                    SELECT date, put_call_ratio_volume, put_call_ratio_oi,
                           atm_iv, max_pain_strike,
                           highest_oi_call_strike, highest_oi_put_strike,
                           total_call_volume, total_put_volume,
                           total_call_oi, total_put_oi
                    FROM options_daily_summary
                    WHERE symbol = $1
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    symbol,
                )

                options = None
                if opt_row:
                    options = OptionsSnapshot(
                        as_of_date=opt_row["date"],
                        put_call_ratio_volume=_float(opt_row["put_call_ratio_volume"]),
                        put_call_ratio_oi=_float(opt_row["put_call_ratio_oi"]),
                        atm_iv=_float(opt_row["atm_iv"]),
                        max_pain_strike=_float(opt_row["max_pain_strike"]),
                        highest_oi_call_strike=_float(opt_row["highest_oi_call_strike"]),
                        highest_oi_put_strike=_float(opt_row["highest_oi_put_strike"]),
                        total_call_volume=_int(opt_row["total_call_volume"]),
                        total_put_volume=_int(opt_row["total_put_volume"]),
                        total_call_oi=_int(opt_row["total_call_oi"]),
                        total_put_oi=_int(opt_row["total_put_oi"]),
                    )

                # 7. Fundamentals summary (analyst + earnings trajectory + dividend + profile)
                fundamentals = await self._build_fundamentals(conn, symbol, price)

            return SymbolDetailResponse(
                quote=quote,
                price_history=price_history,
                intraday_history=intraday_history,
                indicators=indicators,
                news=news,
                earnings=earnings,
                analyst_ratings=analyst_ratings,
                options=options,
                fundamentals=fundamentals,
            )
        except Exception as e:
            logger.error(f"Failed to fetch symbol detail for {symbol}: {e}")
            return None


    async def _build_fundamentals(
        self, conn, symbol: str, current_price: float
    ) -> Optional[FundamentalSummary]:
        """Build fundamentals summary from analyst, earnings, dividend, profile data."""
        try:
            BUY_GRADES = {"buy", "outperform", "overweight", "strong buy", "positive", "sector outperform", "market outperform"}
            HOLD_GRADES = {"hold", "neutral", "equal-weight", "equal weight", "market perform", "sector perform", "sector weight", "in-line", "peer perform"}
            SELL_GRADES = {"sell", "underperform", "underweight", "negative", "reduce", "sector underperform", "market underperform"}

            # Analyst consensus (90 days)
            analyst_rows = await conn.fetch(
                """
                SELECT action, to_grade, price_target, rating_date
                FROM analyst_rating
                WHERE symbol = $1 AND rating_date >= NOW() - INTERVAL '90 days'
                ORDER BY rating_date DESC
                """,
                symbol,
            )

            analyst_consensus = None
            avg_price_target = None
            upside_pct = None
            upgrades_30d = 0
            downgrades_30d = 0
            buy_count = 0
            hold_count = 0
            sell_count = 0

            if analyst_rows:
                price_targets = []
                for r in analyst_rows:
                    grade = (r["to_grade"] or "").lower().strip()
                    action = (r["action"] or "").lower().strip()
                    pt = r["price_target"]

                    if pt is not None:
                        price_targets.append(float(pt))

                    if grade in BUY_GRADES:
                        buy_count += 1
                    elif grade in HOLD_GRADES:
                        hold_count += 1
                    elif grade in SELL_GRADES:
                        sell_count += 1

                    cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
                    if r["rating_date"] and r["rating_date"] >= cutoff_30d:
                        if action in ("up", "upgrade"):
                            upgrades_30d += 1
                        elif action in ("down", "downgrade"):
                            downgrades_30d += 1

                if price_targets:
                    avg_price_target = round(sum(price_targets) / len(price_targets), 2)
                    if current_price and current_price > 0:
                        upside_pct = round((avg_price_target - current_price) / current_price * 100, 1)

                total_graded = buy_count + hold_count + sell_count
                if total_graded == 0:
                    analyst_consensus = "MIXED"
                elif buy_count > hold_count and buy_count > sell_count:
                    analyst_consensus = "BUY"
                elif sell_count > hold_count and sell_count > buy_count:
                    analyst_consensus = "SELL"
                elif hold_count >= buy_count and hold_count >= sell_count:
                    analyst_consensus = "HOLD"
                else:
                    analyst_consensus = "MIXED"

            # Earnings trajectory (last 8 quarters)
            earn_rows = await conn.fetch(
                """
                SELECT eps_actual, eps_est, revenue_growth_yoy
                FROM earnings_event
                WHERE symbol = $1 AND earnings_date < CURRENT_DATE AND eps_actual IS NOT NULL
                ORDER BY earnings_date DESC
                LIMIT 8
                """,
                symbol,
            )

            beat_rate = None
            eps_trend = None
            revenue_trend = None
            consecutive_beats = None

            if len(earn_rows) >= 2:
                beats = 0
                total = 0
                consecutive_beats = 0
                for r in earn_rows:
                    ea = _float(r["eps_actual"])
                    ee = _float(r["eps_est"])
                    if ea is not None and ee is not None:
                        total += 1
                        if ea > ee:
                            beats += 1

                # Consecutive beats/misses
                for r in earn_rows:
                    ea = _float(r["eps_actual"])
                    ee = _float(r["eps_est"])
                    if ea is None or ee is None:
                        break
                    if ea > ee:
                        if consecutive_beats >= 0:
                            consecutive_beats += 1
                        else:
                            break
                    else:
                        if consecutive_beats <= 0:
                            consecutive_beats -= 1
                        else:
                            break

                if total >= 2:
                    beat_rate = round(beats / total * 100, 0)

                # EPS trend
                eps_vals = [_float(r["eps_actual"]) for r in earn_rows if r["eps_actual"] is not None]
                if len(eps_vals) >= 4:
                    recent = list(reversed(eps_vals[:4]))
                    inc = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
                    dec = sum(1 for i in range(1, len(recent)) if recent[i] < recent[i - 1])
                    eps_trend = "ACCELERATING" if inc >= 2 and inc > dec else "DECELERATING" if dec >= 2 and dec > inc else "STABLE"

                # Revenue trend
                rev_vals = [_float(r["revenue_growth_yoy"]) for r in earn_rows if r["revenue_growth_yoy"] is not None]
                if len(rev_vals) >= 3:
                    recent = list(reversed(rev_vals[:4]))
                    inc = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
                    dec = sum(1 for i in range(1, len(recent)) if recent[i] < recent[i - 1])
                    revenue_trend = "ACCELERATING" if inc >= 2 and inc > dec else "DECELERATING" if dec >= 2 and dec > inc else "STABLE"

            # Dividend (last 12 months)
            div_rows = await conn.fetch(
                """
                SELECT cash_amount, ex_date
                FROM corporate_action
                WHERE ticker = $1 AND action_type = 'dividend'
                    AND ex_date >= CURRENT_DATE - INTERVAL '365 days'
                ORDER BY ex_date DESC
                """,
                symbol,
            )

            has_dividend = len(div_rows) > 0
            dividend_yield = None
            annual_dividend = None
            if div_rows:
                amounts = [float(r["cash_amount"]) for r in div_rows[:4] if r["cash_amount"] is not None]
                if amounts:
                    annual_dividend = round(sum(amounts), 4)
                    if current_price and current_price > 0:
                        dividend_yield = round(annual_dividend / current_price * 100, 2)

            # Profile fundamentals
            revenue_growth_3y = None
            profitability_trend = None
            profile_row = await conn.fetchrow(
                "SELECT profile_data FROM ticker_profile WHERE symbol = $1",
                symbol,
            )
            if profile_row and profile_row["profile_data"]:
                pdata = profile_row["profile_data"]
                if isinstance(pdata, str):
                    try:
                        pdata = json.loads(pdata)
                    except (json.JSONDecodeError, TypeError):
                        pdata = {}
                fund = pdata.get("fundamentals", {}) if isinstance(pdata, dict) else {}
                revenue_growth_3y = _float(fund.get("revenue_growth_3y") or pdata.get("revenue_growth_3y"))
                profitability_trend = fund.get("profitability_trend") or (pdata.get("profitability_trend") if isinstance(pdata, dict) else None)

            # Only return if we have any data
            has_any = (analyst_consensus or beat_rate is not None or has_dividend
                       or revenue_growth_3y is not None or profitability_trend)
            if not has_any:
                return None

            return FundamentalSummary(
                analyst_consensus=analyst_consensus,
                avg_price_target=avg_price_target,
                upside_pct=upside_pct,
                upgrades_30d=upgrades_30d,
                downgrades_30d=downgrades_30d,
                buy_count=buy_count,
                hold_count=hold_count,
                sell_count=sell_count,
                beat_rate=beat_rate,
                eps_trend=eps_trend,
                revenue_trend=revenue_trend,
                consecutive_beats=consecutive_beats,
                has_dividend=has_dividend,
                dividend_yield=dividend_yield,
                annual_dividend=annual_dividend,
                revenue_growth_3y=revenue_growth_3y,
                profitability_trend=profitability_trend,
            )
        except Exception as e:
            logger.warning(f"Failed to build fundamentals for {symbol}: {e}")
            return None


symbol_service = SymbolService()
