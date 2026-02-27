import React, { useState, useEffect } from 'react';
import { getBrokerAccount, getBrokerPositions } from '../services/api';

function SortHeader({ label, sortKey, sortConfig, onSort }) {
    const isActive = sortConfig.key === sortKey;
    const arrow = isActive ? (sortConfig.dir === 'asc' ? ' ▲' : ' ▼') : '';
    return (
        <th onClick={() => onSort(sortKey)} style={{ cursor: 'pointer', userSelect: 'none' }}>
            {label}{arrow}
        </th>
    );
}

function AlpacaPortfolio() {
    const [account, setAccount] = useState(null);
    const [positions, setPositions] = useState([]);
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
        const fetchData = async () => {
            try {
                const [accountData, positionsData] = await Promise.all([
                    getBrokerAccount(),
                    getBrokerPositions()
                ]);
                setAccount(accountData);
                setPositions(positionsData.positions || []);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch Alpaca data:', err);
                setError('Failed to load Alpaca data');
            } finally {
                setLoading(false);
            }
        };

        fetchData();

        const intervalId = setInterval(fetchData, 30000);
        return () => clearInterval(intervalId);
    }, []);

    if (loading) {
        return <div className="loading">Loading Alpaca portfolio...</div>;
    }

    if (error) {
        return <div className="error">{error}</div>;
    }

    if (account && account.configured === false) {
        return (
            <div style={{ marginTop: '24px' }}>
                <div className="border-box" style={{ padding: '48px', textAlign: 'center' }}>
                    <div className="text-muted">Alpaca not configured</div>
                </div>
            </div>
        );
    }

    const sortedPositions = [...positions].sort((a, b) => {
        if (!sortConfig.key) return 0;
        const key = sortConfig.key;
        let aVal = key === 'pnl' ? (a.unrealized_pl || 0)
                 : key === 'pnl_pct' ? (a.unrealized_plpc || 0)
                 : (a[key] ?? 0);
        let bVal = key === 'pnl' ? (b.unrealized_pl || 0)
                 : key === 'pnl_pct' ? (b.unrealized_plpc || 0)
                 : (b[key] ?? 0);
        if (typeof aVal === 'string') aVal = aVal.toLowerCase();
        if (typeof bVal === 'string') bVal = bVal.toLowerCase();
        if (aVal < bVal) return sortConfig.dir === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortConfig.dir === 'asc' ? 1 : -1;
        return 0;
    });

    return (
        <div style={{ marginTop: '24px' }}>
            {/* Account Summary */}
            <div className="border-box" style={{ marginBottom: '24px', padding: '24px' }}>
                <div className="chart-title" style={{ marginBottom: '16px' }}>
                    Alpaca Paper Account
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
                    <div>
                        <div className="text-muted text-sm">Equity</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>
                            ${Number(account?.equity || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </div>
                    </div>
                    <div>
                        <div className="text-muted text-sm">Cash</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>
                            ${Number(account?.cash || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </div>
                    </div>
                    <div>
                        <div className="text-muted text-sm">Buying Power</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>
                            ${Number(account?.buying_power || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </div>
                    </div>
                    <div>
                        <div className="text-muted text-sm">Account Status</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 600, textTransform: 'uppercase' }}>
                            {account?.status || '-'}
                        </div>
                    </div>
                </div>
            </div>

            {/* Positions Table */}
            <div className="border-box">
                <div className="chart-title" style={{ padding: '24px', borderBottom: '1px solid var(--border-light)' }}>
                    Alpaca Positions
                </div>
                <div className="table-container">
                    <table>
                        <thead>
                            <tr>
                                <SortHeader label="Company" sortKey="symbol" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Side" sortKey="side" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Qty" sortKey="qty" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Avg Entry" sortKey="avg_entry_price" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Current" sortKey="current_price" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Market Value" sortKey="market_value" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="P&L" sortKey="pnl" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="P&L %" sortKey="pnl_pct" sortConfig={sortConfig} onSort={handleSort} />
                            </tr>
                        </thead>
                        <tbody>
                            {sortedPositions.length === 0 ? (
                                <tr>
                                    <td colSpan="8" style={{ textAlign: 'center', padding: '48px' }} className="text-muted">
                                        No open positions on Alpaca
                                    </td>
                                </tr>
                            ) : (
                                sortedPositions.map((pos) => {
                                    const pnl = Number(pos.unrealized_pl || 0);
                                    const pnlPct = Number(pos.unrealized_plpc || 0) * 100;
                                    const isProfitable = pnl >= 0;

                                    return (
                                        <tr key={pos.symbol}>
                                            <td>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                                                    <img
                                                        src={`https://img.logokit.com/ticker/${pos.symbol}?token=pk_fr4a8c50224be943a466c9`}
                                                        alt={pos.symbol}
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
                                                    <span className="font-bold">{pos.symbol}</span>
                                                </div>
                                            </td>
                                            <td style={{ textTransform: 'uppercase' }}>{pos.side}</td>
                                            <td>{Number(pos.qty || 0)}</td>
                                            <td>${Number(pos.avg_entry_price || 0).toFixed(2)}</td>
                                            <td>${Number(pos.current_price || 0).toFixed(2)}</td>
                                            <td>${Number(pos.market_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                                            <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                                ${Math.abs(pnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {isProfitable ? '▲' : '▼'}
                                            </td>
                                            <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                                {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                                            </td>
                                        </tr>
                                    );
                                })
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}

export default AlpacaPortfolio;
