import { AlertTriangle } from 'lucide-react';
import { useQualityUnits } from '../../context/QualityUnitsContext';
import { filterColor } from '../../utils/filterColors';
import './Quality.css';

// Q1c: per-filter summary of a night on one rig (median / best / worst FWHM, drift).

function fmt(summary, key, units) {
    const arc = summary[`${key}_arcsec`];
    const px = summary[`${key}_px`];
    if (units === 'ARCSEC' && arc != null) return `${arc.toFixed(2)}″`;
    return px != null ? `${px.toFixed(2)} px` : '—';
}

function drift(summary, units) {
    const arc = summary.drift_arcsec_per_hour;
    const px = summary.drift_px_per_hour;
    const v = units === 'ARCSEC' && arc != null ? arc : px;
    if (v == null) return null;
    const u = units === 'ARCSEC' && arc != null ? '″' : ' px';
    const sign = v > 0 ? '+' : '';
    return `${sign}${v.toFixed(2)}${u}/h`;
}

export default function SessionSummary({ summary, rigId }) {
    const { units } = useQualityUnits();
    const rows = (summary || []).filter((s) => rigId === 'ALL' || s.rig_id === rigId);
    if (!rows.length) return null;
    return (
        <div className="session-summary">
            {rows.map((s) => {
                const d = drift(s, units);
                return (
                    <div key={`${s.rig_id}-${s.filter}`} className="session-summary-card">
                        <div className="session-summary-title">
                            <span className="filter-swatch" style={{ backgroundColor: filterColor(s.filter), display: 'inline-block', width: 10, height: 10, borderRadius: 2 }} />
                            {s.filter}
                            <span className="text-muted" style={{ fontWeight: 400, marginLeft: 'auto' }}>
                                {s.measured}/{s.subs} measured
                            </span>
                        </div>
                        {s.measured > 0 ? (
                            <>
                                <div>Median <strong>{fmt(s, 'median_fwhm', units)}</strong></div>
                                <div>Best {fmt(s, 'best_fwhm', units)} · worst {fmt(s, 'worst_fwhm', units)}</div>
                                <div title="Theil–Sen slope of FWHM over the run: steady growth usually means focus drift">
                                    Drift {d ?? <span className="text-muted">run too short</span>}
                                    {s.flagged > 0 && <> · <span style={{ color: 'var(--color-warning)' }}><AlertTriangle size={12} style={{ verticalAlign: '-2px' }} /> {s.flagged} suspect</span></>}
                                </div>
                            </>
                        ) : <div className="text-muted">Not measured yet</div>}
                    </div>
                );
            })}
        </div>
    );
}
