import React, { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip } from 'recharts';
import { getPositions } from '../services/api';

function Portfolio() {
    const [positions, setPositions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        const fetchPositions = async () => {
            try {
                const data = await getPositions();
                setPositions(data);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch positions:', err);
                setError('Failed to load portfolio positions');
            } finally {
                setLoading(false);
            }
        };

        fetchPositions();
    }, []);

    if (loading) {
        return <div className="loading">Loading positions...</div>;
    }

    if (error) {
        return <div className="error">{error}</div>;
    }

    if (positions.length === 0) {
        return <div className="loading">No positions currently held</div>;
    }

    const totalValue = positions.reduce((sum, pos) => sum + pos.market_value, 0);

    // Prepare data for pie chart
    const chartData = positions.map(pos => ({
        name: pos.symbol,
        value: pos.market_value,
        percentage: ((pos.market_value / totalValue) * 100).toFixed(1)
    }));

    // Colors for pie chart
    const COLORS = ['#64B5F6', '#81C784', '#FFB74D', '#E57373', '#9575CD', '#4DB6AC'];

    return (
        <div style={{ marginTop: '24px' }}>
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
                                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
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
                                <th>Company</th>
                                <th>Shares</th>
                                <th>Entry Price</th>
                                <th>Current Price</th>
                                <th>Market Value</th>
                                <th>P&L</th>
                                <th>P&L %</th>
                                <th>Entry Date</th>
                            </tr>
                        </thead>
                        <tbody>
                            {positions.map((position) => {
                                const pnl = position.market_value - (position.entry_price * position.shares);
                                const pnlPercent = ((position.current_price - position.entry_price) / position.entry_price) * 100;
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
                                        <td>{position.shares}</td>
                                        <td>${position.entry_price.toFixed(2)}</td>
                                        <td>${position.current_price.toFixed(2)}</td>
                                        <td>${position.market_value.toLocaleString()}</td>
                                        <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                            ${Math.abs(pnl).toLocaleString()} {isProfitable ? '▲' : '▼'}
                                        </td>
                                        <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                            {pnlPercent >= 0 ? '+' : ''}{pnlPercent.toFixed(2)}%
                                        </td>
                                        <td className="text-muted text-sm">
                                            {new Date(position.entry_date).toLocaleDateString()}
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
