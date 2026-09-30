import { useState, useMemo, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
    LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceArea,
} from 'recharts';
import { Pin, PinOff, Clock, EyeOff, Camera, ChevronDown, X } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import {
    fetchEquipment, fetchTargets,
    fetchRecommendations, fetchTargetRecommendation, fetchLatestReplay,
    postRecommendationFeedback, fetchRecommendationFeedback, fetchRecommendationOutcomes,
    formatDateTime,
} from '../api/client';
import './Tonight.css';

// Binding contract: docs/design/R1-recommendation-engine.md §7 (API) and §8 (this page), and
// docs/design/R2a-feedback-dashboard.md §5-§7 (feedback, outcomes and this page's action row,
// pinned lane, hidden manager and outcomes panel). The backend for these endpoints is built in
// parallel on feat/r1-engine-backend / feat/r2a-feedback-backend and does not exist on this
// branch, so this page is written strictly against the spec's response shapes.

const SNOOZE_OPTIONS = [
    { nights: 1, label: '1 night' },
    { nights: 7, label: '1 week' },
    { nights: 30, label: '1 month' },
];

const DISMISS_REASONS = [
    { reason: 'DONE', label: 'Done with it' },
    { reason: 'NOT_MY_TYPE', label: 'Not my kind of target' },
    { reason: 'TOO_HARD', label: 'Too hard' },
    { reason: 'OTHER', label: 'Other' },
];

const DISMISS_REASON_LABELS = Object.fromEntries(DISMISS_REASONS.map((r) => [r.reason, r.label]));
const SNOOZE_LABELS = { 1: 'a night', 7: 'a week', 30: 'a month' };

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

// ============ Feedback: optimistic recommendation cache updates ============
// R2a §7: card actions update the cache immediately (the card leaves or moves lanes at once),
// then the mutation is sent and the query invalidated/refetched to reconcile with the server.

function findPickInData(data, targetKey) {
    if (data?.hero?.target_key === targetKey) return data.hero;
    for (const lane of data?.lanes || []) {
        const found = (lane.items || []).find((p) => p.target_key === targetKey);
        if (found) return found;
    }
    for (const entry of data?.rig_plan || []) {
        const found = (entry.items || []).find((p) => p.target_key === targetKey);
        if (found) return found;
    }
    return null;
}

function updateRigPlan(rigPlan, targetKey, fn) {
    return (rigPlan || []).map((entry) => ({
        ...entry,
        items: (entry.items || []).flatMap((p) => {
            if (p.target_key !== targetKey) return [p];
            const next = fn(p);
            return next ? [next] : [];
        }),
    }));
}

function removePickFromLanes(lanes, targetKey) {
    return (lanes || []).map((lane) => ({
        ...lane,
        items: (lane.items || []).filter((p) => p.target_key !== targetKey),
    }));
}

function applyOptimisticFeedback(data, targetKey, action) {
    if (!data) return data;
    const pick = findPickInData(data, targetKey);
    let lanes = data.lanes || [];
    let hero = data.hero;
    let rigPlan = data.rig_plan || [];

    if (action === 'PIN' || action === 'UNPIN') {
        const pinned = action === 'PIN';
        rigPlan = updateRigPlan(rigPlan, targetKey, (p) => ({ ...p, feedback: { ...p.feedback, pinned } }));
    } else if (action === 'SNOOZE' || action === 'DISMISS') {
        rigPlan = updateRigPlan(rigPlan, targetKey, () => null);
    }

    if (action === 'PIN' && pick) {
        lanes = removePickFromLanes(lanes, targetKey);
        const updatedPick = { ...pick, feedback: { ...pick.feedback, pinned: true } };
        const pinnedIdx = lanes.findIndex((l) => l.id === 'pinned');
        if (pinnedIdx >= 0) {
            lanes = lanes.map((l, i) => (i === pinnedIdx ? { ...l, items: [updatedPick, ...(l.items || [])] } : l));
        } else {
            lanes = [{ id: 'pinned', title: 'Your pins', items: [updatedPick] }, ...lanes];
        }
        if (hero?.target_key === targetKey) hero = updatedPick;
    } else if (action === 'UNPIN') {
        // Where the pick lands once unpinned depends on the recomputed ranking, so it's dropped
        // from the pinned lane immediately and the real placement arrives on refetch.
        lanes = removePickFromLanes(lanes, targetKey);
        if (hero?.target_key === targetKey) hero = { ...hero, feedback: { ...hero.feedback, pinned: false } };
    } else if (action === 'SNOOZE' || action === 'DISMISS') {
        lanes = removePickFromLanes(lanes, targetKey);
        if (hero?.target_key === targetKey) hero = null;
    }
    // UNSNOOZE / UNDISMISS / IMAGED don't change what's visible on this cached result in a way we
    // can predict client-side, so they rely solely on the query invalidation below.
    return { ...data, lanes, hero, rig_plan: rigPlan };
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

// ============ Toast (page-local; no shared toast component exists yet, matches the
// Equipment.jsx / Admin.jsx page-local pattern, extended with an optional Undo action) ============

function useTonightToast() {
    const [toast, setToast] = useState(null);
    const timerRef = useRef(null);

    function showToast(message, { type = 'info', durationMs = 6000, onUndo } = {}) {
        if (timerRef.current) clearTimeout(timerRef.current);
        setToast({ message, type, onUndo });
        if (durationMs > 0) {
            timerRef.current = setTimeout(() => setToast(null), durationMs);
        }
    }
    function dismissToast() {
        if (timerRef.current) clearTimeout(timerRef.current);
        setToast(null);
    }
    return [toast, showToast, dismissToast];
}

function Toast({ toast, onDismiss }) {
    if (!toast) return null;
    return (
        <div className={`tonight-toast ${toast.type}`} role="status" aria-live="polite">
            <span>{toast.message}</span>
            {toast.onUndo && (
                <button type="button" className="tonight-toast-undo" onClick={() => { toast.onUndo(); onDismiss(); }}>
                    Undo
                </button>
            )}
            <button type="button" className="tonight-toast-close" onClick={onDismiss} aria-label="Dismiss">
                <X size={14} />
            </button>
        </div>
    );
}

// ============ Feedback action row (Pin, Snooze, Not interested, Imaged) ============

function ActionRow({ pinned, onAction }) {
    const [openMenu, setOpenMenu] = useState(null); // 'snooze' | 'dismiss' | null
    const rowRef = useRef(null);

    useEffect(() => {
        if (!openMenu) return undefined;
        function handleOutside(e) {
            if (rowRef.current && !rowRef.current.contains(e.target)) setOpenMenu(null);
        }
        document.addEventListener('mousedown', handleOutside);
        return () => document.removeEventListener('mousedown', handleOutside);
    }, [openMenu]);

    function toggleMenu(name) {
        setOpenMenu((cur) => (cur === name ? null : name));
    }

    return (
        <div className="pick-action-row" ref={rowRef} onClick={(e) => e.stopPropagation()}>
            <button
                type="button"
                className={`action-btn${pinned ? ' active' : ''}`}
                title={pinned ? 'Unpin' : 'Pin for Tonight'}
                onClick={() => onAction(pinned ? 'UNPIN' : 'PIN')}
            >
                {pinned ? <PinOff size={16} /> : <Pin size={16} />}
            </button>

            <div className="action-dropdown">
                <button type="button" className="action-btn" title="Snooze" onClick={() => toggleMenu('snooze')}>
                    <Clock size={16} /><ChevronDown size={12} />
                </button>
                {openMenu === 'snooze' && (
                    <div className="action-menu">
                        {SNOOZE_OPTIONS.map((opt) => (
                            <button
                                key={opt.nights}
                                type="button"
                                onClick={() => { onAction('SNOOZE', { nights: opt.nights }); setOpenMenu(null); }}
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>
                )}
            </div>

            <div className="action-dropdown">
                <button type="button" className="action-btn" title="Not interested" onClick={() => toggleMenu('dismiss')}>
                    <EyeOff size={16} /><ChevronDown size={12} />
                </button>
                {openMenu === 'dismiss' && (
                    <div className="action-menu">
                        {DISMISS_REASONS.map((r) => (
                            <button
                                key={r.reason}
                                type="button"
                                onClick={() => { onAction('DISMISS', { reason: r.reason }); setOpenMenu(null); }}
                            >
                                {r.label}
                            </button>
                        ))}
                    </div>
                )}
            </div>

            <button type="button" className="action-btn" title="I imaged it" onClick={() => onAction('IMAGED')}>
                <Camera size={16} />
            </button>
        </div>
    );
}

// ============ Context strip ============

function ContextStrip({ context, onOpenHidden }) {
    if (!context) return null;
    const tz = context.site?.timezone || 'UTC';
    const counts = context.feedback_counts || {};
    const hiddenCount = (counts.snoozed || 0) + (counts.dismissed || 0);
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
            {onOpenHidden && (
                <div className="context-item">
                    <span className="muted small">&nbsp;</span>
                    <button type="button" className="hidden-link" onClick={onOpenHidden}>
                        Hidden ({hiddenCount})
                    </button>
                </div>
            )}
        </div>
    );
}

// ============ Wide-field regions ============
// R1b: a "WF" pick is a curated region (WF_…), not a catalog object, so /targets/WF_… has no
// images. It links to its member objects instead.

function TargetName({ pick, className }) {
    const label = pick.name || pick.target_key;
    if (pick.catalog === 'WF') return <span className={className}>{label}</span>;
    return <Link to={`/targets/${encodeURIComponent(pick.target_key)}`} className={className}>{label}</Link>;
}

function MemberLinks({ members }) {
    if (!members || members.length === 0) return null;
    return (
        <div className="pick-members small">
            <span className="muted">Includes</span>{' '}
            {members.map((m, i) => (
                <span key={m}>
                    {i > 0 && ', '}
                    <Link to={`/targets/${encodeURIComponent(m)}`}>{m}</Link>
                </span>
            ))}
        </div>
    );
}

function formatSizeArcmin(v) {
    if (v == null) return '';
    if (v < 60) return `${Math.round(v)}′`;
    return `${String(Math.round((v / 60) * 10) / 10)}°`;
}

function formatSizeWindow(w) {
    return w && w.length === 2 ? `${formatSizeArcmin(w[0])}–${formatSizeArcmin(w[1])}` : null;
}

// ============ Hero card ============

function HeroCard({ hero, verdict, onAction }) {
    if (!hero) return null;
    const reasons = (verdict?.reasons?.length ? verdict.reasons : hero.reasons) || [];
    return (
        <div className="hero-card">
            <div className="hero-card-top">
                <VerdictPill level={verdict?.level} />
                <TargetName pick={hero} className="hero-name" />
                <span className="muted">{hero.kind}</span>
                {onAction && (
                    <ActionRow
                        pinned={!!hero.feedback?.pinned}
                        onAction={(action, extra) => onAction(hero, 'hero', 1, action, extra)}
                    />
                )}
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

function PickCard({ pick, laneId, rank, onAction }) {
    return (
        <div className="pick-card">
            <div className="pick-card-header">
                <TargetName pick={pick} className="pick-name" />
                <span className="badge">{pick.kind}</span>
            </div>
            <div className="muted small">{pick.target_key}</div>
            {onAction && (
                <ActionRow
                    pinned={!!pick.feedback?.pinned}
                    onAction={(action, extra) => onAction(pick, laneId, rank, action, extra)}
                />
            )}
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
            {pick.catalog === 'WF' ? (
                <MemberLinks members={pick.members} />
            ) : (
                <Link to={`/targets/${encodeURIComponent(pick.target_key)}`} className="btn btn-secondary btn-sm open-target-link">
                    Open target
                </Link>
            )}
        </div>
    );
}

// ============ Per-rig plan (several mounted rigs) ============

function RigPlanSection({ plan, onAction }) {
    if (!plan || plan.length === 0) return null;
    return (
        <section className="lane-section rig-plan-section">
            <h2 className="section-title">Plan per mounted rig</h2>
            <p className="muted small rig-plan-hint">
                One target per rig, none shared, with backups if the primary clouds out or sets. A rig with no target that clears 1.5 h says why.
            </p>
            <div className="rig-plan-grid">
                {plan.map((entry) => {
                    const [primary, ...backups] = entry.items || [];
                    const sizeWindow = formatSizeWindow(entry.size_window_arcmin);
                    return (
                        <div key={entry.rig.id} className="rig-plan-column">
                            <h3 className="rig-plan-rig">{entry.rig.name}</h3>
                            <div className="rig-plan-meta">
                                {entry.verdict && <VerdictPill level={entry.verdict.level} />}
                                {sizeWindow && <span className="muted small">targets {sizeWindow}</span>}
                            </div>
                            {primary ? (
                                <PickCard pick={primary} laneId="rig_plan" rank={1} onAction={onAction} />
                            ) : (
                                <p className="rig-plan-note">
                                    <strong>Nothing good tonight.</strong>{' '}
                                    <span className="muted">{entry.note || 'Nothing feasible for this rig tonight.'}</span>
                                </p>
                            )}
                            {backups.length > 0 && (
                                <ul className="rig-plan-backups">
                                    {backups.map((p) => (
                                        <li key={p.target_key}>
                                            <span className="muted small">Backup</span>{' '}
                                            <TargetName pick={p} />
                                            <span className="muted small"> · {p.mode} · {p.score?.toFixed(2)}</span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    );
                })}
            </div>
        </section>
    );
}

// ============ Pinned-but-unavailable strip ============

function PinnedUnavailableStrip({ items }) {
    if (!items || items.length === 0) return null;
    return (
        <div className="pinned-unavailable-strip muted small">
            Pinned but not tonight: {items.map((p, i) => (
                <span key={p.target_key}>
                    {i > 0 && ', '}{p.name || p.target_key} ({p.excluded_reason})
                </span>
            ))}
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

// ============ Hidden-items manager ============

function HiddenManagerModal({ onClose, onRestore }) {
    const feedbackQuery = useQuery({
        queryKey: ['recommendationFeedback'],
        queryFn: fetchRecommendationFeedback,
        staleTime: 0,
    });

    const items = feedbackQuery.data?.items || [];
    const snoozed = items.filter((i) => i.snoozed_until);
    const dismissed = items.filter((i) => i.dismissed);

    return (
        <div className="tonight-modal-overlay" onClick={onClose}>
            <div className="tonight-modal-content" onClick={(e) => e.stopPropagation()}>
                <header className="tonight-modal-header">
                    <h2>Hidden targets</h2>
                    <button type="button" className="tonight-modal-close" onClick={onClose} aria-label="Close">
                        <X size={18} />
                    </button>
                </header>
                <div className="tonight-modal-body">
                    {feedbackQuery.isLoading && <p className="muted small">Loading…</p>}
                    {feedbackQuery.isError && <p className="form-error">{feedbackQuery.error?.message}</p>}

                    <h3 className="hidden-section-title">Snoozed ({snoozed.length})</h3>
                    {snoozed.length === 0 && <p className="muted small">Nothing snoozed.</p>}
                    {snoozed.map((item) => (
                        <div key={item.target_key} className="hidden-item-row">
                            <span className="hidden-item-name">{item.name || item.target_key}</span>
                            <span className="muted small">until {item.snoozed_until}</span>
                            <button type="button" className="btn btn-secondary btn-sm" onClick={() => onRestore(item, 'UNSNOOZE')}>
                                Restore
                            </button>
                        </div>
                    ))}

                    <h3 className="hidden-section-title">Not interested ({dismissed.length})</h3>
                    {dismissed.length === 0 && <p className="muted small">Nothing dismissed.</p>}
                    {dismissed.map((item) => (
                        <div key={item.target_key} className="hidden-item-row">
                            <span className="hidden-item-name">{item.name || item.target_key}</span>
                            <span className="muted small">{DISMISS_REASON_LABELS[item.dismiss_reason] || item.dismiss_reason}</span>
                            <button type="button" className="btn btn-secondary btn-sm" onClick={() => onRestore(item, 'UNDISMISS')}>
                                Restore
                            </button>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

// ============ Outcomes panel ============

function OutcomesPanel() {
    const [open, setOpen] = useState(false);
    const outcomesQuery = useQuery({
        queryKey: ['recommendationOutcomes'],
        queryFn: () => fetchRecommendationOutcomes({ days: 90 }),
        enabled: open,
        staleTime: 5 * 60 * 1000,
        retry: false,
    });

    const data = outcomesQuery.data;

    return (
        <div className="outcomes-panel">
            <button type="button" className="replay-toggle" onClick={() => setOpen((o) => !o)}>
                {open ? '▾' : '▸'} Advice outcomes (last 90 days)
            </button>
            {open && (
                <div className="outcomes-body">
                    {outcomesQuery.isLoading && <p className="muted small">Loading…</p>}
                    {outcomesQuery.isError && <p className="form-error">{outcomesQuery.error?.message}</p>}
                    {data && (
                        data.nights_imaged < 5 ? (
                            <p className="muted small">Collecting data: {data.nights_imaged} imaged night{data.nights_imaged === 1 ? '' : 's'} so far.</p>
                        ) : (
                            <>
                                <div className="outcomes-summary">
                                    <div className="outcomes-metric">
                                        <span className="muted small">Hero acted-on rate</span>
                                        <span>{data.hero ? `${Math.round(data.hero.rate * 100)}% (${data.hero.acted}/${data.hero.shown})` : '—'}</span>
                                    </div>
                                    <div className="outcomes-metric">
                                        <span className="muted small">Any-pick acted-on rate</span>
                                        <span>{data.any ? `${Math.round(data.any.rate * 100)}% (${data.any.acted}/${data.any.shown})` : '—'}</span>
                                    </div>
                                    <div className="outcomes-metric">
                                        <span className="muted small">Imaged, not shown</span>
                                        <span>{data.imaged_not_shown ?? '—'}</span>
                                    </div>
                                    <div className="outcomes-metric">
                                        <span className="muted small">Self-reported vs confirmed</span>
                                        <span>
                                            {data.self_reported
                                                ? `${data.self_reported.imaged_events} / ${data.self_reported.confirmed_by_library}`
                                                : '—'}
                                        </span>
                                    </div>
                                </div>
                                {data.by_lane && (
                                    <table className="outcomes-lane-table">
                                        <thead>
                                            <tr><th>Lane</th><th>Shown</th><th>Acted</th><th>Rate</th></tr>
                                        </thead>
                                        <tbody>
                                            {Object.entries(data.by_lane).map(([lane, m]) => (
                                                <tr key={lane}>
                                                    <td>{lane}</td>
                                                    <td>{m.shown}</td>
                                                    <td>{m.acted}</td>
                                                    <td>{m.rate != null ? `${Math.round(m.rate * 100)}%` : '—'}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                            </>
                        )
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
    const queryClient = useQueryClient();

    const [selectedDate, setSelectedDate] = useState('');
    const [selectedSiteId, setSelectedSiteId] = useState(null);
    const [rig, setRig] = useState('mounted');
    const [hiddenModalOpen, setHiddenModalOpen] = useState(false);
    const [toast, showToast, dismissToast] = useTonightToast();
    const perLane = 6;

    const equipmentQuery = useQuery({
        queryKey: ['equipment'],
        queryFn: fetchEquipment,
        staleTime: 60 * 1000,
    });
    const sites = equipmentQuery.data?.sites || [];
    const rigs = equipmentQuery.data?.rigs || [];
    const mountedRigs = rigs.filter((r) => r.is_mounted);
    // Fall back to the default (first) site until the viewer picks one explicitly, derived in
    // render rather than an effect (same pattern as Equipment.jsx's SitesTab).
    const siteId = selectedSiteId ?? sites[0]?.id ?? null;

    const recQueryKey = ['recommendations', siteId, rig, selectedDate, perLane];
    const recQuery = useQuery({
        queryKey: recQueryKey,
        queryFn: () => fetchRecommendations({ date: selectedDate || undefined, siteId, rig, perLane }),
        enabled: siteId != null,
        staleTime: 60 * 1000,
        retry: false,
    });

    // R2a §7: every action is optimistic (updates the cached recommendations immediately), sent
    // with its context, and reconciled by invalidating the recommendations/feedback/outcomes
    // queries once the request settles. A toast offers Undo (the inverse action).
    async function sendFeedback(targetKey, action, extra = {}) {
        try {
            await postRecommendationFeedback({
                targetKey,
                action,
                nights: extra.nights,
                reason: extra.reason,
                context: extra.context,
            });
        } catch (err) {
            showToast(`Failed: ${err.message}`, { type: 'error' });
        } finally {
            queryClient.invalidateQueries({ queryKey: ['recommendations'] });
            queryClient.invalidateQueries({ queryKey: ['recommendationFeedback'] });
            queryClient.invalidateQueries({ queryKey: ['recommendationOutcomes'] });
        }
    }

    function inverseAction(action) {
        if (action === 'PIN') return { action: 'UNPIN' };
        if (action === 'UNPIN') return { action: 'PIN' };
        if (action === 'SNOOZE') return { action: 'UNSNOOZE' };
        if (action === 'DISMISS') return { action: 'UNDISMISS' };
        return null;
    }

    function handlePickAction(pick, laneId, rank, action, extra = {}) {
        const name = pick.name || pick.target_key;
        const context = {
            night: effectiveDate || undefined,
            lane: laneId,
            rank,
            score: pick.score,
            rig_id: pick.rig?.id,
        };

        queryClient.setQueryData(recQueryKey, (old) => applyOptimisticFeedback(old, pick.target_key, action));

        const inverse = inverseAction(action);
        function undo() {
            queryClient.setQueryData(recQueryKey, (old) => applyOptimisticFeedback(old, pick.target_key, inverse.action));
            sendFeedback(pick.target_key, inverse.action, { context });
        }

        let message;
        if (action === 'PIN') message = `Pinned ${name}`;
        else if (action === 'UNPIN') message = `Unpinned ${name}`;
        else if (action === 'SNOOZE') message = `Snoozed ${name} for ${SNOOZE_LABELS[extra.nights] || `${extra.nights} nights`}`;
        else if (action === 'DISMISS') message = `Marked ${name} not interested (${DISMISS_REASON_LABELS[extra.reason] || extra.reason})`;
        else if (action === 'IMAGED') message = `Marked ${name} imaged`;

        showToast(message, inverse ? { onUndo: undo } : {});
        sendFeedback(pick.target_key, action, { ...extra, context });
    }

    function handleRestore(item, action) {
        sendFeedback(item.target_key, action, {});
        showToast(`Restored ${item.name || item.target_key}`);
    }

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
    const rigPlan = data?.rig_plan || [];
    const hasAnyPicks = !!data?.hero || nonEmptyLanes.length > 0 || rigPlan.some((e) => e.items?.length > 0);

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
                    <option value="mounted">
                        {mountedRigs.length === 0 ? 'Mounted'
                            : mountedRigs.length === 1 ? `Mounted: ${mountedRigs[0].name}`
                                : `Mounted (${mountedRigs.length} rigs)`}
                    </option>
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
                    <ContextStrip context={context} onOpenHidden={() => setHiddenModalOpen(true)} />

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
                            <HeroCard hero={data.hero} verdict={data.verdict} onAction={handlePickAction} />

                            <RigPlanSection plan={rigPlan} onAction={handlePickAction} />

                            {nonEmptyLanes.map((lane) => (
                                <section key={lane.id} className="lane-section">
                                    <h2 className="section-title">{lane.title}</h2>
                                    <div className="lane-grid">
                                        {lane.items.map((pick, idx) => (
                                            <PickCard
                                                key={`${pick.target_key}-${pick.rig?.id}`}
                                                pick={pick}
                                                laneId={lane.id}
                                                rank={idx + 1}
                                                onAction={handlePickAction}
                                            />
                                        ))}
                                    </div>
                                    {lane.id === 'pinned' && <PinnedUnavailableStrip items={data.pinned_unavailable} />}
                                </section>
                            ))}

                            {!nonEmptyLanes.some((l) => l.id === 'pinned') && (
                                <PinnedUnavailableStrip items={data.pinned_unavailable} />
                            )}
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

            <OutcomesPanel />

            {isAdmin && <ReplayPanel />}

            <Toast toast={toast} onDismiss={dismissToast} />

            {hiddenModalOpen && (
                <HiddenManagerModal
                    onClose={() => setHiddenModalOpen(false)}
                    onRestore={handleRestore}
                />
            )}
        </div>
    );
}
