import { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
    ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { Pin, PinOff } from 'lucide-react';
import {
    fetchTarget, updateTargetGoals, formatHours, API_BASE_URL,
    fetchTargetRecommendation, postRecommendationFeedback,
} from '../api/client';
import ImageCard from '../components/images/ImageCard';
import QualityValue from '../components/quality/QualityValue';
import { useQualityUnits } from '../context/QualityUnitsContext';
import './TargetDetail.css';

// R2a §7: "Pin for Tonight" toggle. Binding contract: docs/design/R2a-feedback-dashboard.md §6-§7.
// The backend (feat/r2a-feedback-backend) is built in parallel and does not exist on this branch.
// Initial state comes from GET /api/recommendations/target/{key} (`feedback.pinned`); a 404 (the
// key isn't in the candidate pool) hides the toggle quietly rather than showing an error.

const FILTER_COLORS = {
    L: '#d0d4dc', R: '#e05050', G: '#50c070', B: '#5080e0',
    Ha: '#c8283c', OIII: '#2f80ed', SII: '#a83246', Hb: '#3cc8ff',
    Duo: '#b060c0', None: '#a0a0a0', Other: '#707070',
};

function filterColor(name) {
    if (FILTER_COLORS[name]) return FILTER_COLORS[name];
    if (name && name.startsWith('Other:')) return FILTER_COLORS.Other;
    return FILTER_COLORS.Other;
}

export default function TargetDetail() {
    const { targetKey } = useParams();
    const [target, setTarget] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [goalDrafts, setGoalDrafts] = useState({});
    const [savingGoals, setSavingGoals] = useState(false);
    const [goalMessage, setGoalMessage] = useState('');
    const [inPool, setInPool] = useState(false);
    const [pinned, setPinned] = useState(false);
    const [pinBusy, setPinBusy] = useState(false);
    const { units } = useQualityUnits();

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [targetKey]);

    useEffect(() => {
        let cancelled = false;
        async function loadPinState() {
            try {
                const rec = await fetchTargetRecommendation(targetKey);
                if (cancelled) return;
                setInPool(true);
                setPinned(!!rec?.feedback?.pinned);
            } catch {
                // Not in the candidate pool (404) or the request otherwise failed: hide the
                // toggle quietly rather than surface an error on an otherwise working page.
                if (!cancelled) setInPool(false);
            }
        }
        loadPinState();
        return () => { cancelled = true; };
    }, [targetKey]);

    async function handleTogglePin() {
        const nextPinned = !pinned;
        setPinned(nextPinned);
        setPinBusy(true);
        try {
            await postRecommendationFeedback({
                targetKey,
                action: nextPinned ? 'PIN' : 'UNPIN',
                context: {},
            });
        } catch (e) {
            setPinned(!nextPinned);
            console.error('Failed to update pin state:', e);
        } finally {
            setPinBusy(false);
        }
    }

    async function load() {
        setLoading(true);
        setError(null);
        try {
            const data = await fetchTarget(targetKey);
            setTarget(data);

            const drafts = {};
            (data.filters || []).forEach((f) => {
                const existingGoal = (data.goals || []).find((g) => g.filter_group === f.filter);
                drafts[f.filter] = existingGoal ? (existingGoal.goal_seconds / 3600).toString() : '';
            });
            setGoalDrafts(drafts);
        } catch (e) {
            console.error('Failed to load target:', e);
            setError('Target not found');
        } finally {
            setLoading(false);
        }
    }

    async function handleSaveGoals() {
        setSavingGoals(true);
        setGoalMessage('');
        try {
            const goals = Object.entries(goalDrafts)
                .filter(([, hours]) => hours !== '' && !isNaN(parseFloat(hours)))
                .map(([filter_group, hours]) => ({
                    filter_group,
                    goal_seconds: parseFloat(hours) * 3600,
                }));
            await updateTargetGoals(targetKey, goals);
            setGoalMessage('Goals saved.');
            load();
        } catch (e) {
            setGoalMessage(`Failed to save goals: ${e.message}`);
        } finally {
            setSavingGoals(false);
        }
    }

    const nightsChartData = useMemo(() => {
        if (!target?.nights_detail) return [];
        return target.nights_detail.map((n) => {
            const q = n.quality;
            // Q1b: median FWHM of the night, in the viewer's units (px when no scale is known).
            const fwhm = q ? (units === 'ARCSEC' && q.median_fwhm_arcsec != null ? q.median_fwhm_arcsec : q.median_fwhm_px) : null;
            return { night: n.night, ...n.filters, fwhm };
        });
    }, [target, units]);

    const hasNightQuality = useMemo(
        () => nightsChartData.some((n) => n.fwhm != null),
        [nightsChartData],
    );
    const fwhmUnit = units === 'ARCSEC' && target?.nights_detail?.some((n) => n.quality?.median_fwhm_arcsec != null) ? '″' : ' px';

    const filterKeysForChart = useMemo(() => {
        if (!target?.filters) return [];
        return target.filters.map((f) => f.filter);
    }, [target]);

    if (loading) {
        return (
            <div className="loading-state">
                <div className="spinner" />
                <p>Loading target...</p>
            </div>
        );
    }

    if (error || !target) {
        return (
            <div className="empty-state">
                <div className="empty-state-icon">&#128561;</div>
                <h3 className="empty-state-title">{error || 'Target not found'}</h3>
                <Link to="/targets" className="btn btn-secondary">Back to Targets</Link>
            </div>
        );
    }

    return (
        <div className="target-detail-page">
            <nav className="breadcrumb">
                <Link to="/targets">Targets</Link>
                <span>/</span>
                <span>{target.display_name}</span>
            </nav>

            {/* Hero */}
            <div className="target-hero">
                <div className="target-hero-image">
                    {target.cover_image_id ? (
                        <img src={`${API_BASE_URL}/images/${target.cover_image_id}/thumbnail`} alt={target.display_name} />
                    ) : (
                        <div className="target-hero-placeholder">&#127765;</div>
                    )}
                </div>
                <div className="target-hero-info">
                    <h1 className="target-hero-title">{target.display_name}</h1>
                    {target.catalog && (
                        <div className="target-hero-facts">
                            {target.catalog.object_type && <span>{target.catalog.object_type}</span>}
                            {target.catalog.constellation && <span>{target.catalog.constellation}</span>}
                            {target.catalog.apparent_magnitude != null && <span>Mag {target.catalog.apparent_magnitude.toFixed(1)}</span>}
                        </div>
                    )}
                    <div className="target-hero-stats">
                        <div className="hero-stat">
                            <span className="hero-stat-value">{formatHours(target.total_seconds)}</span>
                            <span className="hero-stat-label">Total Integration</span>
                        </div>
                        <div className="hero-stat">
                            <span className="hero-stat-value">{target.total_subs}</span>
                            <span className="hero-stat-label">Subs</span>
                        </div>
                        <div className="hero-stat">
                            <span className="hero-stat-value">{target.nights}</span>
                            <span className="hero-stat-label">Nights</span>
                        </div>
                        <div className="hero-stat">
                            <span className="hero-stat-value">{target.master_count}</span>
                            <span className="hero-stat-label">Masters</span>
                        </div>
                        {target.quality && (
                            <div className="hero-stat">
                                <span className="hero-stat-value">
                                    <QualityValue px={target.quality.median_fwhm_px} arcsec={target.quality.median_fwhm_arcsec}
                                        title={`Median FWHM over ${target.quality.measured} measured subs (all rigs)`} />
                                </span>
                                <span className="hero-stat-label">Median FWHM</span>
                            </div>
                        )}
                        {target.planetary_count > 0 && (
                            <div className="hero-stat">
                                <span className="hero-stat-value">{target.planetary_count}</span>
                                <span className="hero-stat-label">Planetary</span>
                            </div>
                        )}
                    </div>
                    <div className="target-hero-actions">
                        {inPool && (
                            <button
                                type="button"
                                className={`btn btn-secondary${pinned ? ' active' : ''}`}
                                onClick={handleTogglePin}
                                disabled={pinBusy}
                                title={pinned ? 'Unpin from Tonight' : 'Pin for Tonight'}
                            >
                                {pinned ? <PinOff size={16} /> : <Pin size={16} />}
                                {pinned ? 'Pinned for Tonight' : 'Pin for Tonight'}
                            </button>
                        )}
                        <Link
                            to={`/search?target_key=${encodeURIComponent(targetKey)}&frame_type=LIGHT`}
                            className="btn btn-primary"
                        >
                            View all subs &rarr;
                        </Link>
                        {target.catalog && (
                            <Link
                                to={`/catalogs/${target.catalog.catalog_type.toLowerCase()}/${encodeURIComponent(target.catalog.designation)}`}
                                className="btn btn-secondary"
                            >
                                Catalog entry
                            </Link>
                        )}
                    </div>
                </div>
            </div>

            {/* Filter x Rig matrix + goals */}
            <section className="target-section">
                <h3 className="section-title">Filter Breakdown &amp; Goals</h3>
                <table className="target-filter-table">
                    <thead>
                        <tr>
                            <th>Filter</th>
                            <th>Subs</th>
                            <th>Integration</th>
                            <th title="Median FWHM of the measured subs in this filter (all rigs)">FWHM</th>
                            <th>Goal (hours)</th>
                            <th>Progress</th>
                        </tr>
                    </thead>
                    <tbody>
                        {target.filters.map((f) => {
                            const draft = goalDrafts[f.filter] ?? '';
                            const goalSeconds = draft !== '' && !isNaN(parseFloat(draft)) ? parseFloat(draft) * 3600 : null;
                            const pct = goalSeconds ? Math.min(100, Math.round((f.seconds / goalSeconds) * 100)) : null;
                            return (
                                <tr key={f.filter}>
                                    <td>
                                        <span className="filter-swatch" style={{ backgroundColor: filterColor(f.filter) }} />
                                        {f.filter}
                                    </td>
                                    <td>{f.subs}</td>
                                    <td>{formatHours(f.seconds)}</td>
                                    <td>
                                        <QualityValue px={f.quality?.median_fwhm_px} arcsec={f.quality?.median_fwhm_arcsec}
                                            title={f.quality ? `Median over ${f.quality.measured} measured subs` : undefined} />
                                    </td>
                                    <td>
                                        <input
                                            type="number"
                                            className="input goal-input"
                                            min="0"
                                            step="0.5"
                                            value={draft}
                                            onChange={(e) => setGoalDrafts((prev) => ({ ...prev, [f.filter]: e.target.value }))}
                                            placeholder="--"
                                        />
                                    </td>
                                    <td>
                                        {pct !== null ? (
                                            <div className="goal-progress">
                                                <div className="goal-progress-bar" style={{ width: `${pct}%`, backgroundColor: filterColor(f.filter) }} />
                                                <span className="goal-progress-label">{pct}%</span>
                                            </div>
                                        ) : (
                                            <span className="text-muted text-sm">No goal</span>
                                        )}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                <div className="target-goals-actions">
                    <button className="btn btn-primary btn-sm" onClick={handleSaveGoals} disabled={savingGoals}>
                        {savingGoals ? 'Saving...' : 'Save Goals'}
                    </button>
                    {goalMessage && <span className="text-sm" style={{ marginLeft: '0.75rem' }}>{goalMessage}</span>}
                </div>
            </section>

            {/* By filter x rig */}
            {target.by_filter_rig && target.by_filter_rig.length > 0 && (
                <section className="target-section">
                    <h3 className="section-title">By Filter &amp; Rig</h3>
                    <table className="target-filter-table">
                        <thead>
                            <tr>
                                <th>Filter</th>
                                <th>Camera</th>
                                <th>Pixel Scale</th>
                                <th>Focal Length</th>
                                <th>Subs</th>
                                <th>Integration</th>
                                <th title="Median FWHM of the measured subs">FWHM</th>
                                <th title="Best 10% of subs (10th percentile FWHM)">Best</th>
                                <th title="Median half-flux radius">HFR</th>
                                <th title="Median eccentricity: 0 is round; above ~0.5 stars look elongated">Ecc.</th>
                            </tr>
                        </thead>
                        <tbody>
                            {target.by_filter_rig.map((r, idx) => (
                                <tr key={idx}>
                                    <td>{r.filter}</td>
                                    <td>{r.rig_name || r.camera || 'Unknown'}</td>
                                    <td>{r.pixel_scale != null ? `${r.pixel_scale.toFixed(2)}"/px` : '—'}</td>
                                    <td>{r.focal_length != null ? `${Math.round(r.focal_length)} mm` : '—'}</td>
                                    <td>{r.subs}</td>
                                    <td>{formatHours(r.seconds)}</td>
                                    <td>
                                        <QualityValue px={r.quality?.median_fwhm_px} arcsec={r.quality?.median_fwhm_arcsec}
                                            title={r.quality ? `Median FWHM, ${r.quality.measured} of ${r.subs} subs measured` : undefined} />
                                        {r.quality && r.quality.measured < r.subs && (
                                            <span className="quality-coverage">{r.quality.measured}/{r.subs} measured</span>
                                        )}
                                    </td>
                                    <td>
                                        <QualityValue px={r.quality?.best_fwhm_px} arcsec={r.quality?.best_fwhm_arcsec}
                                            title="10th percentile FWHM: what this rig delivers on its better subs" />
                                    </td>
                                    <td><QualityValue px={r.quality?.median_hfr_px} arcsec={r.quality?.median_hfr_arcsec} title="Median HFR" /></td>
                                    <td>{r.quality?.median_ecc != null ? r.quality.median_ecc.toFixed(2) : <span className="text-muted">—</span>}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </section>
            )}

            {/* Nights chart */}
            {nightsChartData.length > 0 && (
                <section className="target-section">
                    <h3 className="section-title">Nights</h3>
                    <ResponsiveContainer width="100%" height={280}>
                        <ComposedChart data={nightsChartData}>
                            <XAxis dataKey="night" stroke="var(--color-text-secondary)" fontSize={11} />
                            <YAxis yAxisId="hours" stroke="var(--color-text-secondary)" fontSize={11} tickFormatter={(v) => `${(v / 3600).toFixed(0)}h`} />
                            {hasNightQuality && (
                                <YAxis yAxisId="fwhm" orientation="right" stroke="var(--color-text-secondary)" fontSize={11}
                                    tickFormatter={(v) => `${Number(v).toFixed(1)}${fwhmUnit.trim()}`} domain={['auto', 'auto']} />
                            )}
                            <Tooltip
                                formatter={(value, name) => (name === 'Median FWHM'
                                    ? `${Number(value).toFixed(2)}${fwhmUnit}`
                                    : formatHours(value))}
                                contentStyle={{ background: 'var(--color-surface-elevated)', border: '1px solid var(--color-border)' }}
                            />
                            <Legend />
                            {filterKeysForChart.map((f) => (
                                <Bar key={f} yAxisId="hours" dataKey={f} stackId="a" fill={filterColor(f)} name={f} />
                            ))}
                            {hasNightQuality && (
                                <Line yAxisId="fwhm" type="monotone" dataKey="fwhm" name="Median FWHM" connectNulls
                                    stroke="var(--color-accent)" strokeWidth={2} dot={{ r: 3 }} />
                            )}
                        </ComposedChart>
                    </ResponsiveContainer>
                </section>
            )}

            {/* Masters gallery */}
            {target.masters && target.masters.length > 0 && (
                <section className="target-section">
                    <h3 className="section-title">Masters</h3>
                    <div className="target-masters-grid">
                        {target.masters.map((m) => (
                            <div key={m.id}>
                                <ImageCard image={m} />
                                {m.fwhm_px != null && (
                                    <div className="master-quality">
                                        <span>FWHM <QualityValue px={m.fwhm_px} arcsec={m.fwhm_arcsec} /></span>
                                        <span>HFR <QualityValue px={m.hfr_px} arcsec={m.hfr_arcsec} /></span>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}
