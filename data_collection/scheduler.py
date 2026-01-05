"""Data collection scheduler using APScheduler."""

import logging
import os
import subprocess
import sys
from datetime import datetime

# YFinance news uses CDP browser for article fetching
# No environment variables needed (configured in yfinance_news.py)

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('scheduler')


def run_pipeline(pipeline_module: str) -> None:
    """Run a pipeline as a subprocess with real-time output."""
    logger.info(f"Starting pipeline: {pipeline_module}")
    start = datetime.now()
    
    try:
        # Stream output in real-time instead of capturing
        result = subprocess.run(
            [sys.executable, '-m', pipeline_module],
            timeout=3600,  # 1 hour timeout
        )
        
        duration = (datetime.now() - start).total_seconds()
        
        if result.returncode == 0:
            logger.info(f"✅ {pipeline_module} completed in {duration:.1f}s")
        else:
            logger.error(f"❌ {pipeline_module} failed (exit {result.returncode})")
                
    except subprocess.TimeoutExpired:
        logger.error(f"⏰ {pipeline_module} timed out after 1 hour")
    except Exception as e:
        logger.error(f"💥 {pipeline_module} exception: {e}")


def job_listener(event):
    """Log job execution events."""
    if event.exception:
        logger.error(f"Job {event.job_id} failed: {event.exception}")


def create_scheduler() -> BlockingScheduler:
    """Create and configure the scheduler with all jobs."""
    scheduler = BlockingScheduler(timezone='America/New_York')
    scheduler.add_listener(job_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)
    
    # ═══════════════════════════════════════════════════════════════════
    # HIGH PRIORITY - Core data pipelines
    # ═══════════════════════════════════════════════════════════════════
    
    # SEC RSS - Check for new filings every 15 minutes (market hours)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.sec_rss'],
        id='sec_rss',
        minute='*/15',
        hour='6-20',  # 6 AM to 8 PM ET
    )
    
    # Earnings Calendar - Daily at 6 AM ET
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.earnings'],
        id='earnings',
        hour=6,
        minute=0,
    )
    
    # Daily OHLCV Bars - After market close at 6 PM ET
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.stooq_daily'],
        id='stooq_daily',
        hour=18,
        minute=30,
    )
    
    # ═══════════════════════════════════════════════════════════════════
    # MEDIUM PRIORITY - News and sentiment
    # ═══════════════════════════════════════════════════════════════════
    
    # YFinance News - Every 4 hours
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.yfinance_news'],
        id='yfinance_news',
        hour='*/4',
        minute=15,
    )
    
    # ═══════════════════════════════════════════════════════════════════
    # LOW PRIORITY - Weekly/infrequent jobs
    # ═══════════════════════════════════════════════════════════════════
    
    # VIX / Volatility Indices - Daily at 6:15 PM ET (after market close)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.vix'],
        id='vix',
        hour=18,
        minute=15,
    )
    
    # Economic Calendar - Daily at 7 AM ET
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.economic_calendar'],
        id='economic_calendar',
        hour=7,
        minute=0,
    )
    
    # XBRL Revenue Extraction - Weekly on Sunday at 2 AM
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.xbrl_revenue'],
        id='xbrl_revenue',
        day_of_week='sun',
        hour=2,
        minute=0,
    )
    
    # SEC EDGAR Full Crawl - Weekly on Saturday at 3 AM
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.sec_edgar.pipeline'],
        id='sec_edgar',
        day_of_week='sat',
        hour=3,
        minute=0,
    )
    
    # Corporate Actions - Weekly on Sunday at 4 AM
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.corporate_actions_crawler'],
        id='corporate_actions',
        day_of_week='sun',
        hour=4,
        minute=0,
    )
    
    # ═══════════════════════════════════════════════════════════════════
    # INTRADAY DATA - Critical for live trading
    # ═══════════════════════════════════════════════════════════════════
    
    # Hourly OHLCV Bars - Every hour during market hours (CRITICAL)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.hourly_bars_auto'],
        id='hourly_bars',
        minute=5,  # Run at :05 past the hour
        hour='10-16',  # 10 AM - 4 PM ET (after first hour settles)
        day_of_week='mon-fri',
    )
    
    # ═══════════════════════════════════════════════════════════════════
    # UNIVERSE & REFERENCE DATA
    # ═══════════════════════════════════════════════════════════════════
    
    # Universe Member Refresh - Weekly on Monday at 1 AM
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.universe'],
        id='universe_refresh',
        day_of_week='mon',
        hour=1,
        minute=0,
    )
    
    # SEC Ticker Map - Monthly on 1st at midnight
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.sec_ticker_map'],
        id='sec_ticker_map',
        day=1,
        hour=0,
        minute=0,
    )
    
    return scheduler


def run_startup_pipelines():
    """Run critical pipelines once at startup to ensure database is updated."""
    import concurrent.futures
    
    logger.info("=" * 60)
    logger.info("Running startup pipelines to update database...")
    logger.info("=" * 60)
    
    # Critical pipelines to run at startup (module, command, env_vars)
    startup_pipelines = [
        ('data_collection.pipelines.universe', 'run', {}),
        ('data_collection.pipelines.vix', 'run', {}),
        ('data_collection.pipelines.earnings', 'run', {}),
        ('data_collection.pipelines.sec_rss', 'run', {}),
        ('data_collection.pipelines.hourly_bars_auto', None, {}),
    ]
    
    # Run sequential pipelines first
    for pipeline, command, env_vars in startup_pipelines:
        cmd_str = f"{pipeline} {command}" if command else pipeline
        logger.info(f"Starting pipeline: {cmd_str}")
        start = datetime.now()
        try:
            cmd = [sys.executable, '-m', pipeline]
            if command:
                cmd.append(command)
            
            env = os.environ.copy()
            env.update(env_vars)
            result = subprocess.run(cmd, timeout=1800, env=env)
            duration = (datetime.now() - start).total_seconds()
            if result.returncode == 0:
                logger.info(f"✅ {pipeline} completed in {duration:.1f}s")
            else:
                logger.error(f"❌ {pipeline} failed (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            logger.error(f"⏰ {pipeline} timed out")
        except Exception as e:
            logger.error(f"Startup pipeline {pipeline} failed: {e}")
    
    # Run YFinance news collection for all watchlist symbols
    logger.info("Starting YFinance news collection...")
    yfinance_start = datetime.now()
    
    result = subprocess.run(
        [sys.executable, '-m', 'data_collection.pipelines.yfinance_news'],
        timeout=3600,  # 1 hour timeout for full collection
    )
    
    yfinance_duration = (datetime.now() - yfinance_start).total_seconds()
    if result.returncode == 0:
        logger.info(f"✅ YFinance news completed in {yfinance_duration:.1f}s")
    else:
        logger.error(f"❌ YFinance news failed in {yfinance_duration:.1f}s")
    
    logger.info("=" * 60)
    logger.info("Startup pipelines completed!")
    logger.info("=" * 60)


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Starting Eiqora Data Collection Scheduler")
    logger.info("=" * 60)
    
    # Run startup pipelines first
    run_startup = os.environ.get("SCHEDULER_RUN_STARTUP", "1") == "1"
    if run_startup:
        run_startup_pipelines()
    
    scheduler = create_scheduler()
    
    # Print scheduled jobs
    logger.info("Scheduled jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.id}: {job.trigger}")
    
    logger.info("-" * 60)
    logger.info("Scheduler running. Press Ctrl+C to stop.")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == '__main__':
    main()
