"""
Eiqora ticker universe configuration.
~83 large/mid-cap stocks with sector mappings and correlation clusters.
"""

import warnings
from typing import Final


# Full universe of tickers (~83)
UNIVERSE_TICKERS: Final[list[str]] = [
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
    # ── Expansion (2026-02-11) ──────────────────────────────────────────
    # The original MEGA50 (50 mega-cap stocks) was too narrow for a
    # trigger-based momentum/swing system.  ~25% of the list (defensive
    # staples, low-beta healthcare) rarely produced actionable triggers.
    # Adding ~33 higher-beta mid-to-large caps gives the candidate
    # selector a richer pool; the new ATR% >= 1.5 filter prevents
    # low-volatility names from wasting scoring cycles.
    #
    # AI/Cloud/Cyber (6) — secular AI/cloud spend; high ATR%, frequent
    #   earnings-driven gaps, strong momentum persistence.
    "CRWD",   # CrowdStrike — endpoint security leader, high-beta cyber
    "PANW",   # Palo Alto Networks — platform consolidation play
    "SNOW",   # Snowflake — cloud data platform, high vol around earnings
    "NET",    # Cloudflare — edge/zero-trust, retail-favorite momentum name
    "DDOG",   # Datadog — observability, strong rev growth
    "ZS",     # Zscaler — zero-trust cloud security
    #
    # Semis (4) — broadening semi exposure beyond mega-caps; these are
    #   higher-beta plays on AI capex, EV, and industrial automation.
    "MRVL",   # Marvell — custom silicon / data-center networking
    "ON",     # ON Semi — EV & industrial power semis, cyclical beta
    "KLAC",   # KLA Corp — semi cap equipment, EDA-adjacent
    "ARM",    # Arm Holdings — IP licensing, AI-edge catalyst
    #
    # Consumer/Internet (6) — platform/marketplace names with strong
    #   retail flow, high ATR%, and catalyst-rich earnings calendars.
    "COIN",   # Coinbase — crypto proxy, extreme vol around BTC moves
    "SHOP",   # Shopify — e-commerce platform, high-beta growth
    "UBER",   # Uber — gig/mobility, improving profitability catalyst
    "ABNB",   # Airbnb — travel/experience economy
    "DASH",   # DoorDash — last-mile delivery, margin expansion story
    #
    # Biotech/Med-tech (4) — replacing low-beta pharma exposure with
    #   higher-growth, higher-vol biotech and med-device names.
    "ISRG",   # Intuitive Surgical — robotic surgery leader, steady gaps
    "VRTX",   # Vertex Pharma — CF franchise + pain pipeline catalyst
    "REGN",   # Regeneron — biologics pipeline, earnings vol
    "DXCM",   # DexCom — CGM leader, high-beta med-tech
    #
    # Energy/Utilities (3) — secular clean-energy and power-demand
    #   themes (AI data-center load); high ATR% vs. trad utilities.
    "FSLR",   # First Solar — US solar manufacturing, IRA beneficiary
    "VST",    # Vistra — power generation, AI data-center demand theme
    "CEG",    # Constellation Energy — nuclear renaissance, AI power
    #
    # Software (4) — high-growth SaaS/EDA with strong momentum and
    #   frequent technical setups.
    "HUBS",   # HubSpot — SMB CRM/marketing, consistent growth
    "TTD",    # The Trade Desk — programmatic ads, CTV catalyst
    "CDNS",   # Cadence Design — EDA duopoly, AI chip design demand
    "SNPS",   # Synopsys — EDA duopoly, semiconductor design tools
    #
    # Industrials (3) — higher-beta industrial/infrastructure plays
    #   with strong momentum persistence.
    "URI",    # United Rentals — equipment rental, infrastructure spend
    "PWR",    # Quanta Services — utility/grid infrastructure buildout
    "AXON",   # Axon Enterprise — law-enforcement tech, AI/body-cam
    #
    # Fintech/Other (3) — speculative/high-vol names that generate
    #   frequent triggers; capped via CRYPTO_ADJACENT cluster.
    "MSTR",   # MicroStrategy — leveraged BTC proxy
    "HOOD",   # Robinhood — retail brokerage, crypto/meme catalyst
    "SOFI",   # SoFi Technologies — digital banking/lending platform
]

# Backward-compat alias (deprecated)
MEGA50_TICKERS = UNIVERSE_TICKERS

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
    # --- Expansion tickers ---
    # AI/Cloud/Cyber
    "CRWD": "Information Technology", "PANW": "Information Technology",
    "SNOW": "Information Technology", "NET": "Information Technology",
    "DDOG": "Information Technology", "ZS": "Information Technology",
    # Semis
    "MRVL": "Information Technology", "ON": "Information Technology",
    "KLAC": "Information Technology", "ARM": "Information Technology",
    # Consumer/Internet
    "COIN": "Financials", "SHOP": "Information Technology",
    "UBER": "Consumer Discretionary", "ABNB": "Consumer Discretionary",
    "DASH": "Consumer Discretionary",
    # Biotech/Med-tech
    "ISRG": "Health Care", "VRTX": "Health Care",
    "REGN": "Health Care", "DXCM": "Health Care",
    # Energy/Utilities
    "FSLR": "Energy", "VST": "Utilities", "CEG": "Utilities",
    # Software
    "HUBS": "Information Technology", "TTD": "Information Technology",
    "CDNS": "Information Technology", "SNPS": "Information Technology",
    # Industrials
    "URI": "Industrials", "PWR": "Industrials", "AXON": "Industrials",
    # Fintech/Other
    "MSTR": "Information Technology", "HOOD": "Financials", "SOFI": "Financials",
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
    "Utilities": "XLU",
}

# Correlation clusters for portfolio constraints
CORRELATION_CLUSTERS: Final[list[dict]] = [
    {
        "cluster_id": "SEMIS",
        "tickers": ["NVDA", "AMD", "MU", "AVGO", "LRCX", "AMAT", "MRVL", "ON", "ARM"],
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
    {
        "cluster_id": "CYBERSECURITY",
        "tickers": ["CRWD", "PANW", "ZS"],
        "max_simultaneous": 1,
    },
    {
        "cluster_id": "CLOUD_INFRA",
        "tickers": ["SNOW", "NET", "DDOG"],
        "max_simultaneous": 1,
    },
    {
        "cluster_id": "CRYPTO_ADJACENT",
        "tickers": ["COIN", "MSTR", "HOOD"],
        "max_simultaneous": 1,
    },
    {
        "cluster_id": "EDA_SEMIS",
        "tickers": ["CDNS", "SNPS", "KLAC", "LRCX", "AMAT"],
        "max_simultaneous": 2,
    },
    {
        "cluster_id": "GIG_ECONOMY",
        "tickers": ["UBER", "DASH", "ABNB"],
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


def is_universe_ticker(ticker: str) -> bool:
    """Check if ticker is in the Eiqora universe."""
    return ticker in UNIVERSE_TICKERS


# Backward-compat alias (deprecated)
is_mega50_ticker = is_universe_ticker

