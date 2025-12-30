"""
Batch Sweep Orchestrator - Runs analysis on all MEGA50 tickers.

Orchestrates:
1. TopDown Agent (runs once, cached)
2. Parallel per-ticker analysis
3. Portfolio Coordinator (runs once on all GO signals)
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from eiqora_v2.config.universe import MEGA50_TICKERS, get_ticker_clusters
from eiqora_v2.graph.swing_trade import run_analysis, create_initial_state
from eiqora_v2.agents.topdown import TopDownAgent
from eiqora_v2.agents.portfolio_coordinator import coordinate_signals
from eiqora_v2.schemas.portfolio import SignalSummary, PortfolioConstraints
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.db import close_pool

logger = logging.getLogger(__name__)

# Concurrency limit for parallel ticker analysis
MAX_CONCURRENT_TICKERS = 10

# TopDown agent (singleton for caching)
topdown_agent = TopDownAgent()


async def run_sweep(
    symbols: list[str] | None = None,
    asof_time: datetime | None = None,
    max_concurrent: int = MAX_CONCURRENT_TICKERS,
    constraints: PortfolioConstraints | None = None,
) -> dict[str, Any]:
    """
    Run full sweep on all tickers.
    
    Args:
        symbols: Tickers to analyze (defaults to MEGA50)
        asof_time: Point-in-time (defaults to now)
        max_concurrent: Max parallel ticker analyses
        constraints: Portfolio constraints
    
    Returns:
        Dict with:
        - topdown: TopDown analysis
        - results: Per-ticker analysis results
        - go_signals: All GO decisions (pre-coordination)
        - portfolio: Final coordinated portfolio
    """
    symbols = symbols or MEGA50_TICKERS
    asof_time = asof_time or datetime.utcnow()
    
    logger.info(f"Starting sweep for {len(symbols)} symbols as of {asof_time}")
    
    # ========================================
    # Phase 0: TopDown Agent (runs once)
    # ========================================
    logger.info("Phase 0: Running TopDown Agent")
    topdown_state = SwingTradeState(
        request_id=f"topdown_{asof_time.strftime('%Y%m%d_%H%M%S')}",
        symbol="SPY",
        sector="Market",
        sector_etf="SPY",
        asof_time=asof_time,
        trigger_type="SWEEP",
        trigger_refs=[],
        errors=[],
    )
    
    try:
        topdown_result = await topdown_agent.run(topdown_state)
        topdown = topdown_result.get("topdown", {})
    except Exception as e:
        logger.error(f"TopDown Agent failed: {e}")
        topdown = {"regime": "UNKNOWN", "bias": "NEUTRAL", "error": str(e)}
    
    logger.info(f"TopDown: regime={topdown.get('regime')}, bias={topdown.get('bias')}")
    
    # ========================================
    # Phase 1-5: Parallel per-ticker analysis
    # ========================================
    logger.info(f"Phase 1-5: Running {len(symbols)} ticker analyses (max {max_concurrent} concurrent)")
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def analyze_with_limit(symbol: str) -> tuple[str, SwingTradeState]:
        async with semaphore:
            try:
                result = await run_analysis(
                    symbol=symbol,
                    asof_time=asof_time,
                    trigger_type="SWEEP",
                )
                # Inject topdown into result
                result["topdown"] = topdown
                return symbol, result
            except Exception as e:
                logger.error(f"Analysis failed for {symbol}: {e}")
                return symbol, {"symbol": symbol, "error": str(e)}
    
    tasks = [analyze_with_limit(symbol) for symbol in symbols]
    results_list = await asyncio.gather(*tasks)
    
    results = {symbol: result for symbol, result in results_list}
    
    # ========================================
    # Extract GO signals
    # ========================================
    go_signals = []
    
    for symbol, result in results.items():
        decision = result.get("decision", {})
        veto = result.get("veto", {})
        
        # Include if GO and not vetoed
        if decision.get("decision") == "GO" and not veto.get("veto", False):
            ideas = result.get("ideas", {})
            ideas_list = ideas.get("ideas", [])
            primary_idea = ideas_list[0] if ideas_list else {}
            
            stats = result.get("stats", [])
            first_stats = stats[0] if stats else {}
            
            context = result.get("context", {})
            
            signal = SignalSummary(
                symbol=symbol,
                sector=result.get("sector", "Unknown"),
                sector_etf=result.get("sector_etf", "SPY"),
                direction=primary_idea.get("direction", "LONG"),
                conviction=primary_idea.get("conviction", "LOW"),
                setup_type=primary_idea.get("setup_type", "UNKNOWN"),
                risk_score=decision.get("risk_score", 0.5),
                win_rate=first_stats.get("win_rate"),
                expected_return=first_stats.get("expected_return"),
                entry_level=context.get("current_price"),
                clusters=get_ticker_clusters(symbol),
            )
            go_signals.append(signal)
    
    logger.info(f"Found {len(go_signals)} GO signals (pre-coordination)")
    
    # ========================================
    # Phase 6: Portfolio Coordinator
    # ========================================
    logger.info("Phase 6: Running Portfolio Coordinator")
    
    portfolio = coordinate_signals(go_signals, constraints)
    
    logger.info(
        f"Portfolio: {len(portfolio.approved)} approved, "
        f"{len(portfolio.rejected)} rejected"
    )
    
    return {
        "topdown": topdown,
        "results": results,
        "go_signals": [s.model_dump() for s in go_signals],
        "portfolio": portfolio.model_dump(),
        "summary": {
            "total_symbols": len(symbols),
            "filtered_early": sum(1 for r in results.values() if r.get("filter_reason")),
            "no_ideas": sum(1 for r in results.values() if not r.get("ideas", {}).get("ideas")),
            "go_decisions": len(go_signals),
            "approved_positions": len(portfolio.approved),
            "rejected_positions": len(portfolio.rejected),
        },
    }


async def run_sweep_cli():
    """CLI entry point for sweep."""
    import argparse
    from rich.console import Console
    from rich.table import Table
    
    console = Console()
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    parser = argparse.ArgumentParser(description="Run MEGA50 sweep")
    parser.add_argument("--symbols", help="Comma-separated symbols (default: MEGA50)")
    parser.add_argument("--date", help="Date YYYY-MM-DD (default: now)")
    parser.add_argument("--max-concurrent", type=int, default=10)
    
    args = parser.parse_args()
    
    symbols = args.symbols.split(",") if args.symbols else None
    asof_time = datetime.strptime(args.date, "%Y-%m-%d") if args.date else None
    
    try:
        result = await run_sweep(
            symbols=symbols,
            asof_time=asof_time,
            max_concurrent=args.max_concurrent,
        )
        
        # Display summary
        console.print("\n[bold green]✓ Sweep Complete[/bold green]\n")
        
        summary = result["summary"]
        console.print(f"Total symbols: {summary['total_symbols']}")
        console.print(f"Filtered early: {summary['filtered_early']}")
        console.print(f"No ideas: {summary['no_ideas']}")
        console.print(f"GO decisions: {summary['go_decisions']}")
        console.print(f"[green]Approved positions: {summary['approved_positions']}[/green]")
        console.print(f"[yellow]Rejected positions: {summary['rejected_positions']}[/yellow]")
        
        # Display approved signals
        if result["portfolio"]["approved"]:
            console.print("\n[bold]Approved Signals:[/bold]")
            table = Table()
            table.add_column("Priority")
            table.add_column("Symbol")
            table.add_column("Direction")
            table.add_column("Allocation")
            table.add_column("Notes")
            
            for signal in result["portfolio"]["approved"]:
                table.add_row(
                    str(signal["priority"]),
                    signal["symbol"],
                    signal["direction"],
                    f"{signal['allocation_pct']:.0%}" if signal.get("allocation_pct") else "-",
                    signal.get("notes", ""),
                )
            
            console.print(table)
        
        # Display rejected signals
        if result["portfolio"]["rejected"]:
            console.print("\n[yellow]Rejected Signals:[/yellow]")
            for signal in result["portfolio"]["rejected"]:
                console.print(f"  {signal['symbol']}: {signal['reason']}")
        
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(run_sweep_cli())
