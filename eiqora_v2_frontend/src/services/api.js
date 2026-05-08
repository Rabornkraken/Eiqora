import axios from 'axios';

// Environment variables set at build time via .env.development / .env.production
const API_BASE_URL = import.meta.env.VITE_API_URL;
const API_VERSION = import.meta.env.VITE_API_VERSION; // 'local' or 'v1'

// Check if using Azure API (v1) or local api.py
const isV1API = API_VERSION === 'v1';

const api = axios.create({
    baseURL: API_BASE_URL,
    headers: {
        'Content-Type': 'application/json',
    },
});

// Dashboard statistics. The /stats endpoint is now fully Alpaca-sourced
// (unrealized_pnl + active_positions + equity), so the header values
// match what the Portfolio view shows.
export const getDashboardStats = async () => {
    const endpoint = isV1API ? '/api/v1/dashboard/stats' : '/api/dashboard-stats';
    const response = await api.get(endpoint);
    const data = response.data;
    return {
        total_trades: data.total_trades || data.total_analyses || 0,
        win_rate: data.win_rate || null,
        total_return: data.total_return || null,
        current_equity: data.current_equity || null,
        sharpe_ratio: data.sharpe_ratio || null,
        active_positions: data.active_positions || 0,
        unrealized_pnl: data.unrealized_pnl ?? 0,
    };
};

// Equity history for chart. ``benchmarks`` is an optional array like
// ['SPY','QQQ','IWM','XLK'] — when provided, each row in the response is
// enriched with a value-of-equivalent-investment per benchmark, anchored
// at the starting equity.
export const getEquityHistory = async (days = 30, benchmarks = null) => {
    if (isV1API) {
        const params = { days };
        if (Array.isArray(benchmarks) && benchmarks.length) {
            params.benchmarks = benchmarks.join(',');
        }
        const response = await api.get('/api/v1/dashboard/equity-history', { params });
        // Map timestamp -> date for chart XAxis compatibility
        return (response.data.history || []).map(item => ({
            ...item,
            date: item.timestamp,
        }));
    }
    const response = await api.get('/api/equity-history');
    return response.data;
};

// Decisions list with pagination (analysis logs)
export const getDecisions = async (params = {}) => {
    if (isV1API) {
        const response = await api.get('/api/v1/analysis/', { params });
        return response.data.analyses || [];
    }
    const response = await api.get('/api/decisions', { params });
    return response.data;
};

// Single decision details
export const getDecisionDetails = async (analysisId) => {
    const endpoint = isV1API ? `/api/v1/analysis/${analysisId}` : `/api/decisions/${analysisId}`;
    const response = await api.get(endpoint);
    return response.data;
};

// Portfolio positions
export const getPositions = async () => {
    if (isV1API) {
        const response = await api.get('/api/v1/positions/open');
        return response.data.positions || [];
    }
    const response = await api.get('/api/positions');
    return response.data;
};

// Trading history. source='alpaca' (default) for broker-matched trades,
// source='legacy' for DB-only pre-broker rows.
export const getTradingHistory = async (limit = 50, source = 'alpaca') => {
    if (isV1API) {
        const response = await api.get('/api/v1/dashboard/trading-history', { params: { limit, source } });
        return response.data.trades || [];
    }
    const response = await api.get('/api/trading-history');
    return response.data;
};

// Account summary (cash balance, equity)
export const getAccount = async () => {
    if (isV1API) {
        const response = await api.get('/api/v1/dashboard/account');
        return response.data;
    }
    const response = await api.get('/api/account');
    return response.data;
};

// Watchlist
export const getWatchlist = async (date = null) => {
    const params = date ? { date } : {};
    if (isV1API) {
        const response = await api.get('/api/v1/dashboard/watchlist', { params });
        return {
            date: response.data.date,
            watchlist: response.data.watchlist || [],
            total_candidates: response.data.total_candidates || 0
        };
    }
    const response = await api.get('/api/watchlist', { params });
    return {
        date: response.data.scan_date,
        watchlist: response.data.watchlist || [],
        total_candidates: response.data.symbols_count || 0
    };
};

// Market regime
export const getMarketRegime = async () => {
    if (isV1API) {
        const response = await api.get('/api/v1/dashboard/regime');
        return response.data;
    }
    return { regime: 'N/A' };
};

// Recent signals
export const getSignals = async (limit = 10) => {
    if (isV1API) {
        const response = await api.get('/api/v1/dashboard/signals', { params: { limit } });
        return response.data;
    }
    return { signals: [] };
};

// Economic calendar
export const getEconomicCalendar = async (days = 14) => {
    const response = await api.get('/api/v1/calendar/upcoming', { params: { days } });
    return response.data;
};

// Distinct tickers that have news
export const getNewsTickers = async () => {
    const response = await api.get('/api/v1/news/tickers');
    return response.data;
};

// News feed with optional symbol filter and pagination
export const getNews = async ({ symbol = null, page = 1, page_size = 25 } = {}) => {
    const params = { page, page_size };
    if (symbol) params.symbol = symbol;
    const response = await api.get('/api/v1/news/', { params });
    return response.data;
};

// All universe symbols with latest price data
export const getSymbolsList = async (search = null) => {
    const params = {};
    if (search) params.search = search;
    const response = await api.get('/api/v1/symbol/', { params });
    return response.data;
};

// Symbol detail (price, indicators, news, earnings, ratings, options)
export const getSymbolDetail = async (symbol, days = 90) => {
    const response = await api.get(`/api/v1/symbol/${symbol}`, { params: { days } });
    return response.data;
};

// Alpaca broker account
export const getBrokerAccount = async () => {
    const response = await api.get('/api/v1/positions/broker/account');
    return response.data;
};

// Alpaca broker positions
export const getBrokerPositions = async () => {
    const response = await api.get('/api/v1/positions/broker/positions');
    return response.data;
};

export default api;
