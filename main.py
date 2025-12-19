import argparse
import asyncio
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from eiqora.graph.workflow import create_graph

load_dotenv()
console = Console()

def run_analysis(ticker: str):
    console.print(f"[bold blue]Starting Eiqora Analysis for {ticker}...[/bold blue]")
    
    app = create_graph()
    
    initial_state = {
        "ticker": ticker,
        "debate_history": [],
        "iteration_count": 0
    }
    
    # Run the graph
    try:
        final_state = app.invoke(initial_state)
        
        # Display Results
        console.print(Panel(Markdown(f"# Final Decision: {ticker}"), title="Eiqora Verdict", style="bold green"))
        
        if final_state.get("final_decision"):
            console.print(f"[bold]Decision:[/bold] {final_state['final_decision']}")
        
        console.print("\n[bold]Trading Plan:[/bold]")
        console.print(final_state.get("investment_plan", "No plan generated."))
        
        console.print("\n[bold]Risk Assessment:[/bold]")
        console.print(final_state.get("risk_assessment", "No assessment generated."))
        
        console.print("\n[bold]Deep Research Summary:[/bold]")
        console.print(f"Market Data: {str(final_state.get('market_data', {}))[:200]}...")
        console.print(f"News Summary: {str(final_state.get('news_summary', ''))[:200]}...")
        
    except Exception as e:
        console.print(f"[bold red]Analysis Failed:[/bold red] {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eiqora Financial Analysis CLI")
    parser.add_argument("ticker", help="Stock ticker symbol (e.g. AAPL)")
    args = parser.parse_args()
    
    run_analysis(args.ticker)
