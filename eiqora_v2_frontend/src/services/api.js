import axios from 'axios';

const api = axios.create({
    baseURL: '/api',
    headers: {
        'Content-Type': 'application/json',
    },
});

// Dashboard statistics
export const getDashboardStats = async () => {
    const response = await api.get('/dashboard-stats');
    return response.data;
};

// Equity history for chart
export const getEquityHistory = async () => {
    const response = await api.get('/equity-history');
    return response.data;
};

// Decisions list with pagination
export const getDecisions = async (params = {}) => {
    const response = await api.get('/decisions', { params });
    return response.data;
};

// Single decision details
export const getDecisionDetails = async (analysisId) => {
    const response = await api.get(`/decisions/${analysisId}`);
    return response.data;
};

// Portfolio positions
export const getPositions = async () => {
    const response = await api.get('/positions');
    return response.data;
};

// Trading history
export const getTradingHistory = async () => {
    const response = await api.get('/trading-history');
    return response.data;
};

// Account summary (cash balance, equity)
export const getAccount = async () => {
    const response = await api.get('/account');
    return response.data;
};

export default api;
