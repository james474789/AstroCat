import { useId, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    ComposedChart, Scatter, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
    ReferenceLine, ReferenceArea, CartesianGrid,
} from 'recharts';
import { useQualityUnits } from '../../context/QualityUnitsContext';
import { filterColor } from '../../utils/filterColors';
import './Quality.css';

// Q1c session timeline (docs/design/20260927-Q1-star-quality.md §8.2): FWHM/HFR over a
// night with synced small panels (altitude, stars, eccentricity) sharing one
// time axis. Each panel has one y-axis; filters are colour AND marker shape.

const SHAPES = ['circle', 'square', 'triangle', 'diamond', 'star', 'cross', 'wye'];
const FILTER_ORDER = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII', 'Hb', 'Duo', 'None'];
// Autofocus runs are frequent, so they're unlabelled dashed lines (see footnote);
// rarer events get a short label.
const EVENT_STYLE = {
    AUTOFOCUS: { label: null, dash: '3 3' },
    MERIDIAN_FLIP: { label: 'Flip', dash: undefined },
    GAP: { label: 'Gap', dash: '1 3' },
    TARGET_CHANGE: { label: 'Target', dash: '6 3' },
};
const BREAK_MS = 20 * 60 * 1000;   // lines break across gaps longer than this

// Insert a null after any gap > BREAK_MS so lines don't bridge pauses.
function withBreaks(rows, key) {
    const out = [];
    rows.forEach((r, i) => {
        if (i > 0 && r.x - rows[i - 1].x > BREAK_MS) out.push({ x: (r.x + rows[i - 1].x) / 2, [key]: null });
        out.push(r);
    });
    return out;
}
const FLAG_LABELS = { SOFT: 'Soft (FWHM well above the night’s median)', CLOUD: 'Few stars (cloud?)', TRAILED: 'Elongated stars' };

const METRICS = {
    fwhm: { label: 'FWHM', size: true },
    hfr: { label: 'HFR', size: true },
    eccentricity: { label: 'Eccentricity', size: false, digits: 2 },
    star_count: { label: 'Stars', size: false, digits: 0 },
    bkg_adu: { label: 'Sky background', size: false, digits: 0, unit: ' ADU' },
};

const toMs = (iso) => (iso ? Date.parse(iso.endsWith('Z') ? iso : `${iso}Z`) : null);

function timeFormatter(tz) {
    let fmt;
    try {
        fmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz || 'UTC' });
    } catch {
        fmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' });
    }
    return (ms) => fmt.format(new Date(ms));
}

// Value of a point in the chosen metric/units; zenith-normalised sizes use FWHM ∝ airmass^0.6.
function metricValue(p, metric, units, normalize) {
    if (METRICS[metric].size) {
        const px = metric === 'fwhm' ? p.fwhm_px : p.hfr_px;
        const arc = metric === 'fwhm' ? p.fwhm_arcsec : p.hfr_arcsec;
        let v = units === 'ARCSEC' && arc != null ? arc : px;
        if (v == null) return null;
        if (normalize && p.airmass) v /= p.airmass ** 0.6;
        return v;
    }
    return p[metric] ?? null;
}

function rollingMedian(values, window = 5) {
    const half = Math.floor(window / 2);
    return values.map((_, i) => {
        const slice = values.slice(Math.max(0, i - half), i + half + 1).filter((v) => v != null).sort((a, b) => a - b);
        if (!slice.length) return null;
        const m = Math.floor(slice.length / 2);
        return slice.length % 2 ? slice[m] : (slice[m - 1] + slice[m]) / 2;
    });
}

function SubTooltip({ active, payload, fmtTime, unitLabel, metric }) {
    if (!active || !payload?.length) return null;
    const p = payload.find((x) => x.payload?.point)?.payload?.point;
    if (!p) return null;
    const m = METRICS[metric];
    const v = payload.find((x) => x.payload?.point)?.payload?.y;
    return (
        <div className="session-tooltip">
            <div className="session-tooltip-value">
                {v != null ? `${Number(v).toFixed(m.digits ?? 2)}${m.size ? unitLabel : (m.unit || '')}` : '—'}
                <span> {m.label}</span>
            </div>
            <div>{fmtTime(toMs(p.t))} · {p.filter} · {p.exposure_s ? `${p.exposure_s}s` : ''}</div>
            {p.target_key && <div>{p.target_key}</div>}
            <div className="session-tooltip-muted">
                {p.star_count != null && `${p.star_count} stars`}
                {p.eccentricity != null && ` · ecc ${p.eccentricity.toFixed(2)}`}
                {p.alt_deg != null && ` · alt ${Math.round(p.alt_deg)}°`}
                {p.guide_rms != null && ` · guide ${p.guide_rms}″`}
            </div>
            {p.flag && <div className="session-tooltip-flag">⚠ {FLAG_LABELS[p.flag] || p.flag}</div>}
            <div className="session-tooltip-muted">Click to open</div>
        </div>
    );
}

function SmallPanel({ data, dataKey, label, domain, xDomain, fmtTime, events, syncId, digits = 0, unit = '', height = 80, color }) {
    const has = data.some((d) => d[dataKey] != null);
    if (!has) return null;
    return (
        <div className="session-panel">
            <div className="session-panel-label">{label}</div>
            <ResponsiveContainer width="100%" height={height}>
                <ComposedChart data={data} syncId={syncId} syncMethod="value" margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
                    <CartesianGrid vertical={false} stroke="var(--color-border)" strokeOpacity={0.5} />
                    <XAxis dataKey="x" type="number" domain={xDomain} hide />
                    <YAxis width={52} domain={domain} tickCount={3} fontSize={10} stroke="var(--color-text-muted)"
                        tickFormatter={(v) => `${Number(v).toFixed(digits)}${unit}`} />
                    <Tooltip cursor={{ stroke: 'var(--color-text-muted)' }}
                        content={({ active, payload }) => (active && payload?.length && payload[0].value != null ? (
                            <div className="session-tooltip">
                                <div className="session-tooltip-value">{Number(payload[0].value).toFixed(digits)}{unit}<span> {label}</span></div>
                                <div>{fmtTime(payload[0].payload.x)}</div>
                            </div>
                        ) : null)} />
                    {events.map((e, i) => (
                        <ReferenceLine key={i} x={e.x} stroke="var(--color-text-muted)" strokeOpacity={0.5}
                            strokeDasharray={EVENT_STYLE[e.type]?.dash} />
                    ))}
                    <Line dataKey={dataKey} type="linear" stroke={color || 'var(--color-text-secondary)'} strokeWidth={2}
                        dot={false} isAnimationActive={false} />
                </ComposedChart>
            </ResponsiveContainer>
        </div>
    );
}

export default function SessionQualityChart({
    timeline, rigId = 'ALL', compact = false, highlightId = null, height = 280, metric: metricProp,
}) {
    const navigate = useNavigate();
    const { units } = useQualityUnits();
    const [metric, setMetric] = useState(metricProp || 'fwhm');
    const [normalize, setNormalize] = useState(false);
    const [hidden, setHidden] = useState(() => new Set());
    const syncId = `session-${useId()}`;
    const fmtTime = useMemo(() => timeFormatter(timeline?.site?.timezone), [timeline]);

    const points = useMemo(() => (timeline?.points || [])
        .filter((p) => rigId === 'ALL' || p.rig_id === rigId)
        .map((p) => ({ ...p, x: toMs(p.t) }))
        .filter((p) => p.x != null), [timeline, rigId]);

    const filters = useMemo(() => {
        const set = [...new Set(points.map((p) => p.filter))];
        return set.sort((a, b) => {
            const ia = FILTER_ORDER.indexOf(a); const ib = FILTER_ORDER.indexOf(b);
            return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || String(a).localeCompare(String(b));
        });
    }, [points]);
    const shapeOf = (f) => SHAPES[filters.indexOf(f) % SHAPES.length];

    const anyArcsec = points.some((p) => (metric === 'hfr' ? p.hfr_arcsec : p.fwhm_arcsec) != null);
    const unitLabel = units === 'ARCSEC' && anyArcsec ? '″' : ' px';

    const series = useMemo(() => filters.map((f) => {
        // Only measured subs: the running median must not invent values for unmeasured ones.
        const dots = points.filter((p) => p.filter === f)
            .map((p) => ({ x: p.x, y: metricValue(p, metric, units, normalize), point: p }))
            .filter((d) => d.y != null);
        const med = rollingMedian(dots.map((d) => d.y));
        return {
            filter: f,
            dots,
            trend: withBreaks(dots.map((d, i) => ({ x: d.x, trend: med[i] })), 'trend'),
        };
    }), [filters, points, metric, units, normalize]);

    const xDomain = useMemo(() => {
        if (!points.length) return [0, 1];
        const lo = points[0].x; const hi = points[points.length - 1].x + (points[points.length - 1].exposure_s || 0) * 1000;
        const pad = Math.max(5 * 60 * 1000, (hi - lo) * 0.03);
        return [lo - pad, hi + pad];
    }, [points]);

    const events = useMemo(() => (timeline?.events || [])
        .filter((e) => (rigId === 'ALL' || e.rig_id === rigId) && EVENT_STYLE[e.type])
        .map((e) => ({ ...e, x: toMs(e.t) })), [timeline, rigId]);

    const darkStart = toMs(timeline?.dark?.start);
    const darkEnd = toMs(timeline?.dark?.end);
    // Altitude is known for every sub; stars/eccentricity only for measured ones, so
    // those lines skip unmeasured subs, and all of them break across long pauses.
    const panels = useMemo(() => {
        const make = (key, pick) => withBreaks(points.map((p) => ({ x: p.x, [key]: pick(p) })).filter((r) => r[key] != null), key);
        return {
            alt: make('alt', (p) => p.alt_deg),
            stars: make('stars', (p) => p.star_count),
            ecc: make('ecc', (p) => p.eccentricity),
        };
    }, [points]);

    if (!points.length) return <p className="quality-note">No subs for this selection.</p>;
    const measured = series.some((s) => s.dots.length);
    const highlight = highlightId != null ? points.find((p) => p.image_id === highlightId) : null;
    const highlightY = highlight ? metricValue(highlight, metric, units, normalize) : null;
    const m = METRICS[metric];
    const ticks = (() => {
        const [lo, hi] = xDomain;
        const hour = 3600 * 1000;
        const step = hi - lo > 8 * hour ? 2 * hour : hi - lo > 3 * hour ? hour : 30 * 60 * 1000;
        const out = [];
        for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) out.push(t);
        return out;
    })();

    return (
        <div className={`session-chart${compact ? ' compact' : ''}`}>
            {!compact && (
                <div className="session-controls">
                    <div className="session-metric" role="group" aria-label="Metric">
                        {Object.entries(METRICS).map(([key, def]) => (
                            <button key={key} type="button" className={metric === key ? 'active' : ''} aria-pressed={metric === key}
                                onClick={() => setMetric(key)}>{def.label}</button>
                        ))}
                    </div>
                    {m.size && (
                        <label className="session-normalize" title="Divide by airmass^0.6 so changes in seeing aren’t confused with the target sinking">
                            <input type="checkbox" checked={normalize} onChange={(e) => setNormalize(e.target.checked)} /> Normalise to zenith
                        </label>
                    )}
                    <div className="session-legend" role="group" aria-label="Filters">
                        {filters.map((f) => (
                            <button key={f} type="button" className={`session-chip${hidden.has(f) ? ' off' : ''}`} aria-pressed={!hidden.has(f)}
                                onClick={() => setHidden((prev) => { const n = new Set(prev); if (n.has(f)) n.delete(f); else n.add(f); return n; })}>
                                <svg width="12" height="12" aria-hidden="true"><FilterGlyph shape={shapeOf(f)} color={filterColor(f)} /></svg>
                                {f}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {!measured && <p className="quality-note">None of these subs has been measured yet.</p>}

            <div className="session-panel">
                {!compact && (
                    <div className="session-panel-label">
                        {m.label}{m.size ? ` (${unitLabel.trim()})` : ''}{normalize && m.size ? ', at zenith' : ''}
                    </div>
                )}
                <ResponsiveContainer width="100%" height={compact ? 120 : height}>
                    <ComposedChart syncId={compact ? undefined : syncId} syncMethod="value" margin={{ top: 14, right: 16, bottom: 0, left: 0 }}>
                        <CartesianGrid vertical={false} stroke="var(--color-border)" strokeOpacity={0.5} />
                        {darkStart != null && darkStart > xDomain[0] && (
                            <ReferenceArea x1={xDomain[0]} x2={Math.min(darkStart, xDomain[1])} fill="var(--color-text-muted)" fillOpacity={0.08} ifOverflow="hidden" />
                        )}
                        {darkEnd != null && darkEnd < xDomain[1] && (
                            <ReferenceArea x1={Math.max(darkEnd, xDomain[0])} x2={xDomain[1]} fill="var(--color-text-muted)" fillOpacity={0.08} ifOverflow="hidden" />
                        )}
                        <XAxis dataKey="x" type="number" domain={xDomain} ticks={ticks} tickFormatter={fmtTime}
                            fontSize={11} stroke="var(--color-text-muted)" allowDuplicatedCategory={false} />
                        <YAxis width={52} domain={['auto', 'auto']} fontSize={11} stroke="var(--color-text-muted)"
                            tickFormatter={(v) => (m.size ? `${Number(v).toFixed(1)}${unitLabel.trim() === 'px' ? '' : '″'}` : Number(v).toFixed(m.digits ?? 0))} />
                        <Tooltip cursor={{ strokeDasharray: '3 3', stroke: 'var(--color-text-muted)' }}
                            content={<SubTooltip fmtTime={fmtTime} unitLabel={unitLabel} metric={metric} />} />
                        {!compact && events.map((e, i) => (
                            <ReferenceLine key={i} x={e.x} stroke="var(--color-text-muted)" strokeOpacity={0.6}
                                strokeDasharray={EVENT_STYLE[e.type].dash}
                                label={EVENT_STYLE[e.type].label
                                    ? { value: EVENT_STYLE[e.type].label, position: 'top', fontSize: 10, fill: 'var(--color-text-muted)' }
                                    : undefined} />
                        ))}
                        {series.filter((s) => !hidden.has(s.filter)).map((s) => (
                            <Line key={`t-${s.filter}`} data={s.trend} dataKey="trend" type="linear" stroke={filterColor(s.filter)}
                                strokeOpacity={0.55} strokeWidth={2} dot={false} activeDot={false} isAnimationActive={false}
                                legendType="none" tooltipType="none" />
                        ))}
                        {series.filter((s) => !hidden.has(s.filter)).map((s) => (
                            <Scatter key={`s-${s.filter}`} name={s.filter} data={s.dots} dataKey="y" isAnimationActive={false}
                                shape={(props) => <SubDot {...props} shape={shapeOf(s.filter)} color={filterColor(s.filter)} />}
                                onClick={(d) => d?.point && navigate(`/images/${d.point.image_id}`)} />
                        ))}
                        {highlight && highlightY != null && (
                            <ReferenceLine x={highlight.x} stroke="var(--color-primary)" strokeWidth={2} strokeOpacity={0.8}
                                label={{ value: 'this sub', position: 'top', fontSize: 10, fill: 'var(--color-text-secondary)' }} />
                        )}
                    </ComposedChart>
                </ResponsiveContainer>
            </div>

            {!compact && (
                <>
                    <SmallPanel data={panels.alt} dataKey="alt" label="Altitude" unit="°" domain={[0, 90]} xDomain={xDomain}
                        fmtTime={fmtTime} events={events} syncId={syncId} />
                    <SmallPanel data={panels.stars} dataKey="stars" label="Stars (cloud shows as a dip)" domain={[0, 'auto']} xDomain={xDomain}
                        fmtTime={fmtTime} events={events} syncId={syncId} />
                    <SmallPanel data={panels.ecc} dataKey="ecc" label="Eccentricity (wind / guiding)" digits={2} domain={[0, 1]} xDomain={xDomain}
                        fmtTime={fmtTime} events={events} syncId={syncId} />
                    <p className="session-footnote">
                        Times in {timeline?.site?.timezone || 'UTC'}{timeline?.site?.name ? ` (${timeline.site.name})` : ''}.
                        Shaded: sky not fully dark. Short-dashed lines: autofocus runs; labelled lines: meridian flip, gap, target change.
                        Faint lines are each filter’s running median.
                    </p>
                </>
            )}
        </div>
    );
}

function FilterGlyph({ shape, color, cx = 6, cy = 6, r = 4.5 }) {
    const common = { fill: color, stroke: 'var(--color-surface)', strokeWidth: 1 };
    switch (shape) {
        case 'square': return <rect x={cx - r} y={cy - r} width={2 * r} height={2 * r} rx={1} {...common} />;
        case 'triangle': return <polygon points={`${cx},${cy - r} ${cx + r},${cy + r} ${cx - r},${cy + r}`} {...common} />;
        case 'diamond': return <polygon points={`${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}`} {...common} />;
        case 'star': return <polygon points={starPoints(cx, cy, r)} {...common} />;
        case 'cross': return <path d={`M${cx - r},${cy} H${cx + r} M${cx},${cy - r} V${cy + r}`} stroke={color} strokeWidth={2.5} />;
        case 'wye': return <path d={`M${cx},${cy} L${cx},${cy + r} M${cx},${cy} L${cx - r},${cy - r} M${cx},${cy} L${cx + r},${cy - r}`} stroke={color} strokeWidth={2.5} />;
        default: return <circle cx={cx} cy={cy} r={r} {...common} />;
    }
}

function starPoints(cx, cy, r) {
    const pts = [];
    for (let i = 0; i < 10; i += 1) {
        const rad = i % 2 ? r * 0.45 : r;
        const a = (Math.PI / 5) * i - Math.PI / 2;
        pts.push(`${cx + rad * Math.cos(a)},${cy + rad * Math.sin(a)}`);
    }
    return pts.join(' ');
}

// Scatter mark: 9px glyph, flagged subs get a ring, and a transparent 24px hit target.
function SubDot({ cx, cy, shape, color, payload }) {
    if (cx == null || cy == null) return null;
    const flagged = payload?.point?.flag;
    return (
        <g style={{ cursor: 'pointer' }}>
            <circle cx={cx} cy={cy} r={12} fill="transparent" />
            {flagged && <circle cx={cx} cy={cy} r={7.5} fill="none" stroke="var(--color-warning)" strokeWidth={2} />}
            <FilterGlyph shape={shape} color={color} cx={cx} cy={cy} r={4.5} />
        </g>
    );
}
