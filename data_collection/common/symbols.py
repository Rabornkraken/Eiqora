"""Symbol list helpers shared across pipelines."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from data_collection.common.settings import DEFAULT_CONFIG_PATH


def _normalize_symbol(value: str) -> str | None:
    if not value:
        return None
    symbol = value.strip().upper()
    if not symbol or symbol == "-":
        return None
    return symbol


def _split_symbols(text: str) -> list[str]:
    cleaned = text.replace(",", " ")
    tokens = [token.strip() for token in cleaned.split()]
    return [token for token in tokens if token]


def load_symbols_from_file(path: Path) -> tuple[list[str], bytes]:
    raw_text = path.read_text(encoding="utf-8")
    raw_bytes = raw_text.encode("utf-8")
    symbols: list[str] = []
    seen = set()
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for token in _split_symbols(stripped):
            symbol = _normalize_symbol(token)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
    return symbols, raw_bytes


def get_config_symbols(config: dict) -> tuple[list[str], Path | None, bytes | None]:
    symbols_file = config.get("universe", {}).get("symbols_file")
    if not symbols_file:
        return [], None, None
    path = Path(symbols_file)
    if not path.is_absolute():
        path = DEFAULT_CONFIG_PATH.parent / path
    if not path.exists():
        raise FileNotFoundError(f"Symbol file not found: {path}")
    symbols, raw_bytes = load_symbols_from_file(path)
    return symbols, path, raw_bytes
