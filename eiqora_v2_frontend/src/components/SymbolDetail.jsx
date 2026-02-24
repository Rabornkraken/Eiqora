import React, { useState, useEffect } from 'react';
import { getSymbolDetail } from '../services/api';
import {
    AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';

function SymbolDetail({ symbol, onBack }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [chartRange, setChartRange] = useState('1D'); // '1D' or '90D'

    useEffect(() => {
        if (symbol) {
            fetchSymbolDetail();
        }
    }, [symbol]);

    const fetchSymbolDetail = async () => {
        setLoading(true);
        setError(null);
        try {
            const result = await getSymbolDetail(symbol);
            setData(result);
        } catch (err) {
            console.error('Failed to fetch symbol detail:', err);
            setError(err.response?.status === 404
                ? `Symbol '${symbol}' not found`
                : 'Failed to load symbol data');
        } finally {
            setLoading(false);
        }
    };

    const formatNum = (val, decimals = 2) => {
        if (val == null) return '--';
        return Number(val).toFixed(decimals);
    };

    const formatPct = (val) => {
        if (val == null) return '--';
        const sign = val >= 0 ? '+' : '';
        return `${sign}${val.toFixed(2)}%`;
    };

    const formatLargeNum = (val) => {
        if (val == null) return '--';
        if (val >= 1e9) return `${(val / 1e9).toFixed(1)}B`;
        if (val >= 1e6) return `${(val / 1e6).toFixed(1)}M`;
        if (val >= 1e3) return `${(val / 1e3).toFixed(1)}K`;
        return val.toLocaleString();
    };

    const timeAgo = (dateStr) => {
        if (!dateStr) return '';
        const now = new Date();
        const then = new Date(dateStr);
        const diffDays = Math.floor((now - then) / 86400000);
        if (diffDays === 0) return 'Today';
        if (diffDays === 1) return 'Yesterday';
        if (diffDays < 30) return `${diffDays}d ago`;
        return then.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    };

    if (loading) {
        return (
            <div className="symbol-detail">
                <button className="symbol-back-btn" onClick={onBack}>Back to Dashboard</button>
                <div className="loading-state">Loading {symbol} data...</div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="symbol-detail">
                <button className="symbol-back-btn" onClick={onBack}>Back to Dashboard</button>
                <div className="error-state">{error}</div>
            </div>
        );
    }

    if (!data) return null;

    const { quote, price_history, intraday_history = [], indicators, news, earnings, analyst_ratings, options, fundamentals } = data;
    const changePositive = quote.change >= 0;
    const hasIntraday = intraday_history.length > 0;
    const showIntraday = chartRange === '1D' && hasIntraday;

    // Prepare intraday chart data with SGT-formatted time labels
    const intradayChartData = intraday_history.map(bar => {
        const dt = new Date(bar.datetime);
        const sgtTime = dt.toLocaleString('en-SG', {
            timeZone: 'Asia/Singapore',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        });
        return { ...bar, time: sgtTime };
    });

    return (
        <div className="symbol-detail">
            <button className="symbol-back-btn" onClick={onBack}>Back to Dashboard</button>

            {/* Header Card */}
            <div className="symbol-header-card">
                <div className="symbol-header-left">
                    <img
                        src={`https://img.logokit.com/ticker/${symbol}?token=pk_fr4a8c50224be943a466c9`}
                        alt={symbol}
                        className="symbol-logo"
                        onError={(e) => { e.target.style.display = 'none'; }}
                    />
                    <div>
                        <h2 className="symbol-title">{symbol}</h2>
                        <span className="symbol-date">As of {quote.date}</span>
                    </div>
                </div>
                <div className="symbol-header-right">
                    <span className="symbol-price">${formatNum(quote.price)}</span>
                    <span className={`symbol-change ${changePositive ? 'positive' : 'negative'}`}>
                        {formatNum(quote.change)} ({formatPct(quote.change_pct)})
                    </span>
                </div>
            </div>

            {/* Price Chart */}
            <div className="symbol-chart-card">
                <div className="symbol-chart-header">
                    <h3>{showIntraday ? 'Intraday (1 min)' : 'Price History (90 days)'}</h3>
                    <div className="chart-range-toggle">
                        <button
                            className={`range-btn ${chartRange === '1D' ? 'active' : ''}`}
                            onClick={() => setChartRange('1D')}
                            disabled={!hasIntraday}
                        >
                            1D
                        </button>
                        <button
                            className={`range-btn ${chartRange === '90D' ? 'active' : ''}`}
                            onClick={() => setChartRange('90D')}
                        >
                            90D
                        </button>
                    </div>
                </div>
                <ResponsiveContainer width="100%" height={300}>
                    {showIntraday ? (
                        <AreaChart data={intradayChartData}>
                            <defs>
                                <linearGradient id="symbolGradient" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="var(--chart-line)" stopOpacity={0.4} />
                                    <stop offset="95%" stopColor="var(--chart-line)" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                            <XAxis
                                dataKey="time"
                                tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                                interval="preserveStartEnd"
                                minTickGap={40}
                            />
                            <YAxis
                                domain={['auto', 'auto']}
                                tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                                tickFormatter={(v) => `$${v.toFixed(0)}`}
                            />
                            <Tooltip
                                contentStyle={{
                                    background: 'var(--bg-surface)',
                                    border: '1px solid var(--border-primary)',
                                    fontFamily: 'var(--font-mono)',
                                    fontSize: '0.8rem',
                                }}
                                formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Close']}
                                labelFormatter={(t) => `SGT ${t}`}
                            />
                            <Area
                                type="monotone"
                                dataKey="close"
                                stroke="var(--chart-line)"
                                fill="url(#symbolGradient)"
                                strokeWidth={2}
                            />
                        </AreaChart>
                    ) : (
                        <AreaChart data={price_history}>
                            <defs>
                                <linearGradient id="symbolGradient" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="var(--chart-line)" stopOpacity={0.4} />
                                    <stop offset="95%" stopColor="var(--chart-line)" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                            <XAxis
                                dataKey="date"
                                tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                                tickFormatter={(d) => new Date(d + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                                interval="preserveStartEnd"
                                minTickGap={40}
                            />
                            <YAxis
                                domain={['auto', 'auto']}
                                tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                                tickFormatter={(v) => `$${v.toFixed(0)}`}
                            />
                            <Tooltip
                                contentStyle={{
                                    background: 'var(--bg-surface)',
                                    border: '1px solid var(--border-primary)',
                                    fontFamily: 'var(--font-mono)',
                                    fontSize: '0.8rem',
                                }}
                                formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Close']}
                                labelFormatter={(d) => new Date(d + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}
                            />
                            <Area
                                type="monotone"
                                dataKey="close"
                                stroke="var(--chart-line)"
                                fill="url(#symbolGradient)"
                                strokeWidth={2}
                            />
                        </AreaChart>
                    )}
                </ResponsiveContainer>
            </div>

            <div className="symbol-cards-grid">
                {/* Technical Indicators */}
                {indicators && (
                    <div className="symbol-card">
                        <h4>Technical Indicators</h4>
                        <div className="indicator-grid">
                            <div className="indicator-item">
                                <span className="indicator-label">RSI (14)</span>
                                <span className="indicator-value">{formatNum(indicators.rsi_14, 1)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">MACD</span>
                                <span className="indicator-value">{formatNum(indicators.macd, 3)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">MACD Signal</span>
                                <span className="indicator-value">{formatNum(indicators.macd_signal, 3)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">MACD Hist</span>
                                <span className="indicator-value">{formatNum(indicators.macd_hist, 3)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">ADX (14)</span>
                                <span className="indicator-value">{formatNum(indicators.adx_14, 1)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">ATR (14)</span>
                                <span className="indicator-value">{formatNum(indicators.atr_14)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">MFI (14)</span>
                                <span className="indicator-value">{formatNum(indicators.mfi_14, 1)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">CMF (20)</span>
                                <span className="indicator-value">{formatNum(indicators.cmf_20, 4)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Stoch %K</span>
                                <span className="indicator-value">{formatNum(indicators.stochastic_k, 1)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Stoch %D</span>
                                <span className="indicator-value">{formatNum(indicators.stochastic_d, 1)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">BB Width</span>
                                <span className="indicator-value">{formatNum(indicators.bb_width, 4)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Vol Z (20)</span>
                                <span className="indicator-value">{formatNum(indicators.volume_z_20)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Support</span>
                                <span className="indicator-value">${formatNum(indicators.support_level)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Resistance</span>
                                <span className="indicator-value">${formatNum(indicators.resistance_level)}</span>
                            </div>
                        </div>
                    </div>
                )}

                {/* Options Flow */}
                {options && (
                    <div className="symbol-card">
                        <h4>Options Flow</h4>
                        <div className="indicator-grid">
                            <div className="indicator-item">
                                <span className="indicator-label">P/C Vol Ratio</span>
                                <span className="indicator-value">{formatNum(options.put_call_ratio_volume)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">P/C OI Ratio</span>
                                <span className="indicator-value">{formatNum(options.put_call_ratio_oi)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">ATM IV</span>
                                <span className="indicator-value">{formatNum(options.atm_iv, 1)}%</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Max Pain</span>
                                <span className="indicator-value">${formatNum(options.max_pain_strike)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Top Call Strike</span>
                                <span className="indicator-value">${formatNum(options.highest_oi_call_strike)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Top Put Strike</span>
                                <span className="indicator-value">${formatNum(options.highest_oi_put_strike)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Call Volume</span>
                                <span className="indicator-value">{formatLargeNum(options.total_call_volume)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Put Volume</span>
                                <span className="indicator-value">{formatLargeNum(options.total_put_volume)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Call OI</span>
                                <span className="indicator-value">{formatLargeNum(options.total_call_oi)}</span>
                            </div>
                            <div className="indicator-item">
                                <span className="indicator-label">Put OI</span>
                                <span className="indicator-value">{formatLargeNum(options.total_put_oi)}</span>
                            </div>
                        </div>
                    </div>
                )}

                {/* Earnings */}
                {earnings.length > 0 && (
                    <div className="symbol-card">
                        <h4>Earnings</h4>
                        <div className="earnings-list">
                            {earnings.map((e, i) => {
                                const isPast = new Date(e.earnings_date) < new Date();
                                const beat = isPast && e.eps_actual != null && e.eps_est != null
                                    ? e.eps_actual > e.eps_est
                                    : null;
                                return (
                                    <div key={i} className="earnings-row">
                                        <div className="earnings-date-col">
                                            <span className="earnings-date">{e.earnings_date}</span>
                                            {e.time_of_day && <span className="earnings-time">{e.time_of_day}</span>}
                                        </div>
                                        <div className="earnings-data-col">
                                            <span>EPS Est: {formatNum(e.eps_est)}</span>
                                            {isPast && <span>EPS Act: {formatNum(e.eps_actual)}</span>}
                                            {e.revenue_actual != null && <span>Rev: {formatLargeNum(e.revenue_actual)}</span>}
                                        </div>
                                        {beat !== null && (
                                            <span className={`earnings-badge ${beat ? 'beat' : 'miss'}`}>
                                                {beat ? 'BEAT' : 'MISS'}
                                            </span>
                                        )}
                                        {!isPast && (
                                            <span className="earnings-badge upcoming">UPCOMING</span>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* Analyst Ratings */}
                {analyst_ratings.length > 0 && (
                    <div className="symbol-card">
                        <h4>Analyst Ratings</h4>
                        {fundamentals && fundamentals.analyst_consensus && (
                            <div className="analyst-summary">
                                <div className="consensus-header">
                                    <span className={`consensus-badge consensus-${fundamentals.analyst_consensus.toLowerCase()}`}>
                                        {fundamentals.analyst_consensus}
                                    </span>
                                    <span className="consensus-counts">
                                        {fundamentals.buy_count} Buy / {fundamentals.hold_count} Hold / {fundamentals.sell_count} Sell
                                    </span>
                                </div>
                                {fundamentals.avg_price_target != null && (
                                    <div className="indicator-item">
                                        <span className="indicator-label">Avg Price Target</span>
                                        <span className="indicator-value">
                                            ${formatNum(fundamentals.avg_price_target)}
                                            {fundamentals.upside_pct != null && (
                                                <span className={fundamentals.upside_pct >= 0 ? 'positive' : 'negative'}>
                                                    {' '}({formatPct(fundamentals.upside_pct)})
                                                </span>
                                            )}
                                        </span>
                                    </div>
                                )}
                            </div>
                        )}
                        <div className="ratings-list">
                            {analyst_ratings.map((r, i) => (
                                <div key={i} className="rating-row">
                                    <div className="rating-firm">{r.firm}</div>
                                    <div className="rating-grade">{r.to_grade || '--'}</div>
                                    {r.price_target && (
                                        <div className="rating-pt">${formatNum(r.price_target)}</div>
                                    )}
                                    <div className="rating-date">{timeAgo(r.rating_date)}</div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Recent News */}
                {news.length > 0 && (
                    <div className="symbol-card symbol-news-card">
                        <h4>Recent News</h4>
                        <div className="symbol-news-list">
                            {news.map((n) => (
                                <a
                                    key={n.doc_id}
                                    href={n.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="symbol-news-row"
                                >
                                    <span className="symbol-news-title">{n.title}</span>
                                    <span className="symbol-news-meta">
                                        {n.provider} · {timeAgo(n.published_at)}
                                    </span>
                                </a>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

export default SymbolDetail;
