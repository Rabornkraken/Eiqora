import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import EquityChart from './components/EquityChart';
import DecisionsTable from './components/DecisionsTable';
import Portfolio from './components/Portfolio';
import TradingHistory from './components/TradingHistory';
import Watchlist from './components/Watchlist';

function App() {
  // Initialize state from URL query param or default to 'equity'
  const getInitialTab = () => {
    const params = new URLSearchParams(window.location.search);
    return params.get('view') || 'equity';
  };

  const [activeTab, setActiveTabState] = useState(getInitialTab);

  // Handle URL updates and conflicting state
  const setActiveTab = (tab) => {
    setActiveTabState(tab);
    const url = new URL(window.location);
    url.searchParams.set('view', tab);
    window.history.pushState({}, '', url);
  };

  // Listen for browser back/forward navigation
  useEffect(() => {
    const handlePopState = () => {
      setActiveTabState(getInitialTab());
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
          </ul>
        </div>
      </nav>

      <main className="container">
        {activeTab === 'equity' && (
          <div className="tab-content">
            <div className="chart-section">
              <EquityChart />
            </div>

            {/* Side-by-side Watchlist and Decisions */}
            <div className="two-column-grid">
              <div className="column-left">
                <Watchlist />
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

        {activeTab === 'history' && (
          <div className="tab-content">
            <TradingHistory />
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
