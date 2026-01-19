import asyncio
import sys
sys.path.insert(0, '/Users/pan/Documents/Github/Eiqora')

async def check():
    from eiqora_v2.tools.db import get_connection
    async with get_connection() as conn:
        # Check for watchlist tables
        tables = await conn.fetch("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name LIKE '%watch%'
        """)
        print('Watchlist tables:', [t['table_name'] for t in tables])
        
        # Check candidate_selection_log
        candidate_count = await conn.fetchval('SELECT COUNT(*) FROM candidate_selection_log')
        print(f'\ncandidate_selection_log rows: {candidate_count}')
        
        if candidate_count > 0:
            recent = await conn.fetch("""
                SELECT symbol, scan_date, technical_score, profile_score, total_score, is_selected
                FROM candidate_selection_log
                WHERE scan_date = (SELECT MAX(scan_date) FROM candidate_selection_log)
                ORDER BY total_score DESC NULLS LAST
                LIMIT 10
            """)
            print('\nRecent candidates from last scan:')
            for r in recent:
                selected = '✓' if r['is_selected'] else ' '
                print(f'  [{selected}] {r["symbol"]}: score={r["total_score"]:.2f if r["total_score"] else 0}, tech={r["technical_score"]:.2f if r["technical_score"] else 0}, profile={r["profile_score"]:.2f if r["profile_score"] else 0}')

asyncio.run(check())
