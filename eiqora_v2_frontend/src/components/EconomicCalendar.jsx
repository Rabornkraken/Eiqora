import React, { useState, useEffect } from 'react';
import { getEconomicCalendar } from '../services/api';

function EconomicCalendar() {
    const [events, setEvents] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        fetchCalendar();
    }, []);

    const fetchCalendar = async () => {
        try {
            const data = await getEconomicCalendar(14);
            setEvents(data.events || []);
            setLoading(false);
        } catch (err) {
            console.error('Failed to fetch economic calendar:', err);
            setError('Failed to load economic calendar');
            setLoading(false);
        }
    };

    const impactColor = (impact) => {
        if (!impact) return 'var(--text-muted)';
        switch (impact.toUpperCase()) {
            case 'HIGH': return 'var(--color-no-go)';
            case 'MEDIUM': return '#F59E0B';
            case 'LOW': return 'var(--text-muted)';
            default: return 'var(--text-muted)';
        }
    };

    // Convert ET date+time to a local JS Date (handles EST/EDT automatically)
    const etToLocalDate = (dateStr, timeStr) => {
        if (!timeStr) return null;
        const inputHour = parseInt(timeStr.split(':')[0], 10);
        // Try EST (-05:00) first
        const d = new Date(`${dateStr}T${timeStr}-05:00`);
        const etHour = parseInt(d.toLocaleString('en-US', {
            timeZone: 'America/New_York', hour: 'numeric', hour12: false
        }), 10);
        if (etHour === inputHour) return d;
        // Otherwise EDT (-04:00)
        return new Date(`${dateStr}T${timeStr}-04:00`);
    };

    const formatTime = (dateStr, timeStr) => {
        if (!timeStr) return 'All Day';
        const d = etToLocalDate(dateStr, timeStr);
        if (!d) return 'All Day';
        return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
    };

    // Get user's timezone abbreviation
    const tzAbbr = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
        .formatToParts(new Date())
        .find(p => p.type === 'timeZoneName')?.value || 'Local';

    // Convert events to local dates for grouping
    const eventsWithLocal = events.map(evt => {
        const localDate = evt.event_time
            ? etToLocalDate(evt.event_date, evt.event_time)
            : null;
        const localDateKey = localDate
            ? localDate.toLocaleDateString('en-CA') // YYYY-MM-DD
            : evt.event_date;
        return { ...evt, localDateKey };
    });

    // Group events by local date
    const grouped = eventsWithLocal.reduce((acc, evt) => {
        const key = evt.localDateKey;
        if (!acc[key]) acc[key] = [];
        acc[key].push(evt);
        return acc;
    }, {});

    const formatDate = (dateStr) => {
        const d = new Date(dateStr + 'T00:00:00');
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const tomorrow = new Date(today);
        tomorrow.setDate(tomorrow.getDate() + 1);

        if (d.getTime() === today.getTime()) return 'Today';
        if (d.getTime() === tomorrow.getTime()) return 'Tomorrow';
        return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    };

    if (loading) {
        return (
            <div className="calendar-container">
                <div className="calendar-header">
                    <h3>Economic Calendar</h3>
                </div>
                <div className="loading-state">Loading calendar...</div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="calendar-container">
                <div className="calendar-header">
                    <h3>Economic Calendar</h3>
                </div>
                <div className="error-state">{error}</div>
            </div>
        );
    }

    return (
        <div className="calendar-container">
            <div className="calendar-header">
                <h3>Economic Calendar</h3>
                <span className="calendar-meta">{events.length} events · Next 14 days · Times in {tzAbbr}</span>
            </div>

            {events.length === 0 ? (
                <div className="empty-state">No upcoming economic events</div>
            ) : (
                <div className="calendar-body">
                    {Object.entries(grouped).map(([dateKey, dayEvents]) => (
                        <div key={dateKey} className="calendar-day-group">
                            <div className="calendar-day-label">{formatDate(dateKey)}</div>
                            {dayEvents.map((evt, i) => (
                                <div key={i} className="calendar-event-row">
                                    <span
                                        className="impact-dot"
                                        style={{ backgroundColor: impactColor(evt.impact) }}
                                        title={evt.impact || 'Unknown'}
                                    />
                                    <span className="event-time">{formatTime(evt.event_date, evt.event_time)}</span>
                                    <span className="event-name">{evt.event_name}</span>
                                    <span className="event-country">{evt.country}</span>
                                    <span className="event-values">
                                        {evt.actual != null && <span className="event-actual">A: {evt.actual}</span>}
                                        {evt.forecast != null && <span className="event-forecast">F: {evt.forecast}</span>}
                                        {evt.previous != null && <span className="event-previous">P: {evt.previous}</span>}
                                    </span>
                                </div>
                            ))}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

export default EconomicCalendar;
