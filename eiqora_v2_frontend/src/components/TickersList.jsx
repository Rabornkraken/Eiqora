import React, { useState, useEffect, useMemo } from 'react';
import { getSymbolsList } from '../services/api';

function TickersList({ onSymbolClick }) {
    const [symbols, setSymbols] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');

    useEffect(() => {
        const fetchSymbols = async () => {
            try {
                const data = await getSymbolsList();
                setSymbols(data.symbols || []);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch symbols:', err);
                setError('Failed to load tickers');
            } finally {
                setLoading(false);
            }
        };
        fetchSymbols();
    }, []);

    const filtered = useMemo(() => {
        if (!search) return symbols;
        const q = search.toUpperCase();
        return symbols.filter(s => s.symbol.includes(q));
    }, [symbols, search]);

    const formatVolume = (vol) => {
        if (vol == null) return '-';
        if (vol >= 1_000_000) return (vol / 1_000_000).toFixed(1) + 'M';
        if (vol >= 1_000) return (vol / 1_000).toFixed(0) + 'K';
        return vol.toLocaleString();
    };

    if (loading) {
        return (
            <div className="tickers-container">
                <div className="loading">Loading tickers...</div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="tickers-container">
                <div className="error">{error}</div>
            </div>
        );
    }

    return (
        <div className="tickers-container">
            <div className="tickers-header">
                <h3>All Tickers</h3>
                <span className="tickers-meta">
                    {filtered.length} of {symbols.length} symbols
                </span>
            </div>

            <div className="tickers-search">
                <input
                    type="text"
                    placeholder="Search symbol or company..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="tickers-search-input"
                />
            </div>

            <div className="tickers-table-wrapper">
                <table className="tickers-table">
                    <thead>
                        <tr>
                            <th></th>
                            <th>Symbol</th>
                            <th style={{ textAlign: 'right' }}>Last Close</th>
                            <th style={{ textAlign: 'right' }}>Change %</th>
                            <th style={{ textAlign: 'right' }}>Volume</th>
                        </tr>
                    </thead>
                    <tbody>
                        {filtered.map((s) => {
                            const isPositive = s.change_pct != null && s.change_pct >= 0;
                            const isNegative = s.change_pct != null && s.change_pct < 0;
                            return (
                                <tr
                                    key={s.symbol}
                                    onClick={() => onSymbolClick(s.symbol)}
                                >
                                    <td style={{ width: 28, padding: '6px 4px' }}>
                                        <img
                                            src={`https://img.logokit.com/ticker/${s.symbol}?token=pk_fr4a8c50224be943a466c9`}
                                            alt=""
                                            className="company-logo-small"
                                            onError={(e) => { e.target.style.display = 'none'; }}
                                        />
                                    </td>
                                    <td className="font-bold">{s.symbol}</td>
                                    <td style={{ textAlign: 'right' }}>
                                        {s.last_close != null ? `$${s.last_close.toFixed(2)}` : '-'}
                                    </td>
                                    <td style={{ textAlign: 'right' }}>
                                        {s.change_pct != null ? (
                                            <span className={isPositive ? 'status-go' : 'status-no-go'}>
                                                {isPositive ? '+' : ''}{s.change_pct.toFixed(2)}%
                                            </span>
                                        ) : '-'}
                                    </td>
                                    <td style={{ textAlign: 'right' }} className="text-secondary">
                                        {formatVolume(s.volume)}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export default TickersList;
