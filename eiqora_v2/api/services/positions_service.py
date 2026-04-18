"""
Positions Service - Business logic for position monitoring
Integrates with database queries and existing tools
"""
from datetime import datetime
from typing import List, Optional

from ..models.positions import (
    Position,
    PositionsResponse,
    PositionDetail,
    ExitRequest,
    ExitResponse,
)


class PositionsService:
    """Service for position management"""

    def __init__(self):
        pass

    async def get_open_positions(self) -> PositionsResponse:
        """
        Get all open positions.

        Source-of-truth is Alpaca. When Alpaca is configured, positions
        returned here are the actual broker holdings. DB trade metadata
        (stop_loss, take_profit, conviction, reasoning) is joined in when
        the DB has a matching ACTIVE row; otherwise defaults are used.

        Falls back to DB positions only when Alpaca isn't configured or
        the broker call fails.
        """
        try:
            from eiqora_v2.tools.broker import _is_alpaca_configured, get_alpaca_positions

            if _is_alpaca_configured():
                alpaca_positions = await get_alpaca_positions()
                if alpaca_positions is not None:
                    # Load DB metadata for enrichment (take_profit, stop_loss, etc.)
                    db_meta = {}
                    try:
                        from eiqora_v2.tools.db import get_connection

                        async with get_connection() as conn:
                            rows = await conn.fetch(
                                """
                                SELECT symbol, position_id, entry_date, take_profit, stop_loss,
                                       time_stop_date, conviction, reasoning
                                FROM position
                                WHERE status = 'ACTIVE'
                                """
                            )
                            for r in rows:
                                db_meta[r["symbol"]] = r
                    except Exception:
                        pass

                    positions = []
                    for p in alpaca_positions:
                        sym = p["symbol"]
                        meta = db_meta.get(sym, {})
                        entry_price = float(p["avg_entry_price"])
                        current_price = float(p["current_price"])
                        unrealized_pnl = float(p["unrealized_pl"])
                        unrealized_pnl_pct = float(p["unrealized_plpc"]) * 100.0
                        side = str(p.get("side", "long")).lower()
                        direction = "SHORT" if side == "short" else "LONG"

                        entry_date = meta.get("entry_date") if meta else None
                        days_held = 0
                        if entry_date:
                            try:
                                now = datetime.utcnow()
                                ed = entry_date.replace(tzinfo=None) if hasattr(entry_date, "tzinfo") else entry_date
                                days_held = max(0, (now - ed).days)
                            except Exception:
                                days_held = 0

                        positions.append(
                            Position(
                                position_id=str(meta.get("position_id")) if meta.get("position_id") else sym,
                                symbol=sym,
                                direction=direction,
                                entry_date=entry_date or datetime.utcnow(),
                                entry_price=entry_price,
                                current_price=current_price,
                                unrealized_pnl_pct=unrealized_pnl_pct,
                                unrealized_pnl=unrealized_pnl,
                                take_profit=float(meta.get("take_profit") or 0),
                                stop_loss=float(meta.get("stop_loss") or 0),
                                time_stop_date=meta.get("time_stop_date") or datetime.utcnow(),
                                days_held=days_held,
                                status="ACTIVE",
                                conviction=meta.get("conviction") or "MEDIUM",
                                reasoning=meta.get("reasoning") or "",
                            )
                        )

                    return PositionsResponse(
                        positions=positions,
                        total=len(positions),
                        total_unrealized_pnl=float(sum(p.unrealized_pnl for p in positions)),
                    )

            # Fallback: DB path (original behavior)
            from eiqora_v2.tools.positions import get_open_positions as fetch_open_positions

            rows = await fetch_open_positions()
            positions = [
                Position(
                    position_id=row["position_id"],
                    symbol=row["symbol"],
                    direction=row["direction"],
                    entry_date=row.get("entry_date") or datetime.utcnow(),
                    entry_price=float(row.get("entry_price") or 0),
                    current_price=float(row.get("current_price") or 0),
                    unrealized_pnl_pct=float(row.get("unrealized_pnl_pct") or 0),
                    unrealized_pnl=float(row.get("unrealized_pnl") or 0),
                    take_profit=float(row.get("take_profit") or 0),
                    stop_loss=float(row.get("stop_loss") or 0),
                    time_stop_date=row.get("time_stop_date") or datetime.utcnow(),
                    days_held=int(row.get("days_held") or 0),
                    status=row.get("status") or "ACTIVE",
                    conviction=row.get("conviction") or "MEDIUM",
                    reasoning=row.get("reasoning") or "",
                )
                for row in rows
            ]

            return PositionsResponse(
                positions=positions,
                total=len(positions),
                total_unrealized_pnl=float(sum(p.unrealized_pnl for p in positions)),
            )

        except Exception as e:
            import traceback
            print(f"Error fetching positions: {e}")
            traceback.print_exc()
            return PositionsResponse(
                positions=[],
                total=0,
                total_unrealized_pnl=0.0,
            )

    async def get_position_details(self, position_id: str) -> Optional[PositionDetail]:
        """
        Get detailed information for a specific position

        Args:
            position_id: Position identifier

        Returns:
            PositionDetail or None if not found
        """
        try:
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                # Query position details
                row = await conn.fetchrow(
                    """
                    SELECT
                        p.position_id,
                        p.symbol,
                        p.direction,
                        p.entry_date,
                        p.entry_price,
                        p.current_price,
                        p.unrealized_pnl_pct,
                        p.unrealized_pnl,
                        p.take_profit,
                        p.stop_loss,
                        p.time_stop_date,
                        p.status,
                        p.conviction,
                        p.reasoning,
                        p.max_profit_pct,
                        p.max_loss_pct,
                        p.last_updated
                    FROM position p
                    WHERE p.position_id = $1
                    """,
                    position_id,
                )

                if not row:
                    return None

                # Get sector from ticker info
                sector_row = await conn.fetchrow(
                    """
                    SELECT sector FROM ticker_info WHERE symbol = $1
                    """,
                    row["symbol"],
                )
                sector = sector_row["sector"] if sector_row else None

                # Generate alerts based on position status
                alerts = []
                if row["unrealized_pnl_pct"] >= 80:
                    alerts.append("APPROACHING_TAKE_PROFIT")
                if row["unrealized_pnl_pct"] <= -70:
                    alerts.append("APPROACHING_STOP_LOSS")
                days_held = (datetime.utcnow() - row["entry_date"]).days
                if days_held >= 25:
                    alerts.append("TIME_STOP_WARNING")

                # Calculate shares and cost basis (assuming 10% risk per position)
                # In production, this should come from actual position data
                risk_amount = 1000  # Assuming $1000 risk per position
                risk_per_share = abs(row["entry_price"] - row["stop_loss"])
                shares = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
                cost_basis = shares * row["entry_price"]
                market_value = shares * row["current_price"]

                return PositionDetail(
                    position_id=row["position_id"],
                    symbol=row["symbol"],
                    direction=row["direction"],
                    entry_date=row["entry_date"],
                    entry_price=float(row["entry_price"]),
                    current_price=float(row["current_price"]),
                    unrealized_pnl_pct=float(row["unrealized_pnl_pct"]),
                    unrealized_pnl=float(row["unrealized_pnl"]),
                    take_profit=float(row["take_profit"]),
                    stop_loss=float(row["stop_loss"]),
                    time_stop_date=row["time_stop_date"],
                    days_held=days_held,
                    status=row["status"],
                    conviction=row["conviction"],
                    reasoning=row["reasoning"],
                    sector=sector,
                    shares=shares,
                    cost_basis=float(cost_basis),
                    market_value=float(market_value),
                    max_profit_pct=float(row["max_profit_pct"]),
                    max_loss_pct=float(row["max_loss_pct"]),
                    alerts=alerts,
                    last_updated=row["last_updated"],
                )

        except Exception as e:
            print(f"Error fetching position details: {e}")
            return None

    async def initiate_exit(
        self, position_id: str, request: ExitRequest
    ) -> Optional[ExitResponse]:
        """
        Initiate exit for a position

        Args:
            position_id: Position identifier
            request: Exit request with reason

        Returns:
            ExitResponse or None if position not found
        """
        try:
            from eiqora_v2.tools.positions import close_position

            result = await close_position(
                position_id=position_id,
                exit_reason=request.reason,
                exit_type=request.exit_type or "MANUAL",
                exit_time=datetime.utcnow(),
            )

            if not result:
                return None

            return ExitResponse(
                position_id=position_id,
                symbol=result["symbol"],
                exit_price=result["exit_price"],
                realized_pnl=result.get("realized_pnl") or 0.0,
                realized_pnl_pct=result.get("realized_pnl_pct") or 0.0,
                exit_date=result["exit_date"],
                reason=request.reason,
                exit_type=request.exit_type or "MANUAL",
            )

        except Exception as e:
            print(f"Error initiating position exit: {e}")
            return None


# Singleton instance
positions_service = PositionsService()
