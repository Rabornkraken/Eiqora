"""
Fundamental Agent implementation.
Provides ticker-level sentiment and fundamental overview.

READ-ONLY: Does NOT trigger data collection. Reads from pre-populated database tables:
- document (news articles)
- earnings_event
- sec_filing (via CIK join)
"""

from datetime import datetime, timedelta
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.fundamental import (
    FundamentalOutput,
    SentimentSummary,
    SECFilingSummary,
    DataStatus,
    EarningsSnapshot,
    InsiderSummary,
    AnalystConsensus,
    EarningsTrajectory,
    DividendSummary,
    ProfileFundamentals,
)
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.documents import get_documents, count_recent_documents
from eiqora_v2.tools.events import get_sec_filings
from eiqora_v2.tools.db import get_connection


class FundamentalAgent(BaseAgent[FundamentalOutput]):
    """
    Fundamental Agent: provides sentiment and fundamental overview.
    
    READ-ONLY - does NOT trigger data collection.
    Assumes data is pre-populated by BacktestDataCollector or scheduled pipelines.
    
    Reads from:
    - yfinance_news table (news with FinBERT sentiment)
    - earnings_event table
    - sec_filing table
    
    Returns structured output with available data, or indicates data is missing.
    """
    
    name = "fundamental"
    output_schema = FundamentalOutput
    
    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """Read fundamental data from database (no collection).

        Uses the cached profile as baseline and only fetches **new data
        since ``profile.last_updated``**.  If no profile exists in state,
        one is created via ``ProfileGenerator`` so the delta path is
        always used.
        """
        from zoneinfo import ZoneInfo
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        # Ensure asof_time is timezone-aware (UTC) for DB comparisons
        if asof_time.tzinfo is None:
            asof_time = asof_time.replace(tzinfo=ZoneInfo("UTC"))

        # Get profile for baseline context (updated weekly)
        profile = state.get("profile") or {}

        self.logger.info(f"Running {self.name} for {symbol}")

        # Ensure a profile exists — create one if missing
        if not profile or profile.get("last_updated") is None:
            self.logger.info(f"  No profile in state — generating profile for {symbol}")
            try:
                from eiqora_v2.services.profile_generator import ProfileGenerator
                generator = ProfileGenerator()
                fresh_profile = await generator.get_profile(symbol)
                profile = fresh_profile.model_dump()
            except Exception as e:
                self.logger.warning(f"  Profile generation failed: {e} — using empty baseline")
                profile = {}

        baseline_risks = profile.get("risks", [])
        baseline_catalysts = profile.get("catalysts", [])

        # Determine query windows based on profile freshness
        last_updated = profile.get("last_updated")
        if last_updated is not None:
            # Ensure last_updated is also timezone-aware for comparison
            if hasattr(last_updated, 'tzinfo') and last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=ZoneInfo("UTC"))
            delta_seconds = (asof_time - last_updated).total_seconds()
            delta_hours = max(1.0, delta_seconds / 3600)
            delta_days = max(1, int(delta_seconds / 86400))
            self.logger.info(
                f"  Delta mode: profile {delta_hours:.1f}h old "
                f"({len(baseline_risks)} risks, {len(baseline_catalysts)} catalysts)"
            )
        else:
            # Profile generation failed — use default full windows as safety net
            delta_hours = 72.0   # 3 days
            delta_days = 30
            self.logger.info("  Full query mode (profile generation failed)")

        try:
            # Step 1: Check what data is available (READ-ONLY)
            doc_counts = await count_recent_documents(symbol, 168, asof_time)  # 7 days
            news_count = sum(doc_counts.values())

            # Step 2: Get recent news for sentiment (delta-aware)
            news_docs = await get_documents(symbol, delta_hours, asof_time, limit=20)

            # Step 3: Get SEC filings (delta-aware)
            sec_filings = await get_sec_filings(symbol, delta_days, asof_time)

            # Step 3b: Get latest earnings snapshot
            # Skip if profile already has earnings for the same quarter
            earnings_snapshot = None
            profile_earnings = profile.get("earnings") if isinstance(profile, dict) else None
            profile_fq = profile_earnings.get("fiscal_quarter") if isinstance(profile_earnings, dict) else None

            latest_earnings = await self._get_latest_earnings(symbol, asof_time)
            if latest_earnings is not None:
                if profile_fq is None or latest_earnings.fiscal_quarter != profile_fq:
                    earnings_snapshot = latest_earnings
                else:
                    # Same quarter as profile — still use the DB result
                    earnings_snapshot = latest_earnings

            # Step 3c: Get insider summary (delta-aware window)
            insider_window = delta_days if last_updated is not None else 90
            insider_summary = await self._get_insider_summary(symbol, asof_time, window_days=insider_window)

            # Step 3d: Get analyst consensus (90d window)
            current_price = (state.get("context") or {}).get("current_price")
            if current_price is None:
                current_price = await self._get_current_price(symbol, asof_time)
            analyst_consensus = await self._get_analyst_consensus(symbol, asof_time, current_price)

            # Step 3e: Get earnings trajectory (last 8 quarters)
            earnings_traj = await self._get_earnings_trajectory(symbol, asof_time)

            # Step 3f: Get dividend summary
            dividend_summary = await self._get_dividend_summary(symbol, asof_time, current_price)

            # Step 3g: Get profile fundamentals
            profile_fundamentals = self._get_profile_fundamentals(profile)

            # Step 4: Analyze sentiment from news headlines
            sentiment_overall, pos_count, neg_count, neutral_count = await self._analyze_sentiment(news_docs)
            
            # Step 5: Check data freshness (but don't trigger collection)
            news_fresh = news_count > 0
            news_last_at = news_docs[0]["published_at"] if news_docs else None
            sec_fresh = len(sec_filings) > 0
            sec_last_at = sec_filings[0]["filed_at"] if sec_filings else None
            earnings_fresh = earnings_snapshot is not None
            
            # Build output (combine profile baseline + fresh data)
            output = FundamentalOutput(
                symbol=symbol,
                sentiment=SentimentSummary(
                    overall=sentiment_overall,
                    news_count=news_count,
                    positive_count=pos_count,
                    negative_count=neg_count,
                    neutral_count=neutral_count,
                    key_topics=self._extract_topics(news_docs),
                    notable_headlines=[d["title"] for d in news_docs[:3]],
                    baseline_risks=baseline_risks,  # From profile
                    baseline_catalysts=baseline_catalysts,  # From profile
                ),
                earnings=earnings_snapshot,
                sec_filings=SECFilingSummary(
                    recent_filings=[
                        {"form_type": f["form_type"], "filed_at": str(f["filed_at"])}
                        for f in sec_filings[:5]
                    ],
                    has_8k=any(f["form_type"] == "8-K" for f in sec_filings),
                    has_10q=any(f["form_type"] == "10-Q" for f in sec_filings),
                    has_10k=any(f["form_type"] == "10-K" for f in sec_filings),
                ),
                insider=insider_summary,
                analyst=analyst_consensus,
                earnings_trajectory=earnings_traj,
                dividend=dividend_summary,
                profile_fundamentals=profile_fundamentals,
                data_status=DataStatus(
                    news_fresh=news_fresh,
                    news_last_at=news_last_at,
                    sec_fresh=sec_fresh,
                    sec_last_at=sec_last_at,
                    earnings_fresh=earnings_fresh,
                    collections_triggered=[],  # NO collections triggered
                    collection_errors=[],
                ),
                analysis_timestamp=datetime.utcnow(),
                error=None,
            )
            
            self.logger.info(f"{self.name} completed successfully (news={news_count}, sec={len(sec_filings)})")
            return {
                "fundamental": output.model_dump(),
            }
            
        except Exception as e:
            self.logger.error(f"{self.name} failed: {e}")
            return {
                "fundamental": FundamentalOutput(
                    symbol=symbol,
                    sentiment=SentimentSummary(overall="NEUTRAL", news_count=0),
                    data_status=DataStatus(
                        news_fresh=False,
                        sec_fresh=False,
                        earnings_fresh=False,
                        collections_triggered=[],
                        collection_errors=[str(e)],
                    ),
                    analysis_timestamp=datetime.utcnow(),
                    error=str(e),
                ).model_dump()
            }
    
    async def _analyze_sentiment(self, news_docs: list[dict]) -> tuple[str, int, int, int]:
        """Simple keyword-based sentiment analysis with counts."""
        if not news_docs:
            return "NEUTRAL", 0, 0, 0
        
        positive_keywords = ["beat", "surge", "jump", "upgrade", "bullish", "strong", "growth", "record"]
        negative_keywords = ["miss", "drop", "fall", "downgrade", "bearish", "weak", "decline", "cut"]
        
        positive_count = 0
        negative_count = 0
        
        for doc in news_docs:
            title = (doc.get("title") or "").lower()
            for kw in positive_keywords:
                if kw in title:
                    positive_count += 1
            for kw in negative_keywords:
                if kw in title:
                    negative_count += 1
        
        neutral_count = max(0, len(news_docs) - positive_count - negative_count)

        if positive_count > negative_count + 2:
            return "POSITIVE", positive_count, negative_count, neutral_count
        if negative_count > positive_count + 2:
            return "NEGATIVE", positive_count, negative_count, neutral_count
        return "NEUTRAL", positive_count, negative_count, neutral_count
    
    def _extract_topics(self, news_docs: list[dict]) -> list[str]:
        """Extract key topics from news titles."""
        topics = []
        for doc in news_docs[:5]:
            title = doc.get("title", "")[:50]
            if title:
                topics.append(title)
        return topics

    async def _get_latest_earnings(
        self,
        symbol: str,
        asof_time: datetime,
    ) -> EarningsSnapshot | None:
        """Fetch latest earnings snapshot from earnings_event, if available."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM earnings_event
                WHERE symbol = $1
                  AND earnings_date <= $2::date
                ORDER BY earnings_date DESC
                LIMIT 1
                """,
                symbol,
                asof_time,
            )
            if not row:
                return None
            data = dict(row)

        eps_actual = data.get("eps_actual")
        eps_est = data.get("eps_est")
        revenue_actual = data.get("revenue_actual")
        revenue_est = data.get("revenue_est")

        eps_surprise_pct = None
        if eps_actual is not None and eps_est not in (None, 0):
            eps_surprise_pct = (float(eps_actual) - float(eps_est)) / abs(float(eps_est)) * 100

        revenue_growth_yoy = data.get("revenue_growth_yoy")
        if revenue_growth_yoy is not None:
            revenue_growth_yoy = float(revenue_growth_yoy)

        return EarningsSnapshot(
            fiscal_quarter=data.get("fiscal_quarter"),
            eps_actual=float(eps_actual) if eps_actual is not None else None,
            eps_estimate=float(eps_est) if eps_est is not None else None,
            eps_surprise_pct=eps_surprise_pct,
            revenue_actual=float(revenue_actual) if revenue_actual is not None else None,
            revenue_growth_yoy=revenue_growth_yoy,
            guidance=data.get("guidance"),
        )

    async def _get_insider_summary(
        self,
        symbol: str,
        asof_time: datetime,
        window_days: int = 90,
    ) -> InsiderSummary | None:
        """Aggregate insider transactions by role and direction."""
        start_date = asof_time - timedelta(days=window_days)
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT i.owner_name, i.transaction_date, i.code, i.shares, i.price
                FROM insider_transaction i
                JOIN security s ON i.cik = s.cik
                WHERE s.ticker = $1
                  AND i.transaction_date BETWEEN $2 AND $3
                ORDER BY i.transaction_date DESC
                """,
                symbol,
                start_date,
                asof_time,
            )

        if not rows:
            return InsiderSummary(available=False, transactions_90d=0)

        ceo_value = 0.0
        cfo_value = 0.0
        director_value = 0.0
        other_value = 0.0

        total_buy_value = 0.0
        total_sell_value = 0.0
        buy_count = 0
        sell_count = 0

        for r in rows:
            shares = float(r["shares"] or 0)
            price = float(r["price"] or 0)
            value = shares * price

            name_lower = (r["owner_name"] or "").lower()
            code = r["code"] or ""

            is_buy = code in ("P", "A")
            is_sell = code in ("S", "F")

            if is_buy:
                total_buy_value += value
                buy_count += 1
                net_value = value
            elif is_sell:
                total_sell_value += value
                sell_count += 1
                net_value = -value
            else:
                continue

            if "ceo" in name_lower or "chief executive" in name_lower:
                ceo_value += net_value
            elif "cfo" in name_lower or "chief financial" in name_lower:
                cfo_value += net_value
            elif "director" in name_lower:
                director_value += net_value
            else:
                other_value += net_value

        return InsiderSummary(
            available=True,
            transactions_90d=len(rows),
            buy_count=buy_count,
            sell_count=sell_count,
            total_buy_value=total_buy_value,
            total_sell_value=total_sell_value,
            net_value=total_buy_value - total_sell_value,
            ceo_net_value=ceo_value,
            cfo_net_value=cfo_value,
            director_net_value=director_value,
            other_net_value=other_value,
        )
    
    async def _get_current_price(self, symbol: str, asof_time: datetime) -> float | None:
        """Fetch latest close price from market_bar_daily."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT close
                FROM market_bar_daily
                WHERE symbol = $1 AND date <= $2::date
                ORDER BY date DESC
                LIMIT 1
                """,
                symbol,
                asof_time,
            )
        return float(row["close"]) if row else None

    async def _get_analyst_consensus(
        self,
        symbol: str,
        asof_time: datetime,
        current_price: float | None,
    ) -> AnalystConsensus | None:
        """Aggregate analyst ratings into consensus view."""
        window_90d = asof_time - timedelta(days=90)
        window_30d = asof_time - timedelta(days=30)

        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT firm, rating_date, action, to_grade, from_grade, price_target
                FROM analyst_rating
                WHERE symbol = $1 AND rating_date >= $2
                ORDER BY rating_date DESC
                """,
                symbol,
                window_90d,
            )

        if not rows:
            return None

        BUY_GRADES = {"buy", "outperform", "overweight", "strong buy", "positive", "sector outperform", "market outperform"}
        HOLD_GRADES = {"hold", "neutral", "equal-weight", "equal weight", "market perform", "sector perform", "sector weight", "in-line", "peer perform"}
        SELL_GRADES = {"sell", "underperform", "underweight", "negative", "reduce", "sector underperform", "market underperform"}

        buy_count = 0
        hold_count = 0
        sell_count = 0
        upgrade_30d = 0
        downgrade_30d = 0
        price_targets = []

        for r in rows:
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

            if r["rating_date"] >= window_30d:
                if action in ("up", "upgrade"):
                    upgrade_30d += 1
                elif action in ("down", "downgrade"):
                    downgrade_30d += 1

        avg_pt = round(sum(price_targets) / len(price_targets), 2) if price_targets else None
        upside_pct = None
        if avg_pt is not None and current_price and current_price > 0:
            upside_pct = round((avg_pt - current_price) / current_price * 100, 1)

        total_graded = buy_count + hold_count + sell_count
        if total_graded == 0:
            consensus = "MIXED"
        elif buy_count > hold_count and buy_count > sell_count:
            consensus = "BUY"
        elif sell_count > hold_count and sell_count > buy_count:
            consensus = "SELL"
        elif hold_count >= buy_count and hold_count >= sell_count:
            consensus = "HOLD"
        else:
            consensus = "MIXED"

        recent_actions = [
            {
                "firm": r["firm"],
                "action": r["action"],
                "to_grade": r["to_grade"],
                "price_target": float(r["price_target"]) if r["price_target"] is not None else None,
                "rating_date": str(r["rating_date"]),
            }
            for r in rows[:5]
        ]

        return AnalystConsensus(
            total_ratings_90d=len(rows),
            avg_price_target=avg_pt,
            upside_pct=upside_pct,
            upgrade_count_30d=upgrade_30d,
            downgrade_count_30d=downgrade_30d,
            buy_count=buy_count,
            hold_count=hold_count,
            sell_count=sell_count,
            consensus=consensus,
            recent_actions=recent_actions,
        )

    async def _get_earnings_trajectory(
        self,
        symbol: str,
        asof_time: datetime,
    ) -> EarningsTrajectory | None:
        """Fetch last 8 quarters of earnings and compute trajectory."""
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT fiscal_quarter, earnings_date, eps_actual, eps_est,
                       revenue_actual, revenue_est, revenue_growth_yoy, guidance
                FROM earnings_event
                WHERE symbol = $1 AND earnings_date <= $2::date
                ORDER BY earnings_date DESC
                LIMIT 8
                """,
                symbol,
                asof_time,
            )

        if not rows:
            return None

        quarters = []
        beats = 0
        total_with_estimates = 0
        consecutive_beats = 0
        streak_broken = False

        for r in rows:
            eps_actual = float(r["eps_actual"]) if r["eps_actual"] is not None else None
            eps_est = float(r["eps_est"]) if r["eps_est"] is not None else None
            rev_actual = float(r["revenue_actual"]) if r["revenue_actual"] is not None else None
            rev_growth = float(r["revenue_growth_yoy"]) if r["revenue_growth_yoy"] is not None else None

            eps_surprise_pct = None
            if eps_actual is not None and eps_est not in (None, 0):
                eps_surprise_pct = round((eps_actual - eps_est) / abs(eps_est) * 100, 1)

            quarters.append({
                "fiscal_quarter": r["fiscal_quarter"],
                "eps_actual": eps_actual,
                "eps_est": eps_est,
                "eps_surprise_pct": eps_surprise_pct,
                "revenue_actual": rev_actual,
                "revenue_growth_yoy": rev_growth,
                "guidance": r["guidance"],
            })

            if eps_actual is not None and eps_est is not None:
                total_with_estimates += 1
                if eps_actual > eps_est:
                    beats += 1
                    if not streak_broken:
                        consecutive_beats += 1
                else:
                    if not streak_broken:
                        streak_broken = True
                        if consecutive_beats == 0:
                            consecutive_beats = -1
                        else:
                            pass  # streak already counted
                    elif consecutive_beats <= 0:
                        consecutive_beats -= 1

        # Re-compute consecutive streak more precisely
        consecutive_beats = 0
        for r in rows:
            eps_actual = float(r["eps_actual"]) if r["eps_actual"] is not None else None
            eps_est = float(r["eps_est"]) if r["eps_est"] is not None else None
            if eps_actual is None or eps_est is None:
                break
            if eps_actual > eps_est:
                if consecutive_beats >= 0:
                    consecutive_beats += 1
                else:
                    break
            else:
                if consecutive_beats <= 0:
                    consecutive_beats -= 1
                else:
                    break

        beat_rate = round(beats / total_with_estimates * 100, 0) if total_with_estimates >= 2 else None

        # EPS trend: compare recent 4 vs prior 4 EPS growth
        eps_trend = self._compute_trend([q.get("eps_actual") for q in quarters])
        revenue_trend = self._compute_trend_from_growth([q.get("revenue_growth_yoy") for q in quarters])

        return EarningsTrajectory(
            quarters=quarters,
            beat_rate=beat_rate,
            consecutive_beats=consecutive_beats,
            eps_trend=eps_trend,
            revenue_trend=revenue_trend,
            quarters_available=len(rows),
        )

    def _compute_trend(self, values: list[float | None]) -> str:
        """Determine trend from a sequence of values (most recent first)."""
        clean = [v for v in values if v is not None]
        if len(clean) < 4:
            return "INSUFFICIENT_DATA"
        recent = clean[:4]
        # Check if recent values are generally increasing (reversed since most recent first)
        recent_rev = list(reversed(recent))
        increases = sum(1 for i in range(1, len(recent_rev)) if recent_rev[i] > recent_rev[i - 1])
        decreases = sum(1 for i in range(1, len(recent_rev)) if recent_rev[i] < recent_rev[i - 1])
        if increases >= 2 and increases > decreases:
            return "ACCELERATING"
        elif decreases >= 2 and decreases > increases:
            return "DECELERATING"
        return "STABLE"

    def _compute_trend_from_growth(self, growth_rates: list[float | None]) -> str:
        """Determine revenue trend from YoY growth rates."""
        clean = [g for g in growth_rates if g is not None]
        if len(clean) < 3:
            return "INSUFFICIENT_DATA"
        recent = clean[:4]
        recent_rev = list(reversed(recent))
        increases = sum(1 for i in range(1, len(recent_rev)) if recent_rev[i] > recent_rev[i - 1])
        decreases = sum(1 for i in range(1, len(recent_rev)) if recent_rev[i] < recent_rev[i - 1])
        if increases >= 2 and increases > decreases:
            return "ACCELERATING"
        elif decreases >= 2 and decreases > increases:
            return "DECELERATING"
        return "STABLE"

    async def _get_dividend_summary(
        self,
        symbol: str,
        asof_time: datetime,
        current_price: float | None,
    ) -> DividendSummary | None:
        """Fetch dividend info from corporate_action table."""
        window_1y = asof_time - timedelta(days=365)
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT ex_date, cash_amount, pay_date
                FROM corporate_action
                WHERE ticker = $1 AND action_type = 'dividend'
                ORDER BY ex_date DESC
                LIMIT 8
                """,
                symbol,
            )

        if not rows:
            return DividendSummary(has_dividend=False)

        # Count payments in last 12 months
        payments_1y = [r for r in rows if r["ex_date"] and r["ex_date"] >= window_1y.date()]
        payment_count_1y = len(payments_1y)

        # Annual dividend = sum of last 4 payments
        last_4 = [float(r["cash_amount"]) for r in rows[:4] if r["cash_amount"] is not None]
        annual_dividend = round(sum(last_4), 4) if last_4 else None

        yield_pct = None
        if annual_dividend and current_price and current_price > 0:
            yield_pct = round(annual_dividend / current_price * 100, 2)

        most_recent = rows[0]
        most_recent_amount = float(most_recent["cash_amount"]) if most_recent["cash_amount"] is not None else None
        most_recent_ex_date = str(most_recent["ex_date"]) if most_recent["ex_date"] else None

        # Check for future ex_date
        next_ex = None
        for r in rows:
            if r["ex_date"] and r["ex_date"] > asof_time.date():
                next_ex = str(r["ex_date"])
                break

        return DividendSummary(
            has_dividend=True,
            annual_dividend=annual_dividend,
            yield_pct=yield_pct,
            most_recent_amount=most_recent_amount,
            most_recent_ex_date=most_recent_ex_date,
            next_ex_date=next_ex,
            payment_count_1y=payment_count_1y,
        )

    def _get_profile_fundamentals(self, profile: dict) -> ProfileFundamentals | None:
        """Extract structured fundamentals from the cached profile."""
        if not profile:
            return None

        fundamentals = profile.get("fundamentals", {})
        if not fundamentals and not profile.get("revenue_growth_3y"):
            return None

        # earnings_trajectory may be a list (from profile_data) or string
        et = fundamentals.get("earnings_trajectory") or profile.get("earnings_trajectory")
        if et is not None and not isinstance(et, str):
            import json as _json
            et = _json.dumps(et, default=str)

        return ProfileFundamentals(
            revenue_growth_3y=fundamentals.get("revenue_growth_3y") or profile.get("revenue_growth_3y"),
            profitability_trend=fundamentals.get("profitability_trend") or profile.get("profitability_trend"),
            valuation_summary=fundamentals.get("valuation_summary") or profile.get("valuation_summary"),
            earnings_trajectory=et,
        )

    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Not used - run() is overridden."""
        return {}
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Not used - run() is overridden."""
        return ""
    
    def _build_state_update(self, state: SwingTradeState, result: FundamentalOutput) -> dict[str, Any]:
        """Build state update."""
        return {"fundamental": result.model_dump()}

