import React, { useState, useEffect } from 'react';
import { getDashboardStats } from '../services/api';

function Header() {
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [theme, setTheme] = useState(localStorage.getItem('theme') || 'light');

    useEffect(() => {
        const fetchStats = async () => {
            try {
                const data = await getDashboardStats();
                setStats(data);
            } catch (error) {
                console.error('Failed to fetch stats:', error);
            } finally {
                setLoading(false);
            }
        };

        fetchStats();
        // Refresh stats every 30 seconds
        const interval = setInterval(fetchStats, 30000);
        return () => clearInterval(interval);
    }, []);

    useEffect(() => {
        // Apply theme to document
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
    }, [theme]);

    const toggleTheme = () => {
        setTheme(prevTheme => prevTheme === 'light' ? 'dark' : 'light');
    };

    return (
        <header className="header">
            <div style={{ width: '100%', padding: '0 32px' }}>
                <div className="header-content flex items-center">
                    <div className="logo" style={{ marginRight: '120px' }}>EIQORA</div>

                    {!loading && stats && (
                        <div className="stats-grid">
                            <div className="stat-item">
                                <div className="stat-label">Active Positions</div>
                                <div className="stat-value">{stats.active_positions || 0}</div>
                            </div>

                            <div className="stat-item">
                                <div className="stat-label">Unrealized P&L</div>
                                <div className="stat-value" style={{ color: stats.unrealized_pnl >= 0 ? 'var(--color-go)' : 'var(--color-no-go)' }}>
                                    {stats.unrealized_pnl !== undefined ? `$${stats.unrealized_pnl.toFixed(2)}` : 'N/A'}
                                </div>
                            </div>

                            <div className="stat-item">
                                <div className="stat-label">Win Rate</div>
                                <div className="stat-value status-go">{stats.win_rate || 'N/A'}</div>
                            </div>

                            <div className="stat-item">
                                <div className="stat-label">Total Return</div>
                                <div className={`stat-value ${stats.total_return && stats.total_return.startsWith('-') ? 'status-no-go' : 'status-go'}`}>
                                    {stats.total_return || 'N/A'}
                                </div>
                            </div>

                            <div className="stat-item">
                                <div className="stat-label">Closed Trades</div>
                                <div className="stat-value">{stats.total_trades || 0}</div>
                            </div>
                        </div>
                    )}

                    <button className="theme-toggle" onClick={toggleTheme} title="Toggle theme">
                        {theme === 'light' ? '🌙' : '☀️'}
                    </button>
                </div>
            </div>
        </header>
    );
}

export default Header;
