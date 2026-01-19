import React, { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { getEquityHistory } from '../services/api';

function EquityChart() {
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        const fetchData = async () => {
            try {
                const equityData = await getEquityHistory();
                setData(equityData);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch equity data:', err);
                setError('Failed to load equity chart data');
            } finally {
                setLoading(false);
            }
        };

        // Fetch immediately on mount
        fetchData();

        // Set up polling to refresh every 30 seconds for live updates
        const intervalId = setInterval(fetchData, 30000); // 30 seconds

        // Cleanup interval on unmount
        return () => clearInterval(intervalId);
    }, []);

    if (loading) {
        return (
            <div className="chart-box">
                <div className="chart-title">Portfolio Equity</div>
                <div className="loading">Loading chart data...</div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="chart-box">
                <div className="chart-title">Portfolio Equity</div>
                <div className="error">{error}</div>
            </div>
        );
    }

    if (data.length === 0) {
        return (
            <div className="chart-box">
                <div className="chart-title">Portfolio Equity</div>
                <div className="loading">No equity data available yet</div>
            </div>
        );
    }

    // Starting balance for reference line
    const startingBalance = 10000;

    // Get colors from CSS variables
    const lineColor = getComputedStyle(document.documentElement)
        .getPropertyValue('--chart-line').trim() || '#64B5F6';
    const referenceColor = getComputedStyle(document.documentElement)
        .getPropertyValue('--chart-reference').trim() || '#9E9E9E';

    return (
        <div className="chart-box">
            <div className="chart-title">Portfolio Equity</div>
            <ResponsiveContainer width="100%" height={400}>
                <AreaChart data={data} margin={{ top: 10, right: 30, left: 20, bottom: 5 }}>
                    <defs>
                        <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor={lineColor} stopOpacity={0.4} />
                            <stop offset="95%" stopColor={lineColor} stopOpacity={0} />
                        </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                    <XAxis
                        dataKey="date"
                        stroke="var(--text-secondary)"
                        style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}
                        tickFormatter={(value) => {
                            const date = new Date(value);
                            return `${date.getMonth() + 1}/${date.getDate()}`;
                        }}
                    />
                    <YAxis
                        stroke="var(--text-secondary)"
                        style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}
                        tickFormatter={(value) => `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
                        domain={['auto', 'auto']}
                        scale="linear"
                        tickCount={8}
                    />
                    <Tooltip
                        contentStyle={{
                            backgroundColor: 'var(--bg-surface)',
                            border: '1px solid var(--border-primary)',
                            borderRadius: 0,
                            fontFamily: "'JetBrains Mono', monospace",
                            fontSize: 12,
                            color: 'var(--text-primary)'
                        }}
                        formatter={(value) => [`$${value.toLocaleString()}`, 'Equity']}
                        labelFormatter={(label) => {
                            const date = new Date(label);
                            return date.toLocaleDateString();
                        }}
                    />
                    <ReferenceLine
                        y={startingBalance}
                        stroke={referenceColor}
                        strokeDasharray="5 5"
                        strokeWidth={1.5}
                        label={{
                            value: 'Starting Balance',
                            position: 'insideTopRight',
                            style: {
                                fontFamily: "'JetBrains Mono', monospace",
                                fontSize: 10,
                                fill: referenceColor
                            }
                        }}
                    />
                    <Area
                        type="monotone"
                        dataKey="equity"
                        stroke={lineColor}
                        strokeWidth={2.5}
                        fill="url(#equityGradient)"
                        dot={false}
                        activeDot={{ r: 6, fill: lineColor, stroke: 'var(--bg-surface)', strokeWidth: 2 }}
                    />
                </AreaChart>
            </ResponsiveContainer>
        </div>
    );
}

export default EquityChart;
