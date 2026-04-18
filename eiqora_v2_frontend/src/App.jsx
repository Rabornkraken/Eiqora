import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import EquityChart from './components/EquityChart';
import DecisionsTable from './components/DecisionsTable';
import Portfolio from './components/Portfolio';
import TradingHistory from './components/TradingHistory';
import TickersList from './components/TickersList';
import Watchlist from './components/Watchlist';
import EconomicCalendar from './components/EconomicCalendar';
import NewsFeed from './components/NewsFeed';
import SymbolDetail from './components/SymbolDetail';

function App() {
  // Initialize state from URL query params
  const getInitialState = () => {
    const params = new URLSearchParams(window.location.search);
    const view = params.get('view') || 'equity';
    const symbol = params.get('symbol') || null;
    return { view, symbol };
  };

  const [activeTab, setActiveTabState] = useState(() => getInitialState().view);
  const [selectedSymbol, setSelectedSymbol] = useState(() => getInitialState().symbol);

  // Handle URL updates
  const setActiveTab = (tab, symbol = null) => {
    setActiveTabState(tab);
    setSelectedSymbol(symbol);
    const url = new URL(window.location);
    url.searchParams.set('view', tab);
    if (symbol) {
      url.searchParams.set('symbol', symbol);
    } else {
      url.searchParams.delete('symbol');
    }
    window.history.pushState({}, '', url);
  };

  // Handle symbol click from watchlist
  const handleSymbolClick = (symbol) => {
    setActiveTab('symbol', symbol);
  };

  // Handle back from symbol detail
  const handleBackFromSymbol = () => {
    setActiveTab('equity');
  };

  // Listen for browser back/forward navigation
  useEffect(() => {
    const handlePopState = () => {
      const { view, symbol } = getInitialState();
      setActiveTabState(view);
      setSelectedSymbol(symbol);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  return (
    <div className="app">
      <Header />

      <nav className="tabs">
        <div className="container">
          <ul className="tabs-list">
            <li>
              <button
                className={`tab-button ${activeTab === 'equity' ? 'active' : ''}`}
                onClick={() => setActiveTab('equity')}
              >
                Equity Performance
              </button>
            </li>
            <li>
              <button
                className={`tab-button ${activeTab === 'portfolio' ? 'active' : ''}`}
                onClick={() => setActiveTab('portfolio')}
              >
                Portfolio
              </button>
            </li>
            <li>
              <button
                className={`tab-button ${activeTab === 'history' ? 'active' : ''}`}
                onClick={() => setActiveTab('history')}
              >
                Trading History
              </button>
            </li>
            <li>
              <button
                className={`tab-button ${activeTab === 'legacy-history' ? 'active' : ''}`}
                onClick={() => setActiveTab('legacy-history')}
              >
                Legacy History
              </button>
            </li>
            <li>
              <button
                className={`tab-button ${activeTab === 'tickers' ? 'active' : ''}`}
                onClick={() => setActiveTab('tickers')}
              >
                Tickers
              </button>
            </li>
            <li className="tab-spacer"></li>
            <li>
              <button
                className={`tab-button ${activeTab === 'news' ? 'active' : ''}`}
                onClick={() => setActiveTab('news')}
              >
                News
              </button>
            </li>
          </ul>
        </div>
      </nav>

      <main className="container">
        {activeTab === 'symbol' && selectedSymbol && (
          <div className="tab-content">
            <SymbolDetail symbol={selectedSymbol} onBack={handleBackFromSymbol} />
          </div>
        )}

        {activeTab === 'equity' && (
          <div className="tab-content">
            <div className="chart-section">
              <EquityChart />
            </div>

            <EconomicCalendar />

            {/* Side-by-side Watchlist and Decisions */}
            <div className="two-column-grid">
              <div className="column-left">
                <Watchlist onSymbolClick={handleSymbolClick} />
              </div>
              <div className="column-right">
                <div className="decisions-section">
                  <h3>Trading Decisions</h3>
                  <DecisionsTable />
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'portfolio' && (
          <div className="tab-content">
            <Portfolio />
          </div>
        )}

        {activeTab === 'news' && (
          <div className="tab-content">
            <NewsFeed />
          </div>
        )}

        {activeTab === 'history' && (
          <div className="tab-content">
            <TradingHistory />
          </div>
        )}

        {activeTab === 'legacy-history' && (
          <div className="tab-content">
            <TradingHistory legacy />
          </div>
        )}

        {activeTab === 'tickers' && (
          <div className="tab-content">
            <TickersList onSymbolClick={handleSymbolClick} />
          </div>
        )}

      </main>
    </div>
  );
}

export default App;
