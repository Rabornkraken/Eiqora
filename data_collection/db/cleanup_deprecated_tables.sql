-- Database Cleanup Script
-- WARNING: Review carefully before running!
-- This script removes deprecated tables not used by agents

-- ============================================================================
-- SOCIAL MEDIA TABLES (NOT USED BY ANY AGENT)
-- ============================================================================

-- Xueqiu (Chinese social media)
DROP TABLE IF EXISTS xueqiu_post CASCADE;
DROP TABLE IF EXISTS xueqiu_comment CASCADE;
DROP TABLE IF EXISTS xueqiu_user CASCADE;

-- Reddit
DROP TABLE IF EXISTS reddit_post CASCADE;
DROP TABLE IF EXISTS reddit_comment CASCADE;
DROP TABLE IF EXISTS reddit_subreddit CASCADE;

-- YouTube
DROP TABLE IF EXISTS youtube_video CASCADE;
DROP TABLE IF EXISTS youtube_comment CASCADE;
DROP TABLE IF EXISTS youtube_channel CASCADE;

-- Douyin/TikTok
DROP TABLE IF EXISTS douyin_creator CASCADE;
DROP TABLE IF EXISTS douyin_video CASCADE;
DROP TABLE IF EXISTS douyin_comment CASCADE;

-- ============================================================================
-- DUPLICATE/REDUNDANT TABLES
-- ============================================================================

-- Option 1: If using `signal` as primary table
-- DROP TABLE IF EXISTS trade_signal CASCADE;

-- Option 2: If using `trade_signal` as primary
-- Migrate data first:
-- INSERT INTO trade_signal SELECT * FROM signal WHERE ...;
-- DROP TABLE signal CASCADE;

-- ============================================================================
-- UNUSED INFRASTRUCTURE
-- ============================================================================

-- Old analog event system (if not using stats service)
-- DROP TABLE IF EXISTS analog_event CASCADE;

-- ============================================================================
-- DATA CLEANUP (Optional - Removes old data)
-- ============================================================================

-- Clean old profiles (older than 30 days)
DELETE FROM ticker_profile 
WHERE last_updated < NOW() - INTERVAL '30 days';

-- Clean old signals (older than 90 days, keep history)
-- DELETE FROM signal 
-- WHERE created_at < NOW() - INTERVAL '90 days';

-- ============================================================================
-- VERIFY REMAINING TABLES
-- ============================================================================

-- Check what's left
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Expected core tables after cleanup:
-- ✅ market_bar_daily
-- ✅ market_bar_hourly
-- ✅ earnings_event
-- ✅ document
-- ✅ sec_filing
-- ✅ security
-- ✅ issuer
-- ✅ watchlist
-- ✅ ticker_profile
-- ✅ signal
-- ✅ economic_indicator
-- ✅ insider_transaction
-- ✅ corporate_action
-- ✅ sec_13f_holding
