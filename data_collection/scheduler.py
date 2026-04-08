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
log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'data_collector.log')

# Create handlers
file_handler = logging.FileHandler(log_file)
console_handler = logging.StreamHandler()

# Create formatter
formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger('scheduler')


def run_pipeline(pipeline_module: str, *args) -> None:
    """Run a pipeline as a subprocess with real-time output."""
    logger.info(f"Starting pipeline: {pipeline_module} {' '.join(args)}")
    start = datetime.now()
    
    try:
        # Stream output in real-time instead of capturing
        cmd = [sys.executable, '-m', pipeline_module]
        if args:
            cmd.extend(args)
        result = subprocess.run(
            cmd,
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
    
    # Watchlist Builder (daily at 8 AM after data is fresh)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.build_watchlist'],
        id='build_watchlist',
        hour='8',
        minute='0',
    )

    # SEC RSS - Check for new filings every 15 minutes (market hours)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.sec_rss', 'run'],
        id='sec_rss',
        minute='*/15',
        hour='6-20',  # 6 AM to 8 PM ET
    )
    
    # Earnings Calendar - Evening collection (after market close for next-day trading)
    # Primary: Catches after-hours earnings releases immediately
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.earnings'],
        id='earnings_evening',
        hour=16,  # 4 PM ET (after market close)
        minute=30,
    )
    
    # Earnings Calendar - Morning backup (catches any missed/late releases)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.earnings'],
        id='earnings_morning',
        hour=6,
        minute=0,
    )

    # Analyst Ratings - Daily at 5 AM ET
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.analyst_ratings'],
        id='analyst_ratings',
        hour=5,
        minute=0,
    )
    
    # Daily OHLCV Bars - After market close at 6 PM ET.
    # Switched from Stooq to yfinance after Stooq started blocking
    # unauthenticated requests in early 2026.
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.yf_daily', 'run'],
        id='yf_daily',
        hour=18,
        minute=30,
    )

    # Daily Technical Indicators - After daily bars at 7 PM ET
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.daily_technical_indicators'],
        id='daily_technical_indicators',
        hour=19,
        minute=0,
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
        hour='*',
        minute=20,
    )
    
    # Options Data - Daily after market close
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.options_summary'],
        id='options_summary',
        hour=16,  # 4 PM ET, after market close
        minute=45,
    )
    
    # Money Flow Indicators - Daily after market close (after options)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.money_flow_indicators'],
        id='money_flow_indicators',
        hour=17,  # 5 PM ET, after options data
        minute=0,
    )
    
    # Hourly Technical Indicators - Every hour during market hours
    # Runs at :15 (10 minutes after hourly bars collected at :05)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.hourly_indicators'],
        id='hourly_indicators',
        day_of_week='mon-fri',
        hour='10-16',  # Market hours (10 AM - 4 PM ET)
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

    # SEC FTD ETL - Monthly on the 2nd at 5:30 AM
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.sec_ftd.etl'],
        id='sec_ftd_etl',
        day=2,
        hour=5,
        minute=30,
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

    # Stock Correlations - Weekly on Sunday at 5 AM
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.stock_correlations'],
        id='stock_correlations',
        day_of_week='sun',
        hour=5,
        minute=0,
    )

    # Ticker Profile Refresh — moved to live scheduler (has eiqora_v2 deps)

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

    # 1-Minute OHLCV Bars - Every minute during market hours
    # Uses yf.download() batch mode (~2-5s for all symbols)
    scheduler.add_job(
        run_pipeline,
        'cron',
        args=['data_collection.pipelines.minute_bars_auto'],
        id='minute_bars',
        minute='*/1',
        hour='9-16',
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
        ('data_collection.pipelines.yf_daily', 'run', {}),  # Update market_bar_daily (yfinance, replaced Stooq)
        ('data_collection.pipelines.vix', 'run', {}),
        ('data_collection.pipelines.earnings', 'run', {}),
        ('data_collection.pipelines.analyst_ratings', 'run', {}),
        ('data_collection.pipelines.options_summary', None, {}),  # Populate options data for profiles
        ('data_collection.pipelines.sec_rss', 'run', {}),
        ('data_collection.pipelines.hourly_bars_auto', None, {}),
        ('data_collection.pipelines.minute_bars_auto', None, {}),
        # Seed reference data
        ('data_collection.pipelines.seed_stock_relationships', None, {}),
        ('data_collection.pipelines.seed_influential_figures', None, {}),
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
