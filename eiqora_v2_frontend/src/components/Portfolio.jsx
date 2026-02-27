import React, { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip } from 'recharts';
import { getPositions, getAccount } from '../services/api';

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

function Portfolio() {
    const [positions, setPositions] = useState([]);
    const [account, setAccount] = useState(null);
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
                const [positionsData, accountData] = await Promise.all([
                    getPositions(),
                    getAccount()
                ]);
                setPositions(positionsData);
                setAccount(accountData);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch portfolio data:', err);
                setError('Failed to load portfolio data');
            } finally {
                setLoading(false);
            }
        };

        fetchData();

        const intervalId = setInterval(fetchData, 30000);
        return () => clearInterval(intervalId);
    }, []);

    if (loading) {
        return <div className="loading">Loading portfolio...</div>;
    }

    if (error) {
        return <div className="error">{error}</div>;
    }

    // API returns current_price and entry_price but not market_value directly
    // Calculate market_value from current_price * shares (if available) or estimate from unrealized_pnl
    const processedPositions = positions.map(pos => {
        // If shares not provided, estimate from unrealized_pnl / price_change
        const priceChange = pos.current_price - pos.entry_price;
        const shares = pos.shares || (priceChange !== 0 ? pos.unrealized_pnl / priceChange : 1);
        const market_value = pos.market_value || (pos.current_price * shares);
        return { ...pos, shares, market_value };
    });

    const sortedPositions = [...processedPositions].sort((a, b) => {
        if (!sortConfig.key) return 0;
        const key = sortConfig.key;
        let aVal = key === 'pnl' ? (a.unrealized_pnl || 0)
                 : key === 'pnl_pct' ? (a.unrealized_pnl_pct || 0)
                 : key === 'entry_date' ? new Date(a.entry_date).getTime()
                 : (a[key] ?? 0);
        let bVal = key === 'pnl' ? (b.unrealized_pnl || 0)
                 : key === 'pnl_pct' ? (b.unrealized_pnl_pct || 0)
                 : key === 'entry_date' ? new Date(b.entry_date).getTime()
                 : (b[key] ?? 0);
        if (typeof aVal === 'string') aVal = aVal.toLowerCase();
        if (typeof bVal === 'string') bVal = bVal.toLowerCase();
        if (aVal < bVal) return sortConfig.dir === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortConfig.dir === 'asc' ? 1 : -1;
        return 0;
    });

    const totalPositionsValue = processedPositions.reduce((sum, pos) => sum + (pos.market_value || 0), 0);
    const cashBalance = account?.cash_balance || 0;
    const totalValue = totalPositionsValue + cashBalance || 1; // Avoid division by zero

    // Prepare data for pie chart (positions + cash)
    const chartData = [
        ...processedPositions.map(pos => ({
            name: pos.symbol,
            value: pos.market_value || 0,
            percentage: ((pos.market_value / totalValue) * 100).toFixed(1)
        })),
        ...(cashBalance > 0 ? [{
            name: 'Cash',
            value: cashBalance,
            percentage: ((cashBalance / totalValue) * 100).toFixed(1)
        }] : [])
    ];

    // Colors for pie chart (cash is gray)
    const COLORS = ['#64B5F6', '#81C784', '#FFB74D', '#E57373', '#9575CD', '#4DB6AC'];
    const CASH_COLOR = '#9E9E9E';

    return (
        <div style={{ marginTop: '24px' }}>
            {/* Portfolio Summary */}
            <div className="border-box" style={{ marginBottom: '24px', padding: '24px' }}>
                <div className="chart-title" style={{ marginBottom: '16px' }}>
                    Portfolio Summary
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
                    <div>
                        <div className="text-muted text-sm">Total Value</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>${totalValue.toFixed(2)}</div>
                    </div>
                    <div>
                        <div className="text-muted text-sm">Positions Value</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>${totalPositionsValue.toFixed(2)}</div>
                    </div>
                    <div>
                        <div className="text-muted text-sm">Cash Balance</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>${cashBalance.toFixed(2)}</div>
                    </div>
                    <div>
                        <div className="text-muted text-sm">Unrealized P&L</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 600, color: account?.unrealized_pnl >= 0 ? 'var(--color-go)' : 'var(--color-no-go)' }}>
                            ${(account?.unrealized_pnl || 0).toFixed(2)}
                        </div>
                    </div>
                </div>
            </div>

            {/* Portfolio Allocation Chart */}
            <div className="border-box" style={{ marginBottom: '24px' }}>
                <div className="chart-title" style={{ padding: '24px', borderBottom: '1px solid var(--border-light)' }}>
                    Portfolio Allocation
                </div>
                <div style={{ padding: '24px' }}>
                    <ResponsiveContainer width="100%" height={300}>
                        <PieChart>
                            <Pie
                                data={chartData}
                                cx="50%"
                                cy="50%"
                                labelLine={false}
                                label={({ name, percentage }) => `${name} ${percentage}%`}
                                outerRadius={100}
                                fill="#8884d8"
                                dataKey="value"
                            >
                                {chartData.map((entry, index) => (
                                    <Cell
                                        key={`cell-${index}`}
                                        fill={entry.name === 'Cash' ? CASH_COLOR : COLORS[index % COLORS.length]}
                                    />
                                ))}
                            </Pie>
                            <Tooltip
                                formatter={(value) => `$${value.toLocaleString()}`}
                                contentStyle={{
                                    backgroundColor: 'var(--bg-surface)',
                                    border: '1px solid var(--border-primary)',
                                    borderRadius: 0,
                                    fontFamily: "'JetBrains Mono', monospace",
                                    fontSize: 12,
                                    color: 'var(--text-primary)'
                                }}
                                itemStyle={{ color: 'var(--text-primary)' }}
                            />
                            <Legend
                                wrapperStyle={{
                                    fontFamily: "'JetBrains Mono', monospace",
                                    fontSize: 12
                                }}
                            />
                        </PieChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Holdings Table */}
            <div className="border-box">
                <div className="chart-title" style={{ padding: '24px', borderBottom: '1px solid var(--border-light)' }}>
                    Current Holdings - Total Value: ${totalValue.toLocaleString()}
                </div>
                <div className="table-container">
                    <table>
                        <thead>
                            <tr>
                                <SortHeader label="Company" sortKey="symbol" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Shares" sortKey="shares" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Entry Price" sortKey="entry_price" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Current Price" sortKey="current_price" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Market Value" sortKey="market_value" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="P&L" sortKey="pnl" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="P&L %" sortKey="pnl_pct" sortConfig={sortConfig} onSort={handleSort} />
                                <SortHeader label="Entry Date" sortKey="entry_date" sortConfig={sortConfig} onSort={handleSort} />
                            </tr>
                        </thead>
                        <tbody>
                            {sortedPositions.map((position) => {
                                const pnl = position.unrealized_pnl || (position.market_value - (position.entry_price * position.shares));
                                const pnlPercent = position.unrealized_pnl_pct || ((position.current_price - position.entry_price) / position.entry_price) * 100;
                                const isProfitable = pnl >= 0;

                                return (
                                    <tr key={position.symbol}>
                                        <td>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                                                <img
                                                    src={`https://img.logokit.com/ticker/${position.symbol}?token=pk_fr4a8c50224be943a466c9`}
                                                    alt={position.symbol}
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
                                                <span className="font-bold">{position.symbol}</span>
                                            </div>
                                        </td>
                                        <td>{(position.shares || 0).toFixed(2)}</td>
                                        <td>${(position.entry_price || 0).toFixed(2)}</td>
                                        <td>${(position.current_price || 0).toFixed(2)}</td>
                                        <td>${(position.market_value || 0).toFixed(2)}</td>
                                        <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                            ${Math.abs(pnl).toLocaleString()} {isProfitable ? '▲' : '▼'}
                                        </td>
                                        <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                            {pnlPercent >= 0 ? '+' : ''}{pnlPercent.toFixed(2)}%
                                        </td>
                                        <td className="text-muted text-sm">
                                            {formatSGT(position.entry_date)}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}

export default Portfolio;
