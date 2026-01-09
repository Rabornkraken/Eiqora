import React, { useState, useEffect } from 'react';
import { getDecisions } from '../services/api';
import DecisionDetails from './DecisionDetails';

function DecisionsTable() {
    const [decisions, setDecisions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selectedDecision, setSelectedDecision] = useState(null);

    useEffect(() => {
        const fetchDecisions = async () => {
            try {
                const data = await getDecisions({ limit: 100 });
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
        // Refresh every 30 seconds
        const interval = setInterval(fetchDecisions, 30000);
        return () => clearInterval(interval);
    }, []);

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
            <div className="table-container">
                <div className="loading">No decisions recorded yet</div>
            </div>
        );
    }

    return (
        <>
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
                        {decisions.map((decision) => (
                            <tr
                                key={decision.analysis_id}
                                onClick={() => setSelectedDecision(decision)}
                            >
                                <td className="text-xs">{formatDate(decision.analysis_time)}</td>
                                <td className="font-bold">{decision.symbol}</td>
                                <td className="text-sm">{decision.trigger_type || 'N/A'}</td>
                                <td>
                                    <span className={decision.final_decision === 'GO' ? 'status-go' : 'status-no-go'}>
                                        {decision.final_decision}
                                    </span>
                                </td>
                                <td className="text-sm text-secondary">
                                    {truncateText(decision.decision_reason)}
                                </td>
                                <td className="text-xs text-muted">View →</td>
                            </tr>
                        ))}
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
