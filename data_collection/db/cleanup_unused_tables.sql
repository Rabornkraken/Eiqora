-- Actual Database Cleanup Script (Based on Live Database Analysis)
-- Database: eiqora-postgres (27 tables, 41 MB)
-- Date: 2026-01-04

-- ============================================================================
-- VERIFIED UNUSED TABLES (SAFE TO DROP)
-- ============================================================================

-- These tables exist but have 0 rows AND no code references:

-- 1. document_fts (Full-text search index)
--    - 0 rows
--    - Only written by index_docs.py pipeline (not currently running)
--    - Never queried by agents
DROP TABLE IF EXISTS document_fts CASCADE;

-- 2. ir_feed_registry (Unknown IR feed tracking)
--    - 0 rows
--    - Only created in migrations, never used
DROP TABLE IF EXISTS ir_feed_registry CASCADE;

-- 3. market_bar_intraday (Duplicate of market_bar_hourly?)
--    - 0 rows
--    - System uses market_bar_hourly instead
DROP TABLE IF EXISTS market_bar_intraday CASCADE;

-- ============================================================================
-- KEEP THESE (IN USE)
-- ============================================================================

-- news_relevance - 131 rows
-- ✅ KEEP: Used by GDELT pipeline for relevance scoring
-- ✅ Written to by gdelt.py (lines 427, 502)

-- ============================================================================
-- VERIFY CLEANUP
-- ============================================================================

-- Should have 24 tables after cleanup (down from 27)
SELECT COUNT(*) as table_count FROM pg_tables WHERE schemaname = 'public';

-- Check remaining size
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 10;
