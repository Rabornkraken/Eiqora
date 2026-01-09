import React, { useState, useEffect } from 'react';
import { getTradingHistory } from '../services/api';

function TradingHistory() {
    const [trades, setTrades] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        const fetchHistory = async () => {
            try {
                const data = await getTradingHistory();
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
    }, []);

    if (loading) {
        return <div className="loading">Loading trading history...</div>;
    }

    if (error) {
        return <div className="error">{error}</div>;
    }

    if (trades.length === 0) {
        return <div className="loading">No trading history available</div>;
    }

    return (
        <div className="border-box" style={{ marginTop: '24px' }}>
            <div className="chart-title" style={{ padding: '24px', borderBottom: '1px solid var(--border-light)' }}>
                Trading History - {trades.length} Completed Trades
            </div>
            <div className="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Company</th>
                            <th>Action</th>
                            <th>Shares</th>
                            <th>Entry Price</th>
                            <th>Exit Price</th>
                            <th>P&L</th>
                            <th>P&L %</th>
                            <th>Entry Date</th>
                            <th>Exit Date</th>
                            <th>Hold Period</th>
                        </tr>
                    </thead>
                    <tbody>
                        {trades.map((trade, index) => {
                            const pnl = (trade.exit_price - trade.entry_price) * trade.shares;
                            const pnlPercent = ((trade.exit_price - trade.entry_price) / trade.entry_price) * 100;
                            const isProfitable = pnl >= 0;

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
                                    <td className={trade.action === 'BUY' ? 'status-go' : 'status-no-go'}>
                                        {trade.action}
                                    </td>
                                    <td>{trade.shares}</td>
                                    <td>${trade.entry_price.toFixed(2)}</td>
                                    <td>${trade.exit_price.toFixed(2)}</td>
                                    <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                        ${Math.abs(pnl).toLocaleString()} {isProfitable ? '▲' : '▼'}
                                    </td>
                                    <td className={isProfitable ? 'status-go' : 'status-no-go'}>
                                        {pnlPercent >= 0 ? '+' : ''}{pnlPercent.toFixed(2)}%
                                    </td>
                                    <td className="text-muted text-sm">
                                        {entryDate.toLocaleDateString()}
                                    </td>
                                    <td className="text-muted text-sm">
                                        {exitDate.toLocaleDateString()}
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
