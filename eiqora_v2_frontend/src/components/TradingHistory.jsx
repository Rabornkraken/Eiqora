import React, { useState, useEffect } from 'react';
import { getTradingHistory } from '../services/api';

function formatSGT(dateValue) {
    if (!dateValue) return '-';
    const d = new Date(dateValue);
    if (isNaN(d.getTime())) return '-';
    return d.toLocaleString('en-SG', { timeZone: 'Asia/Singapore', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

function SortHeader({ label, sortKey, sortConfig, onSort }) {
    const isActive = sortConfig.key === sortKey;
    const arrow = isActive ? (sortConfig.dir === 'asc' ? ' ▲' : ' ▼') : '';
    return (
        <th onClick={() => onSort(sortKey)} style={{ cursor: 'pointer', userSelect: 'none' }}>
            {label}{arrow}
        </th>
    );
}

function TradingHistory({ legacy = false }) {
    const [trades, setTrades] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [sortConfig, setSortConfig] = useState({ key: null, dir: 'desc' });

    const handleSort = (key) => {
        setSortConfig(prev => {
            if (prev.key !== key) return { key, dir: 'desc' };
            if (prev.dir === 'desc') return { key, dir: 'asc' };
            return { key: null, dir: 'desc' };
        });
    };

    useEffect(() => {
        const fetchHistory = async () => {
            try {
                setLoading(true);
                const source = legacy ? 'legacy' : 'alpaca';
                const data = await getTradingHistory(50, source);
                setTrades(data);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch trading history:', err);
                setError('Failed to load trading history');
            } finally {
                setLoading(false);
            }
        };

        fetchHistory();
    }, [legacy]);

    if (loading) {
        return <div className="loading">Loading trading history...</div>;
    }

    if (error) {
        return <div className="error">{error}</div>;
    }

    if (trades.length === 0) {
        return <div className="loading">No trading history available</div>;
    }

    const sortedTrades = [...trades].sort((a, b) => {
        if (!sortConfig.key) return 0;
        const key = sortConfig.key;
        let aVal, bVal;
        if (key === 'pnl') {
            aVal = a.realized_pnl ?? a.pnl ?? 0;
            bVal = b.realized_pnl ?? b.pnl ?? 0;
        } else if (key === 'pnl_pct') {
            aVal = a.realized_pnl_pct ?? a.pnl_pct ?? 0;
            bVal = b.realized_pnl_pct ?? b.pnl_pct ?? 0;
        } else if (key === 'entry_date' || key === 'exit_date') {
            aVal = new Date(a[key]).getTime();
            bVal = new Date(b[key]).getTime();
        } else if (key === 'hold_days') {
            aVal = new Date(a.exit_date) - new Date(a.entry_date);
            bVal = new Date(b.exit_date) - new Date(b.entry_date);
        } else {
            aVal = a[key] ?? 0;
            bVal = b[key] ?? 0;
        }
        if (typeof aVal === 'string') aVal = aVal.toLowerCase();
        if (typeof bVal === 'string') bVal = bVal.toLowerCase();
        if (aVal < bVal) return sortConfig.dir === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortConfig.dir === 'asc' ? 1 : -1;
        return 0;
    });

    // Summary stats for the currently displayed source
    const totalPnl = trades.reduce((sum, t) => sum + (t.realized_pnl ?? t.pnl ?? 0), 0);
    const wins = trades.filter(t => (t.realized_pnl ?? t.pnl ?? 0) > 0).length;
    const losses = trades.filter(t => (t.realized_pnl ?? t.pnl ?? 0) < 0).length;
    const winRate = trades.length > 0 ? (wins / trades.length) * 100 : 0;
    const title = legacy ? 'Legacy Trading History' : 'Trading History';

    return (
        <div className="border-box" style={{ marginTop: '24px' }}>
            <div className="chart-title" style={{ padding: '24px', borderBottom: '1px solid var(--border-light)' }}>
                {title} - {trades.length} Completed Trades
            </div>
            {trades.length > 0 && (
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(4, 1fr)',
                    gap: '16px',
                    padding: '16px 24px',
                    borderBottom: '1px solid var(--border-light)',
                }}>
                    <div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Total Trades</div>
                        <div style={{ fontSize: '20px', fontWeight: 600 }}>{trades.length}</div>
                    </div>
                    <div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Win Rate</div>
                        <div style={{ fontSize: '20px', fontWeight: 600 }}>
                            {winRate.toFixed(1)}%
                            <span style={{ fontSize: '12px', color: 'var(--text-secondary)', marginLeft: '6px', fontWeight: 400 }}>
                                ({wins}W / {losses}L)
                            </span>
                        </div>
                    </div>
                    <div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Total P&amp;L</div>
                        <div style={{
                            fontSize: '20px',
                            fontWeight: 600,
                            color: totalPnl >= 0 ? 'var(--color-go)' : 'var(--color-no-go)',
                        }}>
                            {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
                        </div>
                    </div>
                    <div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Avg P&amp;L / Trade</div>
                        <div style={{
                            fontSize: '20px',
                            fontWeight: 600,
                            color: totalPnl >= 0 ? 'var(--color-go)' : 'var(--color-no-go)',
                        }}>
                            {totalPnl >= 0 ? '+' : ''}${(totalPnl / trades.length).toFixed(2)}
                        </div>
                    </div>
                </div>
            )}
            <div className="table-container">
                <table>
                    <thead>
                        <tr>
                            <SortHeader label="Company" sortKey="symbol" sortConfig={sortConfig} onSort={handleSort} />
                            <th>Action</th>
                            <SortHeader label="Shares" sortKey="shares" sortConfig={sortConfig} onSort={handleSort} />
                            <SortHeader label="Entry Price" sortKey="entry_price" sortConfig={sortConfig} onSort={handleSort} />
                            <SortHeader label="Exit Price" sortKey="exit_price" sortConfig={sortConfig} onSort={handleSort} />
                            <SortHeader label="P&L" sortKey="pnl" sortConfig={sortConfig} onSort={handleSort} />
                            <SortHeader label="P&L %" sortKey="pnl_pct" sortConfig={sortConfig} onSort={handleSort} />
                            <SortHeader label="Entry Date" sortKey="entry_date" sortConfig={sortConfig} onSort={handleSort} />
                            <SortHeader label="Exit Date" sortKey="exit_date" sortConfig={sortConfig} onSort={handleSort} />
                            <SortHeader label="Hold Period" sortKey="hold_days" sortConfig={sortConfig} onSort={handleSort} />
                        </tr>
                    </thead>
                    <tbody>
                        {sortedTrades.map((trade, index) => {
                            // Use realized_pnl from API (v1) or pnl (legacy)
                            const pnl = trade.realized_pnl ?? trade.pnl ?? 0;
                            const pnlPercent = trade.realized_pnl_pct ?? trade.pnl_pct ?? 0;
                            const isProfitable = pnl >= 0;

                            // Map direction (v1) or action (legacy) to display
                            const action = trade.direction
                                ? (trade.direction === 'LONG' ? 'BUY' : 'SELL')
                                : (trade.action || 'BUY');
                            const isLong = trade.direction ? trade.direction === 'LONG' : action === 'BUY';

                            const sharesDisplay = trade.shares ? trade.shares.toFixed(2) : '-';

                            const entryDate = new Date(trade.entry_date);
                            const exitDate = new Date(trade.exit_date);
                            const holdDays = Math.floor((exitDate - entryDate) / (1000 * 60 * 60 * 24));

                            return (
                                <tr key={index}>
                                    <td>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                                            <img
                                                src={`https://img.logokit.com/ticker/${trade.symbol}?token=pk_fr4a8c50224be943a466c9`}
                                                alt={trade.symbol}
                                                style={{
                                                    width: '32px',
                                                    height: '32px',
                                                    borderRadius: '4px',
                                                    objectFit: 'contain'
                                                }}
                                                onError={(e) => {
                                                    e.target.style.display = 'none';
                                                }}
                                            />
                                            <span className="font-bold">{trade.symbol}</span>
                                        </div>
                                    </td>
                                    <td className={isLong ? 'status-go' : 'status-no-go'}>
                                        {action}
                                    </td>
                                    <td>{sharesDisplay}</td>
                                    <td>${trade.entry_price.toFixed(2)}</td>
                                    <td>${trade.exit_price.toFixed(2)}</td>
                                    <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                        ${Math.abs(pnl).toFixed(2)} {isProfitable ? '▲' : '▼'}
                                    </td>
                                    <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                        {pnlPercent >= 0 ? '+' : ''}{pnlPercent.toFixed(2)}%
                                    </td>
                                    <td className="text-muted text-sm">
                                        {formatSGT(trade.entry_date)}
                                    </td>
                                    <td className="text-muted text-sm">
                                        {formatSGT(trade.exit_date)}
                                    </td>
                                    <td className="text-muted">{holdDays} days</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export default TradingHistory;
