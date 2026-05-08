import React, { useState, useEffect, useMemo } from 'react';
import {
    ComposedChart,
    Area,
    Line,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    Legend,
    ResponsiveContainer,
    ReferenceLine,
} from 'recharts';
import { getEquityHistory } from '../services/api';

const BENCHMARKS = [
    { symbol: 'SPY', label: 'SPY (S&P 500)', color: '#FFA726' },
    { symbol: 'QQQ', label: 'QQQ (Nasdaq 100)', color: '#AB47BC' },
    { symbol: 'IWM', label: 'IWM (Russell 2000)', color: '#26A69A' },
    { symbol: 'XLK', label: 'XLK (Tech Sector)', color: '#EF5350' },
];

const TIMEFRAMES = [
    { id: '1W', label: '1W', days: 7 },
    { id: '1M', label: '1M', days: 31 },
    { id: '3M', label: '3M', days: 93 },
    { id: 'YTD', label: 'YTD', days: null }, // computed below
    { id: 'ALL', label: 'All', days: 365 },
];

function ytdDays() {
    const now = new Date();
    const jan1 = new Date(now.getFullYear(), 0, 1);
    return Math.max(1, Math.ceil((now - jan1) / (1000 * 60 * 60 * 24)));
}

function EquityChart() {
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [timeframe, setTimeframe] = useState('1M');

    const days = useMemo(() => {
        const tf = TIMEFRAMES.find(t => t.id === timeframe) || TIMEFRAMES[1];
        return tf.id === 'YTD' ? ytdDays() : tf.days;
    }, [timeframe]);

    useEffect(() => {
        let cancelled = false;
        const fetchData = async () => {
            try {
                setLoading(true);
                const benchSymbols = BENCHMARKS.map(b => b.symbol);
                const equityData = await getEquityHistory(days, benchSymbols);
                if (cancelled) return;
                setData(equityData);
                setError(null);
            } catch (err) {
                if (cancelled) return;
                console.error('Failed to fetch equity data:', err);
                setError('Failed to load equity chart data');
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        fetchData();
        // Poll every 30s for live updates
        const intervalId = setInterval(fetchData, 30000);
        return () => {
            cancelled = true;
            clearInterval(intervalId);
        };
    }, [days]);

    const lineColor = getComputedStyle(document.documentElement)
        .getPropertyValue('--chart-line').trim() || '#64B5F6';
    const referenceColor = getComputedStyle(document.documentElement)
        .getPropertyValue('--chart-reference').trim() || '#9E9E9E';

    // Reference line = first data point's equity (auto-adjusts to whatever
    // starting balance the account actually had — 10K, 100K, anything).
    const startingBalance = data.length > 0 ? data[0].equity : null;

    // Header stats
    const stats = useMemo(() => {
        if (!data.length || !startingBalance) return null;
        const last = data[data.length - 1];
        const ret = last.equity - startingBalance;
        const retPct = (ret / startingBalance) * 100;
        const benchSummary = BENCHMARKS.map(b => {
            const lastVal = last[b.symbol];
            if (typeof lastVal !== 'number') return null;
            const bRet = ((lastVal - startingBalance) / startingBalance) * 100;
            return { symbol: b.symbol, color: b.color, ret: bRet, delta: retPct - bRet };
        }).filter(Boolean);
        return { ret, retPct, benchSummary };
    }, [data, startingBalance]);

    return (
        <div className="chart-box">
            <div
                className="chart-title"
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    flexWrap: 'wrap',
                    gap: '12px',
                }}
            >
                <span>Portfolio Equity</span>
                <div style={{ display: 'flex', gap: '4px' }}>
                    {TIMEFRAMES.map(tf => (
                        <button
                            key={tf.id}
                            onClick={() => setTimeframe(tf.id)}
                            style={{
                                padding: '4px 10px',
                                fontFamily: "'JetBrains Mono', monospace",
                                fontSize: 11,
                                background: timeframe === tf.id ? lineColor : 'transparent',
                                color: timeframe === tf.id ? 'var(--bg-surface)' : 'var(--text-secondary)',
                                border: `1px solid ${timeframe === tf.id ? lineColor : 'var(--border-primary)'}`,
                                borderRadius: 0,
                                cursor: 'pointer',
                                fontWeight: timeframe === tf.id ? 600 : 400,
                            }}
                        >
                            {tf.label}
                        </button>
                    ))}
                </div>
            </div>

            {stats && (
                <div
                    style={{
                        display: 'flex',
                        alignItems: 'baseline',
                        gap: '16px',
                        padding: '8px 16px',
                        flexWrap: 'wrap',
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: 12,
                        borderBottom: '1px solid var(--border-light)',
                    }}
                >
                    <span style={{ color: 'var(--text-secondary)' }}>
                        Return:
                        <span
                            style={{
                                color: stats.ret >= 0 ? 'var(--color-go)' : 'var(--color-no-go)',
                                fontWeight: 600,
                                marginLeft: '6px',
                            }}
                        >
                            {stats.ret >= 0 ? '+' : ''}${stats.ret.toFixed(2)} ({stats.retPct >= 0 ? '+' : ''}{stats.retPct.toFixed(2)}%)
                        </span>
                    </span>
                    {stats.benchSummary.map(b => (
                        <span key={b.symbol} style={{ color: 'var(--text-secondary)' }}>
                            vs <span style={{ color: b.color, fontWeight: 600 }}>{b.symbol}</span>:
                            <span
                                style={{
                                    color: b.delta >= 0 ? 'var(--color-go)' : 'var(--color-no-go)',
                                    fontWeight: 600,
                                    marginLeft: '6px',
                                }}
                            >
                                {b.delta >= 0 ? '+' : ''}{b.delta.toFixed(2)}%
                            </span>
                        </span>
                    ))}
                </div>
            )}

            {loading && data.length === 0 ? (
                <div className="loading">Loading chart data...</div>
            ) : error ? (
                <div className="error">{error}</div>
            ) : data.length === 0 ? (
                <div className="loading">No equity data available yet</div>
            ) : (
                <ResponsiveContainer width="100%" height={420}>
                    <ComposedChart data={data} margin={{ top: 10, right: 30, left: 20, bottom: 5 }}>
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
                                color: 'var(--text-primary)',
                            }}
                            formatter={(value, name) => [
                                typeof value === 'number' ? `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '—',
                                name,
                            ]}
                            labelFormatter={(label) => new Date(label).toLocaleDateString()}
                        />
                        <Legend
                            wrapperStyle={{
                                fontFamily: "'JetBrains Mono', monospace",
                                fontSize: 11,
                                paddingTop: '8px',
                            }}
                        />
                        {startingBalance !== null && (
                            <ReferenceLine
                                y={startingBalance}
                                stroke={referenceColor}
                                strokeDasharray="5 5"
                                strokeWidth={1.5}
                                label={{
                                    value: `Starting $${startingBalance.toLocaleString(undefined, { maximumFractionDigits: 0 })}`,
                                    position: 'insideTopRight',
                                    style: {
                                        fontFamily: "'JetBrains Mono', monospace",
                                        fontSize: 10,
                                        fill: referenceColor,
                                    },
                                }}
                            />
                        )}
                        <Area
                            type="monotone"
                            dataKey="equity"
                            name="Portfolio"
                            stroke={lineColor}
                            strokeWidth={2.5}
                            fill="url(#equityGradient)"
                            dot={false}
                            activeDot={{ r: 6, fill: lineColor, stroke: 'var(--bg-surface)', strokeWidth: 2 }}
                        />
                        {BENCHMARKS.map(b => (
                            <Line
                                key={b.symbol}
                                type="monotone"
                                dataKey={b.symbol}
                                name={b.label}
                                stroke={b.color}
                                strokeWidth={1.5}
                                strokeDasharray="4 2"
                                dot={false}
                                activeDot={{ r: 4 }}
                            />
                        ))}
                    </ComposedChart>
                </ResponsiveContainer>
            )}
        </div>
    );
}

export default EquityChart;
