import { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
    ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { Pin, PinOff } from 'lucide-react';
import {
    fetchTarget, updateTargetGoals, formatHours, API_BASE_URL,
    fetchTargetRecommendation, postRecommendationFeedback, fetchNightTimeline,
} from '../api/client';
import ImageCard from '../components/images/ImageCard';
import QualityValue from '../components/quality/QualityValue';
import { filterColor } from '../utils/filterColors';
import SessionQualityChart from '../components/quality/SessionQualityChart';
import SessionSummary from '../components/quality/SessionSummary';
import { Button, EmptyState, Spinner } from '../components/ui';
import './NightReport.css'; // shared .night-table-head
import { useQualityUnits } from '../context/QualityUnitsContext';
import './TargetDetail.css';

// R2a §7: "Pin for Tonight" toggle. Binding contract: docs/design/R2a-feedback-dashboard.md §6-§7.
// The backend (feat/r2a-feedback-backend) is built in parallel and does not exist on this branch.
// Initial state comes from GET /api/recommendations/target/{key} (`feedback.pinned`); a 404 (the
// key isn't in the candidate pool) hides the toggle quietly rather than showing an error.


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
    const [selectedNight, setSelectedNight] = useState(null);
    const [nightTimeline, setNightTimeline] = useState({ data: null, loading: false, error: null });
    const nightPanelRef = useRef(null);

    useEffect(() => {
        if (!selectedNight) return undefined;
        let cancelled = false;
        setNightTimeline((prev) => ({ ...prev, loading: true, error: null }));
        fetchNightTimeline(selectedNight, { target_key: targetKey })
            .then((data) => { if (!cancelled) setNightTimeline({ data, loading: false, error: null }); })
            .catch((e) => { if (!cancelled) setNightTimeline({ data: null, loading: false, error: e.message || 'Could not load the night' }); });
        const t = setTimeout(() => nightPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
        return () => { cancelled = true; clearTimeout(t); };
    }, [selectedNight, targetKey]);

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
                <Spinner />
                <p>Loading target...</p>
            </div>
        );
    }

    if (error || !target) {
        return (
            <EmptyState
                icon={<span aria-hidden="true">&#128561;</span>}
                title={error || 'Target not found'}
                action={<Button to="/targets">Back to Targets</Button>}
            />
        );
    }

    return (
        <div className="page-target-detail">
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
                            <Button
                                className={pinned ? 'is-pinned' : ''}
                                onClick={handleTogglePin}
                                disabled={pinBusy}
                                title={pinned ? 'Unpin from Tonight' : 'Pin for Tonight'}
                                icon={pinned ? <PinOff size={16} /> : <Pin size={16} />}
                            >
                                {pinned ? 'Pinned for Tonight' : 'Pin for Tonight'}
                            </Button>
                        )}
                        <Button
                            to={`/search?target_key=${encodeURIComponent(targetKey)}&frame_type=LIGHT`}
                            variant="filled"
                        >
                            View all subs &rarr;
                        </Button>
                        {target.catalog && (
                            <Button to={`/catalogs/${target.catalog.catalog_type.toLowerCase()}/${encodeURIComponent(target.catalog.designation)}`}>
                                Catalog entry
                            </Button>
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
                    <Button variant="filled" size="sm" onClick={handleSaveGoals} loading={savingGoals}>
                        {savingGoals ? 'Saving...' : 'Save Goals'}
                    </Button>
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

            {/* Nights chart: integration per night; median FWHM in its own synced chart (one y-axis each) */}
            {nightsChartData.length > 0 && (
                <section className="target-section">
                    <h3 className="section-title">Nights</h3>
                    <p className="text-muted text-sm" style={{ margin: '0 0 0.5rem' }}>Click a night to see how star quality changed through it.</p>
                    <ResponsiveContainer width="100%" height={240}>
                        <ComposedChart data={nightsChartData} syncId="target-nights" style={{ cursor: 'pointer' }}
                            onClick={(state) => {
                                const night = state?.activeLabel ?? nightsChartData[state?.activeIndex]?.night;
                                if (night) setSelectedNight(night);
                            }}>
                            <XAxis dataKey="night" stroke="var(--color-text-secondary)" fontSize={11} />
                            <YAxis width={48} stroke="var(--color-text-secondary)" fontSize={11} tickFormatter={(v) => `${(v / 3600).toFixed(0)}h`} />
                            <Tooltip
                                formatter={(value) => formatHours(value)}
                                contentStyle={{ background: 'var(--color-surface-elevated)', border: '1px solid var(--color-border)' }}
                            />
                            <Legend />
                            {filterKeysForChart.map((f) => (
                                <Bar key={f} dataKey={f} stackId="a" fill={filterColor(f)} name={f} />
                            ))}
                        </ComposedChart>
                    </ResponsiveContainer>
                    {hasNightQuality && (
                        <>
                            <div className="session-panel-label">Median FWHM per night ({fwhmUnit.trim()})</div>
                            <ResponsiveContainer width="100%" height={110}>
                                <ComposedChart data={nightsChartData} syncId="target-nights" style={{ cursor: 'pointer' }}
                                    onClick={(state) => {
                                        const night = state?.activeLabel ?? nightsChartData[state?.activeIndex]?.night;
                                        if (night) setSelectedNight(night);
                                    }}>
                                    <XAxis dataKey="night" hide />
                                    <YAxis width={48} domain={['auto', 'auto']} stroke="var(--color-text-secondary)" fontSize={11}
                                        tickFormatter={(v) => Number(v).toFixed(1)} />
                                    <Tooltip
                                        formatter={(value) => (value == null ? 'not measured' : `${Number(value).toFixed(2)}${fwhmUnit}`)}
                                        contentStyle={{ background: 'var(--color-surface-elevated)', border: '1px solid var(--color-border)' }}
                                    />
                                    <Line type="monotone" dataKey="fwhm" name="Median FWHM" connectNulls
                                        stroke="var(--color-text-secondary)" strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
                                </ComposedChart>
                            </ResponsiveContainer>
                        </>
                    )}
                </section>
            )}

            {/* Q1c: the selected night's timeline for this target */}
            {selectedNight && (
                <section className="target-section" ref={nightPanelRef}>
                    <div className="night-table-head">
                        <h3 className="section-title">Night of {selectedNight}</h3>
                        <span style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                            <Button size="sm" to={`/nights/${selectedNight}?target=${encodeURIComponent(targetKey)}`}>Night report</Button>
                            <Button size="sm" to={`/nights/${selectedNight}`}>Whole night</Button>
                            <Button variant="plain" size="sm" onClick={() => setSelectedNight(null)}>Close</Button>
                        </span>
                    </div>
                    {nightTimeline.loading && !nightTimeline.data && <div className="loading-state"><Spinner /></div>}
                    {nightTimeline.error && <p className="text-muted">{nightTimeline.error}</p>}
                    {nightTimeline.data && (
                        <div style={{ opacity: nightTimeline.loading ? 0.5 : 1 }}>
                            <SessionQualityChart timeline={nightTimeline.data} rigId="ALL" height={240} />
                            <SessionSummary summary={nightTimeline.data.summary} rigId="ALL" />
                        </div>
                    )}
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
