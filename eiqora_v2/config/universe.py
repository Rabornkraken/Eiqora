"""
MEGA50 ticker universe configuration.
50 mega-cap stocks from S&P 500 with sector mappings and correlation clusters.
"""

from typing import Final


# Full list of 50 mega-cap tickers
MEGA50_TICKERS: Final[list[str]] = [
    # Information Technology (14)
    "NVDA", "AAPL", "MSFT", "AVGO", "ORCL", "PLTR", "AMD", "MU", 
    "CSCO", "IBM", "CRM", "APP", "LRCX", "AMAT",
    # Financials (10)
    "BRK.B", "JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "AXP", "C",
    # Health Care (7)
    "LLY", "JNJ", "ABBV", "UNH", "MRK", "TMO", "ABT",
    # Consumer Discretionary (4)
    "AMZN", "TSLA", "HD", "MCD",
    # Communication Services (5)
    "GOOGL", "GOOG", "META", "NFLX", "TMUS",
    # Consumer Staples (5)
    "WMT", "COST", "PG", "KO", "PM",
    # Energy (2)
    "XOM", "CVX",
    # Industrials (3)
    "GE", "CAT", "RTX",
]

# Sector mapping for each ticker
SECTOR_MAPPING: Final[dict[str, str]] = {
    # Information Technology
    "NVDA": "Information Technology", "AAPL": "Information Technology",
    "MSFT": "Information Technology", "AVGO": "Information Technology",
    "ORCL": "Information Technology", "PLTR": "Information Technology",
    "AMD": "Information Technology", "MU": "Information Technology",
    "CSCO": "Information Technology", "IBM": "Information Technology",
    "CRM": "Information Technology", "APP": "Information Technology",
    "LRCX": "Information Technology", "AMAT": "Information Technology",
    # Financials
    "BRK.B": "Financials", "JPM": "Financials", "V": "Financials",
    "MA": "Financials", "BAC": "Financials", "WFC": "Financials",
    "MS": "Financials", "GS": "Financials", "AXP": "Financials", "C": "Financials",
    # Health Care
    "LLY": "Health Care", "JNJ": "Health Care", "ABBV": "Health Care",
    "UNH": "Health Care", "MRK": "Health Care", "TMO": "Health Care", "ABT": "Health Care",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "MCD": "Consumer Discretionary",
    # Communication Services
    "GOOGL": "Communication Services", "GOOG": "Communication Services",
    "META": "Communication Services", "NFLX": "Communication Services",
    "TMUS": "Communication Services",
    # Consumer Staples
    "WMT": "Consumer Staples", "COST": "Consumer Staples",
    "PG": "Consumer Staples", "KO": "Consumer Staples", "PM": "Consumer Staples",
    # Energy
    "XOM": "Energy", "CVX": "Energy",
    # Industrials
    "GE": "Industrials", "CAT": "Industrials", "RTX": "Industrials",
}

# Sector to ETF mapping
SECTOR_ETFS: Final[dict[str, str]] = {
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
}

# Correlation clusters for portfolio constraints
CORRELATION_CLUSTERS: Final[list[dict]] = [
    {
        "cluster_id": "SEMIS",
        "tickers": ["NVDA", "AMD", "MU", "AVGO", "LRCX", "AMAT"],
        "max_simultaneous": 2,
    },
    {
        "cluster_id": "FANMAG",
        "tickers": ["AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "NFLX"],
        "max_simultaneous": 3,
    },
    {
        "cluster_id": "BIG_BANKS",
        "tickers": ["JPM", "BAC", "WFC", "C", "MS", "GS"],
        "max_simultaneous": 2,
    },
    {
        "cluster_id": "CARD_NETWORKS",
        "tickers": ["V", "MA", "AXP"],
        "max_simultaneous": 1,
    },
]


def get_sector(ticker: str) -> str:
    """Get sector for a ticker."""
    return SECTOR_MAPPING.get(ticker, "Unknown")


def get_sector_etf(ticker: str) -> str:
    """Get sector ETF for a ticker."""
    sector = get_sector(ticker)
    return SECTOR_ETFS.get(sector, "SPY")


def get_clusters_for_ticker(ticker: str) -> list[str]:
    """Get correlation cluster IDs that include this ticker."""
    clusters = []
    for cluster in CORRELATION_CLUSTERS:
        if ticker in cluster["tickers"]:
            clusters.append(cluster["cluster_id"])
    return clusters


# Alias for convenience
get_ticker_clusters = get_clusters_for_ticker


def is_mega50_ticker(ticker: str) -> bool:
    """Check if ticker is in MEGA50 universe."""
    return ticker in MEGA50_TICKERS

