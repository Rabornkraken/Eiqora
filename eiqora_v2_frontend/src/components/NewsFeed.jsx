import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { getNews, getNewsTickers } from '../services/api';

function NewsFeed() {
    const [articles, setArticles] = useState([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [symbolFilter, setSymbolFilter] = useState('');
    const [activeSymbol, setActiveSymbol] = useState('');
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState(null);

    // Autocomplete state
    const [allTickers, setAllTickers] = useState([]);
    const [showSuggestions, setShowSuggestions] = useState(false);
    const inputRef = useRef(null);
    const suggestionsRef = useRef(null);

    const PAGE_SIZE = 25;

    // Fetch ticker list for autocomplete on mount
    useEffect(() => {
        getNewsTickers()
            .then(setAllTickers)
            .catch(() => {});
    }, []);

    // Close suggestions on outside click
    useEffect(() => {
        const handleClickOutside = (e) => {
            if (
                suggestionsRef.current && !suggestionsRef.current.contains(e.target) &&
                inputRef.current && !inputRef.current.contains(e.target)
            ) {
                setShowSuggestions(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const suggestions = useMemo(() => {
        if (!symbolFilter) return allTickers.slice(0, 8);
        const q = symbolFilter.toUpperCase();
        return allTickers
            .filter(t => t.startsWith(q))
            .slice(0, 8);
    }, [symbolFilter, allTickers]);

    const fetchNews = useCallback(async (pageNum, append = false, symbol = activeSymbol) => {
        try {
            if (append) {
                setLoadingMore(true);
            } else {
                setLoading(true);
            }
            setError(null);

            const data = await getNews({
                symbol: symbol || null,
                page: pageNum,
                page_size: PAGE_SIZE,
            });

            if (append) {
                setArticles(prev => [...prev, ...(data.articles || [])]);
            } else {
                setArticles(data.articles || []);
            }
            setTotal(data.total || 0);
            setPage(pageNum);
        } catch (err) {
            console.error('Failed to fetch news:', err);
            setError('Failed to load news');
        } finally {
            setLoading(false);
            setLoadingMore(false);
        }
    }, [activeSymbol]);

    useEffect(() => {
        fetchNews(1, false, activeSymbol);
    }, [activeSymbol]);

    // Ticker chips from current articles
    const tickerCounts = useMemo(() => {
        const counts = {};
        articles.forEach(a => {
            counts[a.ticker] = (counts[a.ticker] || 0) + 1;
        });
        return Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 12);
    }, [articles]);

    // Group articles by date
    const grouped = useMemo(() => {
        const now = new Date();
        const todayStr = now.toISOString().slice(0, 10);
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        const yesterdayStr = yesterday.toISOString().slice(0, 10);

        const groups = { Today: [], Yesterday: [], Earlier: [] };
        articles.forEach(a => {
            const d = a.published_at ? a.published_at.slice(0, 10) : '';
            if (d === todayStr) groups.Today.push(a);
            else if (d === yesterdayStr) groups.Yesterday.push(a);
            else groups.Earlier.push(a);
        });
        return Object.entries(groups).filter(([, items]) => items.length > 0);
    }, [articles]);

    const handleChipClick = (ticker) => {
        if (activeSymbol === ticker) {
            setActiveSymbol('');
            setSymbolFilter('');
        } else {
            setActiveSymbol(ticker);
            setSymbolFilter(ticker);
        }
    };

    const handleSelectSuggestion = (ticker) => {
        setSymbolFilter(ticker);
        setActiveSymbol(ticker);
        setShowSuggestions(false);
    };

    const handleFilterSubmit = (e) => {
        e.preventDefault();
        setActiveSymbol(symbolFilter);
        setShowSuggestions(false);
    };

    const handleClearFilter = () => {
        setSymbolFilter('');
        setActiveSymbol('');
    };

    const handleLoadMore = () => {
        fetchNews(page + 1, true);
    };

    const timeAgo = (dateStr) => {
        if (!dateStr) return '';
        const now = new Date();
        const then = new Date(dateStr);
        const diffMs = now - then;
        const diffMins = Math.floor(diffMs / 60000);
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        const diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return `${diffHours}h ago`;
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 7) return `${diffDays}d ago`;
        return then.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    };

    const hasMore = articles.length < total;

    if (loading && articles.length === 0) {
        return (
            <div className="news-container">
                <div className="news-header"><h3>News</h3></div>
                <div className="loading-state">Loading news...</div>
            </div>
        );
    }

    if (error && articles.length === 0) {
        return (
            <div className="news-container">
                <div className="news-header"><h3>News</h3></div>
                <div className="error-state">{error}</div>
            </div>
        );
    }

    return (
        <div className="news-container">
            <div className="news-header">
                <h3>News</h3>
                <span className="news-meta">
                    {activeSymbol && <span className="news-filter-active">{activeSymbol}</span>}
                    {articles.length} of {total.toLocaleString()} articles
                </span>
            </div>

            {/* Filter Row with Autocomplete */}
            <div className="news-filter-row">
                <form className="news-filter" onSubmit={handleFilterSubmit}>
                    <div className="news-search-wrapper">
                        <input
                            ref={inputRef}
                            type="text"
                            placeholder="Search symbol..."
                            value={symbolFilter}
                            onChange={(e) => {
                                setSymbolFilter(e.target.value.toUpperCase());
                                setShowSuggestions(true);
                            }}
                            onFocus={() => setShowSuggestions(true)}
                            className="news-filter-input"
                            autoComplete="off"
                        />
                        {showSuggestions && suggestions.length > 0 && (
                            <div className="news-suggestions" ref={suggestionsRef}>
                                {suggestions.map(ticker => (
                                    <button
                                        key={ticker}
                                        type="button"
                                        className={`news-suggestion-item ${ticker === activeSymbol ? 'active' : ''}`}
                                        onClick={() => handleSelectSuggestion(ticker)}
                                    >
                                        <img
                                            src={`https://img.logokit.com/ticker/${ticker}?token=pk_fr4a8c50224be943a466c9`}
                                            alt=""
                                            className="news-suggestion-logo"
                                            onError={(e) => { e.target.style.display = 'none'; }}
                                        />
                                        <span>{ticker}</span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                    <button type="submit" className="news-filter-btn">Search</button>
                    {activeSymbol && (
                        <button type="button" className="news-clear-btn" onClick={handleClearFilter}>Clear</button>
                    )}
                </form>
            </div>

            {/* Ticker Chips */}
            {!activeSymbol && tickerCounts.length > 0 && (
                <div className="news-ticker-chips">
                    {tickerCounts.map(([ticker, count]) => (
                        <button
                            key={ticker}
                            className={`news-chip ${activeSymbol === ticker ? 'active' : ''}`}
                            onClick={() => handleChipClick(ticker)}
                        >
                            {ticker} <span className="news-chip-count">{count}</span>
                        </button>
                    ))}
                </div>
            )}

            {/* Articles grouped by date */}
            {articles.length === 0 ? (
                <div className="empty-state">No news articles found</div>
            ) : (
                <div className="news-list">
                    {grouped.map(([label, items]) => (
                        <div key={label} className="news-date-group">
                            <div className="news-date-label">{label}</div>
                            {items.map((article) => (
                                <a
                                    key={article.doc_id}
                                    href={article.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="news-card"
                                >
                                    <div className="news-card-body">
                                        <div className="news-card-top">
                                            <img
                                                src={`https://img.logokit.com/ticker/${article.ticker}?token=pk_fr4a8c50224be943a466c9`}
                                                alt={article.ticker}
                                                className="news-article-logo"
                                                onError={(e) => { e.target.style.display = 'none'; }}
                                            />
                                            <div className="news-card-text">
                                                <span className="news-title">{article.title}</span>
                                                <div className="news-card-bottom">
                                                    <span className="news-ticker-badge">{article.ticker}</span>
                                                    <span className="news-dot">·</span>
                                                    <span className="news-provider">{article.provider}</span>
                                                    <span className="news-dot">·</span>
                                                    <span className="news-time">{timeAgo(article.published_at)}</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </a>
                            ))}
                        </div>
                    ))}
                </div>
            )}

            {hasMore && (
                <button
                    className="news-load-more"
                    onClick={handleLoadMore}
                    disabled={loadingMore}
                >
                    {loadingMore ? 'Loading...' : `Load More (${total - articles.length} remaining)`}
                </button>
            )}
        </div>
    );
}

export default NewsFeed;
