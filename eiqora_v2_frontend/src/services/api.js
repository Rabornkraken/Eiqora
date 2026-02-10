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

// Dashboard statistics
export const getDashboardStats = async () => {
    const endpoint = isV1API ? '/api/v1/dashboard/stats' : '/api/dashboard-stats';
    const response = await api.get(endpoint);

    // Also fetch account state for equity info
    let accountData = {};
    if (isV1API) {
        try {
            const positionsResp = await api.get('/api/v1/positions/open');
            accountData = {
                total_unrealized_pnl: positionsResp.data.total_unrealized_pnl || 0,
                total_positions: positionsResp.data.total || 0
            };
        } catch (e) {
            console.error('Failed to fetch positions for stats:', e);
        }
    }

    const data = response.data;
    // Transform API response to match frontend expectations
    // API returns pre-formatted strings for win_rate, total_return, etc.
    return {
        total_trades: data.total_trades || data.total_analyses || 0,
        win_rate: data.win_rate || null,
        total_return: data.total_return || null,
        current_equity: data.current_equity || null,
        sharpe_ratio: data.sharpe_ratio || null,
        active_positions: data.active_positions || accountData.total_positions || 0,
        unrealized_pnl: data.unrealized_pnl ?? accountData.total_unrealized_pnl ?? 0
    };
};

// Equity history for chart
export const getEquityHistory = async (days = 30) => {
    if (isV1API) {
        const response = await api.get('/api/v1/dashboard/equity-history', { params: { days } });
        return response.data.history || [];
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

// Trading history
export const getTradingHistory = async (limit = 50) => {
    if (isV1API) {
        const response = await api.get('/api/v1/dashboard/trading-history', { params: { limit } });
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

export default api;
