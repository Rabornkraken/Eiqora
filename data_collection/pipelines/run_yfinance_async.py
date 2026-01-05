"""
Async wrapper to run YFinance news collection with CDP browser
"""
import asyncio
import sys
sys.path.insert(0, '/Users/pan/Documents/Github/Eiqora/data_collection/pipelines')

from yfinance_news import collect_news, score_news, run

# Run async functions
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='Max symbols to process')
    parser.add_argument('--mode', choices=['collect', 'score', 'both'], default='both')
    args = parser.parse_args()
    
    if args.mode == 'collect':
        asyncio.run(collect_news(limit_symbols=args.limit))
    elif args.mode == 'score':
        score_news(limit=args.limit)
    else:
        asyncio.run(run(limit_symbols=args.limit))
