"""
CLI for testing eiqora_v2 swing trade analysis.
"""

import argparse
import asyncio
import json
import logging
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from eiqora_v2.graph.swing_trade import run_analysis
from eiqora_v2.config.universe import MEGA50_TICKERS, is_mega50_ticker
from eiqora_v2.tools.db import close_pool

console = Console()


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


async def run_command(symbol: str, date_str: str | None, verbose: bool):
    """Run analysis for a single symbol."""
    setup_logging(verbose)
    
    # Parse date
    if date_str:
        asof_time = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        asof_time = datetime.utcnow()
    
    # Validate symbol
    if not is_mega50_ticker(symbol):
        console.print(f"[yellow]Warning: {symbol} is not in MEGA50 universe[/yellow]")
    
    console.print(f"\n[bold blue]Running analysis for {symbol}[/bold blue]")
    console.print(f"As of: {asof_time}")
    console.print("-" * 50)
    
    try:
        # Run analysis
        result = await run_analysis(
            symbol=symbol,
            asof_time=asof_time,
            trigger_type="CHART_SETUP",
        )
        
        # Display results
        console.print("\n[bold green]✓ Analysis Complete[/bold green]\n")
        
        # Triage
        if result.get("triage"):
            console.print(Panel(
                Syntax(json.dumps(result["triage"], indent=2, default=str), "json"),
                title="[blue]Event Triage[/blue]",
            ))
        
        # Context
        if result.get("context"):
            console.print(Panel(
                Syntax(json.dumps(result["context"], indent=2, default=str), "json"),
                title="[blue]Context[/blue]",
            ))
        
        # Chart
        if result.get("chart"):
            console.print(Panel(
                Syntax(json.dumps(result["chart"], indent=2, default=str), "json"),
                title="[blue]Chart[/blue]",
            ))
        
        # Filter result
        if result.get("filter_reason"):
            console.print(f"\n[yellow]Filtered: {result['filter_reason']}[/yellow]")
        else:
            # Show idea generation results
            if result.get("ideas"):
                console.print(Panel(
                    Syntax(json.dumps(result["ideas"], indent=2, default=str), "json"),
                    title="[magenta]Ideas[/magenta]",
                ))
            
            # Show decision
            if result.get("decision"):
                decision = result["decision"]
                decision_color = "green" if decision.get("decision") == "GO" else "red"
                console.print(Panel(
                    Syntax(json.dumps(decision, indent=2, default=str), "json"),
                    title=f"[{decision_color}]Decision: {decision.get('decision')}[/{decision_color}]",
                ))
            
            # Show veto
            if result.get("veto"):
                veto = result["veto"]
                veto_color = "red" if veto.get("veto") else "green"
                console.print(Panel(
                    Syntax(json.dumps(veto, indent=2, default=str), "json"),
                    title=f"[{veto_color}]Veto: {veto.get('veto')}[/{veto_color}]",
                ))
        
        # Errors
        if result.get("errors"):
            console.print("\n[red]Errors:[/red]")
            for error in result["errors"]:
                console.print(f"  - {error}")
        
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise
    finally:
        await close_pool()


async def list_tickers_command():
    """List all MEGA50 tickers."""
    console.print("\n[bold]MEGA50 Universe[/bold]\n")
    for i, ticker in enumerate(MEGA50_TICKERS, 1):
        console.print(f"  {i:2}. {ticker}")
    console.print(f"\nTotal: {len(MEGA50_TICKERS)} tickers")


def main():
    parser = argparse.ArgumentParser(
        description="Eiqora V2 Swing Trade Analysis CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run analysis for a symbol")
    run_parser.add_argument("--symbol", "-s", required=True, help="Stock symbol")
    run_parser.add_argument("--date", "-d", help="Date (YYYY-MM-DD)")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    
    # List command
    subparsers.add_parser("list", help="List MEGA50 tickers")
    
    args = parser.parse_args()
    
    if args.command == "run":
        asyncio.run(run_command(args.symbol, args.date, args.verbose))
    elif args.command == "list":
        asyncio.run(list_tickers_command())


if __name__ == "__main__":
    main()
