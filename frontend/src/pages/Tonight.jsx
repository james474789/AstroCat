import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
    LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceArea,
} from 'recharts';
import { useAuth } from '../context/AuthContext';
import {
    fetchEquipment, fetchTargets,
    fetchRecommendations, fetchTargetRecommendation, fetchLatestReplay,
    formatDateTime,
} from '../api/client';
import './Tonight.css';

// Binding contract: docs/design/R1-recommendation-engine.md §7 (API) and §8 (this page). The
// backend for these endpoints is built in parallel on feat/r1-engine-backend and does not exist
// on this branch, so this page is written strictly against the spec's response shapes.

const TIER_LABELS = {
    ASTRO: 'Astronomical dark',
    NAUTICAL: 'Nautical twilight',
    BRIGHT: 'Bright / narrowband only',
    NONE: 'No darkness',
};

const HORIZON_LABELS = {
    SAVED: 'Saved',
    LEARNED: 'Learned',
    DEFAULT: 'Default (flat 30°)',
};

const GOAL_LABELS = {
    SET: 'set goal',
    INFERRED: 'inferred goal',
    DEFAULT: 'default goal',
};

function formatHoursDecimal(h) {
    if (h == null) return '—';
    return `${h.toFixed(1)}h`;
}

function shiftDate(base, deltaDays) {
    const d = base ? new Date(`${base}T00:00:00Z`) : new Date();
    d.setUTCDate(d.getUTCDate() + deltaDays);
    return d.toISOString().slice(0, 10);
}

function formatLocalRange(startIso, endIso, timeZone) {
    if (!startIso || !endIso) return '—';
    try {
        const fmt = new Intl.DateTimeFormat('en-US', { timeZone, hour: '2-digit', minute: '2-digit' });
        return `${fmt.format(new Date(startIso))}–${fmt.format(new Date(endIso))} local`;
    } catch {
        return '—';
    }
}

function formatUTCRange(startIso, endIso) {
    if (!startIso || !endIso) return '';
    const f = (iso) => new Date(iso).toISOString().slice(11, 16);
    return `${f(startIso)}–${f(endIso)} UTC`;
}

function darkRanges(darkFlags) {
    const ranges = [];
    let start = null;
    (darkFlags || []).forEach((d, i) => {
        if (d && start === null) start = i;
        if (!d && start !== null) { ranges.push([start, i - 1]); start = null; }
    });
    if (start !== null) ranges.push([start, darkFlags.length - 1]);
    return ranges;
}

// ============ Small display components ============

function VerdictPill({ level }) {
    const cls = { GO: 'verdict-go', MARGINAL: 'verdict-marginal', DONT_BOTHER: 'verdict-dont-bother' }[level] || 'verdict-unknown';
    const label = { GO: 'GO', MARGINAL: 'MARGINAL', DONT_BOTHER: "DON'T BOTHER" }[level] || level || '—';
    return <span className={`verdict-pill ${cls}`}>{label}</span>;
}

function TierBadge({ tier, tierNote }) {
    const warn = tier === 'NAUTICAL' || tier === 'BRIGHT';
    const label = TIER_LABELS[tier] || tier;
    return (
        <span className={`tier-badge${warn ? ' warn' : ''}${tier === 'NONE' ? ' none' : ''}`} title={tierNote || ''}>
            {label}
        </span>
    );
}

function ReasonChips({ reasons, max }) {
    const list = max ? (reasons || []).slice(0, max) : (reasons || []);
    if (!list.length) return null;
    return (
        <div className="chip-row">
            {list.map((r, i) => (
                <span key={r.code ? `${r.code}-${i}` : i} className="chip reason-chip" title={r.text}>{r.text}</span>
            ))}
        </div>
    );
}

function ScoreBar({ score, components }) {
    const pct = Math.max(0, Math.min(1, score || 0)) * 100;
    const title = components
        ? Object.entries(components).map(([k, v]) => `${k}: ${(v ?? 0).toFixed(2)}`).join('\n')
        : `score: ${(score ?? 0).toFixed(2)}`;
    return (
        <div className="score-bar" title={title}>
            <div className="score-bar-fill" style={{ width: `${pct}%` }} />
            <span className="score-bar-label">{(score ?? 0).toFixed(2)}</span>
        </div>
    );
}

function GoalProgress({ haveHours, goalHours, goalSource }) {
    const pct = goalHours ? Math.min(100, ((haveHours || 0) / goalHours) * 100) : 0;
    const label = `${formatHoursDecimal(haveHours)} / ${goalHours ? formatHoursDecimal(goalHours) : '—'}`;
    return (
        <div className="goal-progress" title={`${label} (${GOAL_LABELS[goalSource] || goalSource || ''})`}>
            <div className="goal-progress-track">
                <div className="goal-progress-fill" style={{ width: `${pct}%` }} />
            </div>
            <span className="goal-progress-label">{label}</span>
        </div>
    );
}

function AltitudeSparkline({ curve, height = 70, showAxis = false }) {
    if (!curve || !curve.t_utc || curve.t_utc.length === 0) {
        return <div className="sparkline-empty" />;
    }
    const data = curve.t_utc.map((t, i) => ({
        t,
        alt: curve.alt?.[i],
        moonAlt: curve.moon_alt?.[i],
        limit: curve.limit?.[i],
    }));
    const ranges = darkRanges(curve.dark);
    return (
        <ResponsiveContainer width="100%" height={height}>
            <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                {ranges.map(([s, e], idx) => (
                    <ReferenceArea key={idx} x1={data[s]?.t} x2={data[e]?.t} fill="var(--color-primary)" fillOpacity={0.08} strokeOpacity={0} />
                ))}
                {showAxis ? (
                    <XAxis
                        dataKey="t"
                        tickFormatter={(t) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        stroke="var(--color-text-secondary)"
                        fontSize={10}
                        minTickGap={40}
                    />
                ) : (
                    <XAxis dataKey="t" hide />
                )}
                <YAxis domain={[0, 90]} hide={!showAxis} width={28} stroke="var(--color-text-secondary)" fontSize={10} />
                {showAxis && (
                    <Tooltip
                        labelFormatter={(t) => new Date(t).toLocaleTimeString()}
                        contentStyle={{ background: 'var(--color-surface-elevated)', border: '1px solid var(--color-border)' }}
                    />
                )}
                <Line type="monotone" dataKey="limit" stroke="var(--color-text-muted)" strokeWidth={1} dot={false} isAnimationActive={false} name="Horizon limit" />
                <Line type="monotone" dataKey="moonAlt" stroke="var(--color-text-muted)" strokeDasharray="4 3" strokeWidth={1.25} dot={false} isAnimationActive={false} name="Moon" />
                <Line type="monotone" dataKey="alt" stroke="var(--color-primary)" strokeWidth={2} dot={false} isAnimationActive={false} name="Target" />
            </LineChart>
        </ResponsiveContainer>
    );
}

// ============ Context strip ============

function ContextStrip({ context }) {
    if (!context) return null;
    const tz = context.site?.timezone || 'UTC';
    return (
        <div className="context-strip">
            <div className="context-item">
                <span className="muted small">Dark window</span>
                <span>{formatLocalRange(context.dark_start_utc, context.dark_end_utc, tz)}</span>
                <span className="muted small">{formatUTCRange(context.dark_start_utc, context.dark_end_utc)}</span>
            </div>
            <TierBadge tier={context.tier} tierNote={context.tier_note} />
            <div className="context-item">
                <span className="muted small">Moon</span>
                <span>{context.moon ? `${Math.round(context.moon.illumination * 100)}% lit` : '—'}</span>
                <span className="muted small">
                    {context.moon ? `up ${Math.round((context.moon.up_fraction || 0) * 100)}% of dark` : ''}
                </span>
            </div>
            <div className="context-item">
                <span className="muted small">Horizon</span>
                {context.horizon_source === 'LEARNED' ? (
                    <Link to="/equipment" className="horizon-link">Learned horizon (floor {context.floor_deg}°)</Link>
                ) : (
                    <span>{HORIZON_LABELS[context.horizon_source] || context.horizon_source} (floor {context.floor_deg}°)</span>
                )}
            </div>
        </div>
    );
}

// ============ Hero card ============

function HeroCard({ hero, verdict }) {
    if (!hero) return null;
    const reasons = (verdict?.reasons?.length ? verdict.reasons : hero.reasons) || [];
    return (
        <div className="hero-card">
            <div className="hero-card-top">
                <VerdictPill level={verdict?.level} />
                <Link to={`/targets/${encodeURIComponent(hero.target_key)}`} className="hero-name">
                    {hero.name || hero.target_key}
                </Link>
                <span className="muted">{hero.kind}</span>
            </div>
            <div className="hero-card-meta muted small">
                {hero.rig?.name} &middot; mode {hero.mode}
                {hero.alternatives?.length > 0 && ` · also: ${hero.alternatives.map((a) => a.rig_name).join(', ')}`}
            </div>
            <ReasonChips reasons={reasons} />
            <AltitudeSparkline curve={hero.curve} height={220} showAxis />
            <div className="hero-card-stats">
                <div><span className="muted small">Have</span> {formatHoursDecimal(hero.have_hours)} / {hero.goal_hours ? formatHoursDecimal(hero.goal_hours) : '—'} ({GOAL_LABELS[hero.goal_source] || hero.goal_source})</div>
                <div><span className="muted small">Usable</span> {formatHoursDecimal(hero.usable_hours)}</div>
                <div><span className="muted small">Moon sep</span> {hero.moon_sep_min_deg != null ? `${Math.round(hero.moon_sep_min_deg)}°` : '—'}</div>
                <div><span className="muted small">Max alt</span> {hero.max_alt_deg != null ? `${Math.round(hero.max_alt_deg)}°` : '—'}</div>
            </div>
        </div>
    );
}

// ============ Lane pick card ============

function PickCard({ pick }) {
    return (
        <div className="pick-card">
            <div className="pick-card-header">
                <Link to={`/targets/${encodeURIComponent(pick.target_key)}`} className="pick-name">
                    {pick.name || pick.target_key}
                </Link>
                <span className="badge">{pick.kind}</span>
            </div>
            <div className="muted small">{pick.target_key}</div>
            <div className="pick-rig-row">
                <span>{pick.rig?.name}</span>
                {pick.alternatives?.length > 0 && (
                    <span
                        className="muted small"
                        title={pick.alternatives.map((a) => `${a.rig_name}: ${a.score?.toFixed(2)}`).join(', ')}
                    >
                        also: {pick.alternatives.map((a) => a.rig_name).join(', ')}
                    </span>
                )}
            </div>
            <div className="pick-mode-row">
                <span className="chip">{pick.mode}</span>
                <ScoreBar score={pick.score} components={pick.components} />
            </div>
            <ReasonChips reasons={pick.reasons} max={3} />
            <AltitudeSparkline curve={pick.curve} height={70} />
            <GoalProgress haveHours={pick.have_hours} goalHours={pick.goal_hours} goalSource={pick.goal_source} />
            <Link to={`/targets/${encodeURIComponent(pick.target_key)}`} className="btn btn-secondary btn-sm open-target-link">
                Open target
            </Link>
        </div>
    );
}

// ============ Excluded counts (no feasible picks) ============

function ExcludedCountsPanel({ counts }) {
    const entries = Object.entries(counts || {}).filter(([, v]) => v > 0);
    return (
        <div className="empty-state">
            <h3 className="empty-state-title">No feasible picks tonight</h3>
            {entries.length > 0 && (
                <div className="chip-row excluded-counts">
                    {entries.map(([k, v]) => <span key={k} className="chip">{k}: {v}</span>)}
                </div>
            )}
        </div>
    );
}

// ============ "Why not…?" panel ============

function normalizeWhyNot(data) {
    if (!data) return [];
    const list = data.results || data.rigs || (Array.isArray(data) ? data : []);
    return list.map((item) => ({
        rigName: item.rig?.name || item.rig_name || item.name || 'Rig',
        pick: item.pick || (item.score != null ? item : null),
        excludedReason: item.excluded_reason || item.pick?.excluded_reason,
        details: item.details,
    }));
}

function formatDetails(details) {
    if (!details) return '';
    try {
        return Object.entries(details)
            .map(([k, v]) => `${k}: ${typeof v === 'number' ? v.toFixed(2) : v}`)
            .join(' · ');
    } catch {
        return '';
    }
}

function WhyNotPanel({ date, siteId, rig }) {
    const [text, setText] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [result, setResult] = useState(null);

    async function handleSubmit(e) {
        e.preventDefault();
        const query = text.trim();
        if (!query) return;
        setLoading(true);
        setError(null);
        setResult(null);
        let key = query.toUpperCase().replace(/\s+/g, '');
        try {
            const found = await fetchTargets({ search: query, page_size: 5 });
            if (found?.items?.length) key = found.items[0].target_key;
        } catch {
            // No target search match; fall back to treating the input as a raw key.
        }
        try {
            const data = await fetchTargetRecommendation(key, { date, siteId, rig });
            setResult({ key, data });
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }

    const entries = useMemo(() => normalizeWhyNot(result?.data), [result]);

    return (
        <div className="why-not-panel">
            <h2 className="section-title">Why isn&apos;t X on the list?</h2>
            <form onSubmit={handleSubmit} className="why-not-form">
                <input
                    className="input"
                    placeholder="Target name or key, e.g. NGC7000"
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                />
                <button type="submit" className="btn btn-secondary" disabled={loading}>
                    {loading ? 'Checking…' : 'Check'}
                </button>
            </form>
            {error && <div className="form-error">{error}</div>}
            {result && (
                <div className="why-not-results">
                    <div className="why-not-title">{result.data?.name || result.key}</div>
                    {entries.length === 0 && <p className="muted small">No per-rig detail in the response.</p>}
                    {entries.map((entry, i) => (
                        <div key={i} className="why-not-row">
                            <span className="why-not-rig">{entry.rigName}</span>
                            {entry.pick ? (
                                <span className="badge badge-success">
                                    feasible &middot; score {entry.pick.score != null ? entry.pick.score.toFixed(2) : '—'} &middot; mode {entry.pick.mode}
                                </span>
                            ) : (
                                <span className="badge badge-warning">{entry.excludedReason || 'excluded'}</span>
                            )}
                            {entry.details && <span className="muted small">{formatDetails(entry.details)}</span>}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ============ Admin replay panel ============

function ReplayPanel() {
    const [open, setOpen] = useState(false);
    const replayQuery = useQuery({
        queryKey: ['latestReplay'],
        queryFn: fetchLatestReplay,
        retry: false,
        staleTime: 10 * 60 * 1000,
    });

    if (replayQuery.isError || !replayQuery.data) return null;

    const report = replayQuery.data;
    const metrics = report.metrics || report;
    const metricKeys = ['hit@1', 'hit@3', 'hit@5', 'hit@10', 'mrr', 'feasible_recall']
        .filter((k) => metrics[k] != null);

    return (
        <div className="replay-panel">
            <button type="button" className="replay-toggle" onClick={() => setOpen((o) => !o)}>
                {open ? '▾' : '▸'} Replay report{report.generated_at ? ` (${formatDateTime(report.generated_at)})` : ''}
            </button>
            {open && (
                <div className="replay-body">
                    {metricKeys.map((k) => (
                        <div key={k} className="replay-metric">
                            <span className="muted small">{k}</span>
                            <span>{typeof metrics[k] === 'number' ? metrics[k].toFixed(3) : String(metrics[k])}</span>
                        </div>
                    ))}
                    {report.nights != null && (
                        <div className="replay-metric">
                            <span className="muted small">nights</span>
                            <span>{report.nights}</span>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ============ Main page ============

export default function Tonight() {
    const { user } = useAuth();
    const isAdmin = !!user?.is_admin;

    const [selectedDate, setSelectedDate] = useState('');
    const [selectedSiteId, setSelectedSiteId] = useState(null);
    const [rig, setRig] = useState('mounted');
    const perLane = 6;

    const equipmentQuery = useQuery({
        queryKey: ['equipment'],
        queryFn: fetchEquipment,
        staleTime: 60 * 1000,
    });
    const sites = equipmentQuery.data?.sites || [];
    const rigs = equipmentQuery.data?.rigs || [];
    const mountedRig = rigs.find((r) => r.is_mounted);
    // Fall back to the default (first) site until the viewer picks one explicitly, derived in
    // render rather than an effect (same pattern as Equipment.jsx's SitesTab).
    const siteId = selectedSiteId ?? sites[0]?.id ?? null;

    const recQuery = useQuery({
        queryKey: ['recommendations', siteId, rig, selectedDate, perLane],
        queryFn: () => fetchRecommendations({ date: selectedDate || undefined, siteId, rig, perLane }),
        enabled: siteId != null,
        staleTime: 60 * 1000,
        retry: false,
    });

    if (equipmentQuery.isLoading) {
        return (
            <div className="loading-state">
                <div className="spinner" />
                <p>Loading equipment…</p>
            </div>
        );
    }

    if (equipmentQuery.isError) {
        return (
            <div className="empty-state">
                <h3 className="empty-state-title">Failed to load equipment</h3>
                <p className="empty-state-text">{equipmentQuery.error?.message}</p>
                <button className="btn btn-primary" onClick={() => equipmentQuery.refetch()}>Retry</button>
            </div>
        );
    }

    if (sites.length === 0) {
        return (
            <div className="empty-state">
                <h3 className="empty-state-title">No observing sites yet</h3>
                <p className="empty-state-text">Add a site to get tonight's picks.</p>
                <Link className="btn btn-primary" to="/equipment">Go to Equipment</Link>
            </div>
        );
    }

    if (rigs.length === 0) {
        return (
            <div className="empty-state">
                <h3 className="empty-state-title">No rigs yet</h3>
                <p className="empty-state-text">Add a rig so AstroCat knows what it's planning for.</p>
                <Link className="btn btn-primary" to="/equipment">Go to Equipment</Link>
            </div>
        );
    }

    const data = recQuery.data;
    const context = data?.context;
    const effectiveDate = selectedDate || context?.night || '';

    function handlePrev() {
        setSelectedDate(shiftDate(selectedDate || context?.night, -1));
    }
    function handleNext() {
        setSelectedDate(shiftDate(selectedDate || context?.night, 1));
    }

    const nonEmptyLanes = (data?.lanes || []).filter((l) => l.items && l.items.length > 0);
    const hasAnyPicks = !!data?.hero || nonEmptyLanes.length > 0;

    return (
        <div className="tonight-page">
            <div className="page-header">
                <h1 className="page-title">Tonight</h1>
                <p className="page-subtitle">What to image tonight, ranked by your history and sky</p>
            </div>

            <div className="tonight-controls">
                <div className="date-control">
                    <button type="button" className="btn btn-icon" onClick={handlePrev} title="Previous night">&lsaquo;</button>
                    <input
                        type="date"
                        className="input"
                        value={effectiveDate}
                        onChange={(e) => setSelectedDate(e.target.value)}
                    />
                    <button type="button" className="btn btn-icon" onClick={handleNext} title="Next night">&rsaquo;</button>
                    {selectedDate && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelectedDate('')}>Tonight</button>
                    )}
                </div>
                <select
                    className="input select"
                    value={siteId ?? ''}
                    onChange={(e) => setSelectedSiteId(e.target.value ? Number(e.target.value) : null)}
                >
                    {sites.map((s) => (
                        <option key={s.id} value={s.id}>{s.name}{s.is_default ? ' (default)' : ''}</option>
                    ))}
                </select>
                <select className="input select" value={rig} onChange={(e) => setRig(e.target.value)}>
                    <option value="mounted">Mounted{mountedRig ? `: ${mountedRig.name}` : ''}</option>
                    <option value="all">All rigs (best per target)</option>
                    {rigs.map((r) => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
                </select>
            </div>

            {recQuery.isLoading && (
                <div className="loading-state">
                    <div className="spinner" />
                    <p>Working out tonight’s picks…</p>
                </div>
            )}

            {recQuery.isError && (
                <div className="form-error">{recQuery.error?.message || 'Failed to load recommendations.'}</div>
            )}

            {data && (
                <>
                    <ContextStrip context={context} />

                    {context?.rig_mode === 'ALL_FALLBACK' && (
                        <div className="all-fallback-banner">
                            No rig marked as mounted: showing the best rig per target. <Link to="/equipment">Set one in Equipment.</Link>
                        </div>
                    )}

                    {context?.tier === 'NONE' ? (
                        <div className="empty-state">
                            <h3 className="empty-state-title">Too bright tonight</h3>
                            <p className="empty-state-text">Sun never below −9°.</p>
                        </div>
                    ) : !hasAnyPicks ? (
                        <ExcludedCountsPanel counts={data.excluded_counts} />
                    ) : (
                        <>
                            <HeroCard hero={data.hero} verdict={data.verdict} />

                            {nonEmptyLanes.map((lane) => (
                                <section key={lane.id} className="lane-section">
                                    <h2 className="section-title">{lane.title}</h2>
                                    <div className="lane-grid">
                                        {lane.items.map((pick) => (
                                            <PickCard key={`${pick.target_key}-${pick.rig?.id}`} pick={pick} />
                                        ))}
                                    </div>
                                </section>
                            ))}
                        </>
                    )}

                    {data.skipped_rigs?.length > 0 && (
                        <div className="skipped-rigs muted small">
                            Skipped: {data.skipped_rigs.map((r) => `${r.name} (${r.reason})`).join(', ')}
                        </div>
                    )}
                </>
            )}

            <WhyNotPanel date={selectedDate || undefined} siteId={siteId} rig={rig} />

            {isAdmin && <ReplayPanel />}
        </div>
    );
}
