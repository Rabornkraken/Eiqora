"""
Backtest Data Collector.

Integrates with data collection pipelines to gather point-in-time data
for backtesting without data leakage.
"""

import logging
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class BacktestDataCollector:
    """
    Collects historical data for backtesting using existing pipelines.
    
    Ensures point-in-time data availability without data leakage.
    All data queries should use timestamps <= the backtest date.
    """
    
    def __init__(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        project_root: str | None = None,
    ):
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.project_root = project_root or Path(__file__).parent.parent.parent
    
    def collect_all(self):
        """Run all data collection pipelines for the backtest period."""
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        
        logger.info("="*60)
        logger.info("BACKTEST DATA COLLECTION")
        logger.info("="*60)
        logger.info(f"Tickers: {self.tickers}")
        logger.info(f"Period: {self.start_date} to {self.end_date}")
        logger.info("")
        
        self.collect_daily_bars()
        self.collect_hourly_bars()
        self.collect_vix()
        self.collect_earnings()
        self.collect_economic_calendar()
        self.collect_sec_filings()
        self.collect_news()
        self.collect_corporate_actions()
        
        logger.info("")
        logger.info("="*60)
        logger.info("DATA COLLECTION COMPLETE")
        logger.info("="*60)
    
    def _run_pipeline(self, name: str, cmd: list[str], timeout: int = 300):
        """Run a pipeline as subprocess."""
        logger.info(f"  Running {name}...")
        
        full_env = os.environ.copy()
        full_env['PYTHONPATH'] = str(self.project_root)
        
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.project_root),
                env=full_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            
            if result.returncode == 0:
                logger.info(f"    ✅ {name} completed")
            else:
                # Show more error detail
                err = result.stderr[:300] if result.stderr else result.stdout[:300]
                logger.warning(f"    ⚠️ {name} failed: {err}")
                
        except subprocess.TimeoutExpired:
            logger.warning(f"    ⚠️ {name} timed out after {timeout}s")
        except Exception as e:
            logger.warning(f"    ⚠️ {name} error: {e}")
    
    def collect_daily_bars(self):
        """Collect daily OHLCV bars from yfinance."""
        logger.info("📊 Collecting daily OHLCV bars (yfinance)...")

        # yf_daily expects --symbols as comma-separated string
        symbols_str = ",".join(self.tickers)

        self._run_pipeline(
            "yf_daily",
            [
                sys.executable, "-m", "data_collection.pipelines.yf_daily",
                "backfill",
                "--start", self.start_date.isoformat(),
                "--end", self.end_date.isoformat(),
                "--symbols", symbols_str,
            ]
        )
    
    def collect_hourly_bars(self):
        """Collect hourly bars from yfinance."""
        logger.info("📊 Collecting hourly OHLCV bars (yfinance)...")
        
        self._run_pipeline(
            "hourly_bars",
            [
                sys.executable, "-m", "data_collection.pipelines.hourly_bars",
                "--tickers", *self.tickers,
                "--start", self.start_date.isoformat(),
                "--end", self.end_date.isoformat(),
            ]
        )
    
    def collect_vix(self):
        """Collect VIX data."""
        logger.info("📈 Collecting VIX data...")
        
        # vix.py has no args, just runs asyncio
        self._run_pipeline(
            "vix",
            [
                sys.executable, "-m", "data_collection.pipelines.vix",
            ]
        )
    
    def collect_earnings(self):
        """Collect earnings calendar."""
        logger.info("💰 Collecting earnings calendar...")
        
        # Calculate window based on dates
        days_past = max(1, (date.today() - self.start_date).days)
        days_future = max(0, (self.end_date - date.today()).days)
        
        # earnings.py uses --past-days and --future-days
        self._run_pipeline(
            "earnings",
            [
                sys.executable, "-m", "data_collection.pipelines.earnings",
                "run",
                "--past-days", str(days_past),
                "--future-days", str(days_future),
            ]
        )
    
    def collect_economic_calendar(self):
        """Collect economic calendar (FOMC, CPI, NFP, etc.)."""
        logger.info("🏛️ Collecting economic calendar...")
        
        # economic_calendar.py might need 'run' subcommand
        self._run_pipeline(
            "economic_calendar",
            [
                sys.executable, "-m", "data_collection.pipelines.economic_calendar",
            ]
        )
    
    def collect_sec_filings(self):
        """Collect SEC filings (8-K, etc.)."""
        logger.info("📄 Collecting SEC filings...")
        
        self._run_pipeline(
            "sec_rss",
            [
                sys.executable, "-m", "data_collection.pipelines.sec_rss",
                "run",
            ]
        )
    
    def collect_news(self):
        """Collect news articles."""
        logger.info("📰 Collecting news articles...")
        
        self._run_pipeline(
            "yfinance_news",
            [
                sys.executable, "-m", "data_collection.pipelines.yfinance_news",
                "run",
            ]
        )
    
    def collect_corporate_actions(self):
        """Collect corporate actions (dividends, splits)."""
        logger.info("🏢 Collecting corporate actions...")
        
        # Longer timeout for Playwright crawling
        self._run_pipeline(
            "corporate_actions",
            [
                sys.executable, "-m", "data_collection.pipelines.corporate_actions_crawler",
                "run",
            ],
            timeout=600,  # 10 minutes for slow Playwright
        )


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Backtest Data Collector")
    parser.add_argument("--tickers", nargs="+", default=["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN"])
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    collector = BacktestDataCollector(
        tickers=args.tickers,
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
    )
    
    collector.collect_all()


if __name__ == "__main__":
    main()
