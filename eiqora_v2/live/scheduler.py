"""
Live trading scheduler.

Runs pre-market scanner at scheduled times using APScheduler.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from eiqora_v2.live.scanner import LiveScanner
from eiqora_v2.live.signals import SignalManager

_logger = logging.getLogger(__name__)


class LiveScheduler:
    """Scheduler for live trading operations."""
    
    def __init__(
        self,
        db_url: str | None = None,
        symbols_file: str = "data_collection/config/symbols.txt",
        timezone: str = "America/New_York",
    ):
        self.db_url = db_url or self._get_db_url()
        self.symbols_file = symbols_file
        self.timezone_str = timezone
        
        self.scanner = LiveScanner(symbols_file=symbols_file, db_url=self.db_url)
        self.signal_manager = SignalManager()  # Uses get_connection() from tools.db
        
        self.scheduler = AsyncIOScheduler(timezone=timezone)
    
    def _get_db_url(self) -> str:
        return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    
    async def run_hourly_scan(self):
        """Hourly scanner job (runs 10 AM - 3 PM ET)."""
        try:
            _logger.info("🔍 Starting hourly scan...")
            
            # Generate signals
            signals = await self.scanner.generate_trade_signals()
            
            # Store to database
            if signals:
                signal_ids = self.signal_manager.store_signals(signals)
                _logger.info(f"Stored {len(signal_ids)} signals to database")
            
            # Send notifications
            self.signal_manager.send_notifications(signals)
            
            _logger.info("✅ Hourly scan complete")
            
        except Exception as e:
            _logger.error(f"Hourly scan failed: {e}", exc_info=True)
    
    async def run_position_monitor(self):
        """Position monitor job (runs at 4:30 PM ET after market close)."""
        try:
            _logger.info("📊 Running position monitor...")
            
            # TODO: Implement position monitoring
            # - Check database for open positions
            # - Run PositionMonitorAgent on each
            # - Apply TIGHTEN/EXIT recommendations
            
            _logger.info("✅ Position monitor complete")
            
        except Exception as e:
            _logger.error(f"Position monitor failed: {e}", exc_info=True)
    
    def schedule_jobs(self):
        """Schedule all live trading jobs."""
        
        # Hourly scans at :30 past each hour (10:30 AM - 3:30 PM ET, Monday-Friday)
        # Scanning at :30 allows full hourly bars to form
        for hour in [10, 11, 12, 13, 14, 15]:
            self.scheduler.add_job(
                self.run_hourly_scan,
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=hour,
                    minute=30,  # Changed from 0 to 30
                    timezone=self.timezone_str,
                ),
                id=f"hourly_scan_{hour}",
                name=f"Hourly Scan {hour}:30",
                replace_existing=True,
            )
        _logger.info("Scheduled: Hourly Scanner (10:30 AM - 3:30 PM ET, Mon-Fri)")
        
        # Position monitor at 4:30 PM ET (Monday-Friday)
        self.scheduler.add_job(
            self.run_position_monitor,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=16,
                minute=30,
                timezone=self.timezone_str,
            ),
            id="position_monitor",
            name="Position Monitor",
            replace_existing=True,
        )
        _logger.info("Scheduled: Position Monitor (4:30 PM ET, Mon-Fri)")
    
    def start(self):
        """Start the scheduler."""
        _logger.info("Starting live trading scheduler...")
        self.schedule_jobs()
        self.scheduler.start()
        _logger.info("✅ Scheduler started")
    
    def run_now(self, job_name: str = "hourly_scan"):
        """Run a job immediately (for testing)."""
        if job_name == "hourly_scan":
            asyncio.run(self.run_hourly_scan())
        elif job_name == "position_monitor":
            asyncio.run(self.run_position_monitor())
        else:
            _logger.error(f"Unknown job: {job_name}")
    
    def stop(self):
        """Stop the scheduler."""
        _logger.info("Stopping scheduler...")
        self.scheduler.shutdown()


async def main():
    """Main entry point for running scheduler."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    scheduler = LiveScheduler()
    
    # For testing, run immediately
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        _logger.info("Running hourly scan NOW...")
        await scheduler.run_hourly_scan()
    else:
        # Start scheduled jobs
        scheduler.start()
        
        try:
            # Keep running
            while True:
                await asyncio.sleep(60)
        except (KeyboardInterrupt, SystemExit):
            scheduler.stop()


if __name__ == "__main__":
    asyncio.run(main())
