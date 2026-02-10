import React, { useState, useEffect } from 'react';
import { getDecisionDetails } from '../services/api';

function DecisionDetails({ decision, onClose }) {
    const [details, setDetails] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchDetails = async () => {
            try {
                const data = await getDecisionDetails(decision.analysis_id);
                setDetails(data);
            } catch (error) {
                console.error('Failed to fetch decision details:', error);
            } finally {
                setLoading(false);
            }
        };

        fetchDetails();
    }, [decision.analysis_id]);

    const renderAgentOutput = (label, output) => {
        if (!output) return null;

        // Handle different output formats
        let displayContent;
        if (typeof output === 'string') {
            displayContent = output;
        } else if (typeof output === 'object') {
            displayContent = JSON.stringify(output, null, 2);
        } else {
            return null;
        }

        return (
            <div className="agent-section">
                <h4>{label}</h4>
                <pre>{displayContent}</pre>
            </div>
        );
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <div className="text-xl font-bold">{decision.symbol}</div>
                        <div className="text-sm text-muted">
                            {new Date(decision.created_at || decision.analysis_time).toLocaleString()}
                        </div>
                    </div>
                    <button className="modal-close" onClick={onClose}>✕</button>
                </div>

                <div className="modal-body">
                    {loading ? (
                        <div className="loading">Loading details...</div>
                    ) : details ? (
                        <>
                            <div className="agent-section">
                                <h4>Final Decision</h4>
                                <div className={`text-xl ${details.final_decision === 'GO' ? 'status-go' : 'status-no-go'}`}>
                                    {details.final_decision}
                                </div>
                                <pre className="text-sm">{details.decision_reason}</pre>
                            </div>

                            {renderAgentOutput('Top-Down Analysis', details.topdown_output)}
                            {renderAgentOutput('Context Analysis', details.context_output)}
                            {renderAgentOutput('Chart Analysis', details.chart_output)}
                            {renderAgentOutput('Idea Generator', details.idea_generator_output)}
                            {renderAgentOutput('Exit Policy', details.exit_policy_output)}
                            {renderAgentOutput('Red Team Review', details.red_team_output)}
                            {renderAgentOutput('Decision Agent', details.decision_output)}
                            {renderAgentOutput('Position Manager', details.position_manager_output)}
                            {renderAgentOutput('Risk Model', details.risk_model_output)}

                            {details.trigger_detail && (
                                <div className="agent-section">
                                    <h4>Trigger Details</h4>
                                    <pre>{JSON.stringify(details.trigger_detail, null, 2)}</pre>
                                </div>
                            )}
                        </>
                    ) : (
                        <div className="error">Failed to load decision details</div>
                    )}
                </div>
            </div>
        </div>
    );
}

export default DecisionDetails;
