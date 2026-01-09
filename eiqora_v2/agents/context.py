"""
Context Agent implementation.
Provides deterministic stock-level context features for swing planning.
"""

from datetime import date
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.config.universe import get_sector_etf
from eiqora_v2.schemas.context import (
    ContextOutput,
    VolBasis,
    TrendStatus,
    MomentumMetrics,
    RelativeStrengthMetrics,
    RelativeStrengthPair,
)
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.prices import get_indicators, get_return_metrics


class ContextAgent(BaseAgent[ContextOutput]):
    """
    Context Agent: provides deterministic context features (no LLM call).
    
    Gathers:
    - Volatility (RV20)
    - Trend status (vs MA20, MA50, MA200)
    - Momentum (20d, 60d returns)
    - Volume profile
    - State tags
    """
    
    name = "context"
    output_schema = ContextOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Fetch daily technical indicators."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Get daily indicators
        indicators = await get_indicators(symbol, 60, asof_time)
        
        return {
            "daily": indicators,
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Unused (context output is deterministic)."""
        return ""

    def _get_system_prompt(self) -> str:
        """Unused (context output is deterministic)."""
        return ""

    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """Compute deterministic context output from indicators."""
        symbol = state.get("symbol")
        asof_time = state.get("asof_time")

        try:
            data = await self._gather_data(state)
            daily = data.get("daily", {})

            if daily.get("error"):
                msg = daily.get("error", "Unknown error")
                self.logger.warning(f"{symbol}: Context data error - {msg}")
                return {
                    "context": {"error": msg, "data_points": daily.get("data_points", 0)},
                    "errors": state.get("errors", []) + [f"context: {msg}"],
                }

            current_price = float(daily.get("current_price") or 0)
            if current_price <= 0:
                raise ValueError("Missing or invalid current_price")

            vol_basis = self._build_vol_basis(daily, current_price)
            trend = self._build_trend(daily, current_price)
            momentum = MomentumMetrics(
                ret_20d=daily.get("ret_20d"),
                ret_60d=daily.get("ret_60d"),
            )
            rel_strength = await self._build_relative_strength(
                symbol,
                daily,
                asof_time,
            )

            output = ContextOutput(
                vol_basis=vol_basis,
                trend=trend,
                momentum=momentum,
                volume_z_20d=float(daily.get("volume_z_20d") or 0.0),
                state_tags=list(daily.get("state_tags") or []),
                current_price=current_price,
                relative_strength=rel_strength,
                data_quality=self._data_quality(daily, asof_time),
            )

            return {"context": output.model_dump()}

        except Exception as e:
            self.logger.error(f"{symbol}: Context computation failed - {e}")
            return {
                "context": {"error": str(e)},
                "errors": state.get("errors", []) + [f"context: {e}"],
            }

    def _build_vol_basis(self, daily: dict[str, Any], current_price: float) -> VolBasis:
        rv20 = daily.get("rv20")
        if rv20 is not None and rv20 > 0:
            return VolBasis(type="RV20", value=float(rv20))

        atr14 = daily.get("atr14")
        if atr14 is not None and atr14 > 0 and current_price > 0:
            return VolBasis(type="ATR14_PROXY", value=float(atr14) / current_price)

        return VolBasis(type="RV20", value=0.0)

    def _build_trend(self, daily: dict[str, Any], current_price: float) -> TrendStatus:
        trend = daily.get("trend") or {}

        def _coerce(value: Any, fallback: str | None) -> str | None:
            return value if value in ("ABOVE", "BELOW") else fallback

        ma20 = daily.get("ma20")
        ma20_state = _coerce(trend.get("ma20"), None)
        if ma20_state is None:
            ma20_state = "ABOVE" if ma20 and current_price > ma20 else "BELOW"

        ma50_state = _coerce(trend.get("ma50"), None)
        ma200_state = _coerce(trend.get("ma200"), None)

        return TrendStatus(ma20=ma20_state, ma50=ma50_state, ma200=ma200_state)

    def _data_quality(self, daily: dict[str, Any], asof_time: Any) -> str:
        data_points = int(daily.get("data_points") or 0)
        if data_points < 20:
            return "SPARSE"

        last_date = daily.get("last_date")
        if isinstance(last_date, str):
            try:
                last_date = date.fromisoformat(last_date)
            except ValueError:
                last_date = None

        if isinstance(last_date, date) and asof_time:
            gap_days = (asof_time.date() - last_date).days
            if gap_days >= 3:
                return "STALE"

        return "GOOD"

    async def _build_relative_strength(
        self,
        symbol: str,
        daily: dict[str, Any],
        asof_time: Any,
    ) -> RelativeStrengthMetrics | None:
        if not asof_time:
            return None

        symbol_ret_20d = daily.get("ret_20d")
        symbol_ret_60d = daily.get("ret_60d")
        symbol_last_date = daily.get("last_date")
        if isinstance(symbol_last_date, str):
            try:
                symbol_last_date = date.fromisoformat(symbol_last_date)
            except ValueError:
                symbol_last_date = None

        sector_etf = get_sector_etf(symbol)
        benchmarks = {
            "SPY": "vs_spy",
            sector_etf: "vs_sector",
            "QQQ": "vs_qqq",
        }

        rel = RelativeStrengthMetrics(sector_etf=sector_etf)
        missing: list[str] = []

        for bench_symbol, field_name in benchmarks.items():
            metrics = await get_return_metrics(bench_symbol, 120, asof_time)
            if metrics.get("error"):
                missing.append(bench_symbol)
                pair = RelativeStrengthPair(
                    benchmark=bench_symbol,
                    data_quality="MISSING",
                )
            else:
                bench_ret_20d = metrics.get("ret_20d")
                bench_ret_60d = metrics.get("ret_60d")
                bench_last_date = metrics.get("last_date")

                data_quality = "OK"
                if symbol_last_date and bench_last_date:
                    gap_days = abs((symbol_last_date - bench_last_date).days)
                    if gap_days >= 3:
                        data_quality = "STALE"

                pair = RelativeStrengthPair(
                    benchmark=bench_symbol,
                    symbol_ret_20d=symbol_ret_20d,
                    benchmark_ret_20d=bench_ret_20d,
                    rel_ret_20d=(
                        symbol_ret_20d - bench_ret_20d
                        if symbol_ret_20d is not None and bench_ret_20d is not None
                        else None
                    ),
                    symbol_ret_60d=symbol_ret_60d,
                    benchmark_ret_60d=bench_ret_60d,
                    rel_ret_60d=(
                        symbol_ret_60d - bench_ret_60d
                        if symbol_ret_60d is not None and bench_ret_60d is not None
                        else None
                    ),
                    data_quality=data_quality,
                )

            setattr(rel, field_name, pair)

        rel.missing = missing
        return rel
