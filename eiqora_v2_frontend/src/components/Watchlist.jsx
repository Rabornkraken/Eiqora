import React, { useState, useEffect } from 'react';
import { getWatchlist } from '../services/api';

function Watchlist({ onSymbolClick }) {
    const [watchlist, setWatchlist] = useState([]);
    const [scanDate, setScanDate] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        fetchWatchlist();
    }, []);

    const fetchWatchlist = async () => {
        try {
            const data = await getWatchlist();
            setWatchlist(data.watchlist || []);
            setScanDate(data.date);
            setLoading(false);
        } catch (err) {
            console.error('Failed to fetch watchlist:', err);
            setError('Failed to load watchlist');
            setLoading(false);
        }
    };

    // Score color based on value
    const getScoreColor = (score) => {
        if (score >= 0.8) return 'var(--green)';
        if (score >= 0.7) return 'var(--text-secondary)';
        return 'var(--text-muted)';
    };

    if (loading) {
        return (
            <div className="watchlist-container">
                <div className="watchlist-header">
                    <h3>Daily Watchlist</h3>
                </div>
                <div className="loading-state">Loading watchlist...</div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="watchlist-container">
                <div className="watchlist-header">
                    <h3>Daily Watchlist</h3>
                </div>
                <div className="error-state">{error}</div>
            </div>
        );
    }

    return (
        <div className="watchlist-container">
            <div className="watchlist-header">
                <h3>Daily Watchlist</h3>
                <span className="watchlist-meta">
                    {watchlist.length} stocks · {scanDate || 'No data'}
                </span>
            </div>

            {watchlist.length === 0 ? (
                <div className="empty-state">No watchlist data available</div>
            ) : (
                <div className="watchlist-table-wrapper">
                    <table className="watchlist-table">
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Score</th>
                            </tr>
                        </thead>
                        <tbody>
                            {watchlist.map((item) => (
                                <tr
                                    key={item.symbol}
                                    onClick={() => onSymbolClick && onSymbolClick(item.symbol)}
                                    style={{ cursor: onSymbolClick ? 'pointer' : undefined }}
                                >
                                    <td className="symbol-cell">
                                        <div className="symbol-with-logo">
                                            <img
                                                src={`https://img.logokit.com/ticker/${item.symbol}?token=pk_fr4a8c50224be943a466c9`}
                                                alt={item.symbol}
                                                className="company-logo-small"
                                                onError={(e) => {
                                                    e.target.style.display = 'none';
                                                }}
                                            />
                                            <span className="symbol-text">{item.symbol}</span>
                                        </div>
                                    </td>
                                    <td className="score-cell-compact">
                                        <span className="score-value" style={{ color: getScoreColor(item.total_score) }}>
                                            {(item.total_score * 100).toFixed(0)}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

export default Watchlist;
