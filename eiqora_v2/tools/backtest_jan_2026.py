#!/usr/bin/env python
"""
Backtest Jan 2026 with new 0.60 threshold.
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from eiqora_v2.live.candidate_selector import CandidateSelector

async def backtest_jan_2026():
    """Run candidate selector for January 1-18, 2026."""
    
    et_tz = ZoneInfo("America/New_York")
    
    # Test from Jan 1-18, 2026
    start_date = datetime(2026, 1, 1, tzinfo=et_tz)
    end_date = datetime(2026, 1, 18, tzinfo=et_tz)
    
    print(f"Backtesting from {start_date.date()} to {end_date.date()}\n")
    
    # Test with BOTH thresholds for comparison
    results_new = []
    results_old = []
    
    current_date = start_date
    day_count = 0
    
    while current_date <= end_date:
        # Skip weekends
        if current_date.weekday() >= 5:
            current_date += timedelta(days=1)
            continue
        
        day_count += 1
        print(f"Testing {current_date.strftime('%Y-%m-%d %a')} (Day {day_count})")
        
        try:
            # NEW threshold (0.60)
            selector_new = CandidateSelector(threshold=0.60)
            watchlist_new = await selector_new.build_watchlist(current_date)
            
            # OLD threshold (0.70)
            selector_old = CandidateSelector(threshold=0.70)
            watchlist_old = await selector_old.build_watchlist(current_date)
            
            top_score = watchlist_new[0]['total_score'] if watchlist_new else (
                watchlist_old[0]['total_score'] if watchlist_old else 0.0
            )
            
            results_new.append({
                'date': current_date.date(),
                'count': len(watchlist_new),
                'top_score': top_score
            })
            
            results_old.append({
                'date': current_date.date(),
                'count': len(watchlist_old),
                'top_score': top_score
            })
            
            if len(watchlist_new) != len(watchlist_old):
                diff = len(watchlist_new) - len(watchlist_old)
                print(f"  New: {len(watchlist_new)}, Old: {len(watchlist_old)} ({diff:+d})")
            else:
                print(f"  Both: {len(watchlist_new)}")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
        
        current_date += timedelta(days=1)
    
    # Analysis
    print(f"\n\n{'='*80}")
    print("COMPARISON ANALYSIS")
    print('='*80)
    
    total_days = len(results_new)
    
    # New threshold (0.60)
    days_with_new = sum(1 for r in results_new if r['count'] > 0)
    total_candidates_new = sum(r['count'] for r in results_new)
    avg_new = total_candidates_new / total_days if total_days > 0 else 0
    
    # Old threshold (0.70)  
    days_with_old = sum(1 for r in results_old if r['count'] > 0)
    total_candidates_old = sum(r['count'] for r in results_old)
    avg_old = total_candidates_old / total_days if total_days > 0 else 0
    
    print(f"\n📊 NEW THRESHOLD (0.60)")
    print(f"Days with selections: {days_with_new}/{total_days} ({days_with_new/total_days*100:.1f}%)")
    print(f"Total candidates: {total_candidates_new}")
    print(f"Avg per day: {avg_new:.1f}")
    
    print(f"\n📊 OLD THRESHOLD (0.70)")
    print(f"Days with selections: {days_with_old}/{total_days} ({days_with_old/total_days*100:.1f}%)")
    print(f"Total candidates: {total_candidates_old}")
    print(f"Avg per day: {avg_old:.1f}")
    
    print(f"\n📈 IMPROVEMENT")
    print(f"Additional selection days: +{days_with_new - days_with_old} ({(days_with_new/days_with_old - 1)*100 if days_with_old > 0 else 0:.0f}% increase)")
    print(f"Additional candidates: +{total_candidates_new - total_candidates_old}")
    
    print(f"\n📅 DAY-BY-DAY RESULTS")
    print(f"{'Date':<12} {'New':<6} {'Old':<6} {'Diff':<6} {'Top Score'}")
    print('-'*50)
    for new, old in zip(results_new, results_old):
        diff = new['count'] - old['count']
        diff_str = f"{diff:+d}" if diff != 0 else "="
        print(f"{new['date']!s:<12} {new['count']:<6} {old['count']:<6} {diff_str:<6} {new['top_score']:.3f}")
    
    return results_new, results_old

if __name__ == '__main__':
    import logging
    logging.basicConfig(
        level=logging.WARNING,  # Reduce noise
        format="%(message)s"
    )
    
    asyncio.run(backtest_jan_2026())
