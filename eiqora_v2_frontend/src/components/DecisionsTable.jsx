import React, { useState, useEffect, useMemo } from 'react';
import { getDecisions } from '../services/api';
import DecisionDetails from './DecisionDetails';

function DecisionsTable() {
    const [decisions, setDecisions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selectedDecision, setSelectedDecision] = useState(null);
    const [filterSymbol, setFilterSymbol] = useState('');
    const [filterDecision, setFilterDecision] = useState('');

    // Date range: default 7 days ago to today
    const today = new Date().toISOString().split('T')[0];
    const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().split('T')[0];
    const [startDate, setStartDate] = useState(sevenDaysAgo);
    const [endDate, setEndDate] = useState(today);

    useEffect(() => {
        const fetchDecisions = async () => {
            try {
                const params = { limit: 500 };
                if (startDate) params.start_date = startDate;
                if (endDate) params.end_date = endDate;
                const data = await getDecisions(params);
                setDecisions(data);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch decisions:', err);
                setError('Failed to load decisions');
            } finally {
                setLoading(false);
            }
        };

        fetchDecisions();
        const interval = setInterval(fetchDecisions, 30000);
        return () => clearInterval(interval);
    }, [startDate, endDate]);

    const filteredDecisions = useMemo(() => {
        return decisions.filter(d => {
            if (filterSymbol && !d.symbol?.toUpperCase().includes(filterSymbol.toUpperCase())) return false;
            if (filterDecision) {
                const fd = d.final_decision || d.result?.decision || '';
                if (fd.toUpperCase() !== filterDecision) return false;
            }
            return true;
        });
    }, [decisions, filterSymbol, filterDecision]);

    const formatDate = (dateString) => {
        const date = new Date(dateString);
        return date.toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    };

    const truncateText = (text, maxLength = 60) => {
        if (!text) return 'N/A';
        return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
    };

    if (loading) {
        return (
            <div className="table-container">
                <div className="loading">Loading decisions...</div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="error">{error}</div>
        );
    }

    if (decisions.length === 0) {
        return (
            <>
                <div className="decisions-filters">
                    <input
                        type="date"
                        value={startDate}
                        onChange={(e) => setStartDate(e.target.value)}
                        className="decisions-filter-date"
                    />
                    <span className="decisions-date-separator">to</span>
                    <input
                        type="date"
                        value={endDate}
                        onChange={(e) => setEndDate(e.target.value)}
                        className="decisions-filter-date"
                    />
                </div>
                <div className="table-container">
                    <div className="loading">No decisions in selected range</div>
                </div>
            </>
        );
    }

    return (
        <>
            {/* Filters */}
            <div className="decisions-filters">
                <input
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="decisions-filter-date"
                />
                <span className="decisions-date-separator">to</span>
                <input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="decisions-filter-date"
                />
                <input
                    type="text"
                    placeholder="Filter symbol..."
                    value={filterSymbol}
                    onChange={(e) => setFilterSymbol(e.target.value.toUpperCase())}
                    className="decisions-filter-input"
                />
                <select
                    value={filterDecision}
                    onChange={(e) => setFilterDecision(e.target.value)}
                    className="decisions-filter-select"
                >
                    <option value="">All Decisions</option>
                    <option value="GO">GO</option>
                    <option value="NO_GO">NO_GO</option>
                </select>
                <span className="decisions-filter-count">
                    {filteredDecisions.length} of {decisions.length}
                </span>
            </div>

            <div className="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Date/Time</th>
                            <th>Symbol</th>
                            <th>Trigger</th>
                            <th>Decision</th>
                            <th>Reason</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        {filteredDecisions.map((decision) => {
                            const finalDecision = decision.final_decision || decision.result?.decision || 'N/A';
                            const decisionReason = decision.decision_reason || decision.result?.reasoning || '';
                            const analysisTime = decision.analysis_time || decision.created_at;

                            return (
                                <tr
                                    key={decision.analysis_id}
                                    onClick={() => setSelectedDecision(decision)}
                                >
                                    <td className="text-xs">{formatDate(analysisTime)}</td>
                                    <td>
                                        <div className="symbol-with-logo">
                                            <img
                                                src={`https://img.logokit.com/ticker/${decision.symbol}?token=pk_fr4a8c50224be943a466c9`}
                                                alt=""
                                                className="company-logo-small"
                                                onError={(e) => { e.target.style.display = 'none'; }}
                                            />
                                            <span className="font-bold">{decision.symbol}</span>
                                        </div>
                                    </td>
                                    <td className="text-sm">{decision.trigger_type || 'N/A'}</td>
                                    <td>
                                        <span className={finalDecision === 'GO' ? 'status-go' : 'status-no-go'}>
                                            {finalDecision}
                                        </span>
                                    </td>
                                    <td className="text-sm text-secondary">
                                        {truncateText(decisionReason)}
                                    </td>
                                    <td className="text-xs text-muted">View →</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            {selectedDecision && (
                <DecisionDetails
                    decision={selectedDecision}
                    onClose={() => setSelectedDecision(null)}
                />
            )}
        </>
    );
}

export default DecisionsTable;
