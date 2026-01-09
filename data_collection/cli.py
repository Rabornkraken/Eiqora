"""CLI entrypoint for running data collection pipelines."""

from __future__ import annotations

import argparse
import importlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable


PIPELINES: dict[str, str] = {
    "alpaca_ohlcv": "data_collection.pipelines.alpaca_ohlcv.pipeline",
    "sec_edgar": "data_collection.pipelines.sec_edgar.pipeline",
    "gdelt": "data_collection.pipelines.gdelt.pipeline",
    "yfinance_news": "data_collection.pipelines.yfinance_news",
    "fred_macro": "data_collection.pipelines.fred_macro.pipeline",
    "usaspending": "data_collection.pipelines.usaspending.pipeline",
    "sec_ftd": "data_collection.pipelines.sec_ftd.pipeline",
    "sec_ftd_etl": "data_collection.pipelines.sec_ftd.etl",
}


def _load_runner(module_path: str) -> Callable[[], None]:
    module = importlib.import_module(module_path)
    runner = getattr(module, "run", None)
    if not callable(runner):
        raise RuntimeError(f"Pipeline module missing run(): {module_path}")
    return runner


def run_selected(selected: list[str], parallel: bool, max_workers: int) -> None:
    runners = {name: _load_runner(PIPELINES[name]) for name in selected}

    if parallel and len(runners) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(runner): name for name, runner in runners.items()}
            for future in as_completed(futures):
                name = futures[future]
                future.result()
                print(f"Completed: {name}")
    else:
        for name, runner in runners.items():
            runner()
            print(f"Completed: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run data collection pipelines.")
    parser.add_argument(
        "--pipeline",
        action="append",
        choices=sorted(PIPELINES.keys()),
        help="Pipeline(s) to run. Repeat for multiple.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all pipelines.",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run selected pipelines in parallel.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Max worker threads when --parallel is set.",
    )
    args = parser.parse_args()

    if not args.all and not args.pipeline:
        parser.error("Specify --pipeline or --all")

    selected = sorted(PIPELINES.keys()) if args.all else list(dict.fromkeys(args.pipeline))
    run_selected(selected, parallel=args.parallel, max_workers=args.max_workers)


if __name__ == "__main__":
    main()
