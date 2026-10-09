import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchNightTimeline, fetchQualityNights } from '../../api/client';
import { AlertTriangle, ArrowRight } from 'lucide-react';
import QualityValue from './QualityValue';
import './Quality.css';

// Q1d Dashboard tile: the most recent observing night's star quality at a glance.
// Never breaks the Dashboard: on any error, or with no nights yet, it renders nothing.

function formatNight(night) {
    return new Date(`${night}T12:00:00Z`).toLocaleDateString(undefined, {
        weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC',
    });
}

export default function LastNightTile() {
    const nightsQuery = useQuery({
        queryKey: ['dashboardLastNight'],
        queryFn: () => fetchQualityNights({ limit: 1 }),
        staleTime: 10 * 60 * 1000,
        retry: false,
    });
    const last = nightsQuery.data?.nights?.[0];
    const timelineQuery = useQuery({
        queryKey: ['dashboardLastNightTimeline', last?.night],
        queryFn: () => fetchNightTimeline(last.night),
        enabled: !!last?.night,
        staleTime: 10 * 60 * 1000,
        retry: false,
    });

    if (!last || nightsQuery.isError) return null;
    const tl = timelineQuery.data;
    const suspect = tl ? tl.points.filter((p) => p.flag).length : null;
    const drifting = (tl?.summary || [])
        .filter((s) => s.drift_arcsec_per_hour != null && s.median_fwhm_arcsec)
        .map((s) => ({ ...s, rel: s.drift_arcsec_per_hour / s.median_fwhm_arcsec }))
        .sort((a, b) => b.rel - a.rel)[0];

    return (
        <div className="dashboard-card last-night-tile">
            <div className="card-header">
                <h3>Last night</h3>
                <span className="text-muted text-sm">{formatNight(last.night)}</span>
            </div>
            <div className="last-night-body">
                <div className="last-night-stats">
                    <div><strong>{last.subs}</strong><span>subs</span></div>
                    <div><strong>{last.measured}</strong><span>measured</span></div>
                    {suspect != null && (
                        <div className={suspect ? 'warn' : ''}><strong>{suspect}</strong><span>suspect</span></div>
                    )}
                </div>
                {last.rigs.filter((r) => r.measured > 0).slice(0, 3).map((r) => (
                    <div key={String(r.rig_id)} className="last-night-rig">
                        <span className="text-muted">{r.rig_name || 'Unassigned rig'}</span>
                        <span>
                            median <QualityValue px={r.median_fwhm_px} arcsec={r.median_fwhm_arcsec} />
                        </span>
                    </div>
                ))}
                {last.measured === 0 && <p className="text-muted text-sm">Not measured yet: star quality appears once these subs are processed.</p>}
                {drifting && drifting.rel >= 0.1 && (
                    <p className="text-sm last-night-warn">
                        <AlertTriangle size={14} style={{ verticalAlign: '-2px' }} /> {drifting.filter && drifting.filter !== 'None' ? `${drifting.filter} ` : ''}FWHM grew {drifting.drift_arcsec_per_hour.toFixed(2)}″ per hour: focus drift?
                    </p>
                )}
                <Link to={`/nights/${last.night}`} className="link text-sm">Open night report <ArrowRight size={14} style={{ verticalAlign: '-2px' }} /></Link>
            </div>
        </div>
    );
}
