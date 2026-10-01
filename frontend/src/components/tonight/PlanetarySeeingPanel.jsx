import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, ReferenceArea } from 'recharts';
import { ChevronDown, Orbit, Check, X, Minus, AlertTriangle } from 'lucide-react';
import { getSeeingForecast } from '../../api/client';
import './PlanetarySeeingPanel.css';

// Design: docs/design/S1-planetary-seeing-forecast.md §7.2. The panel owns its query and its error
// boundary, so a failing forecast never affects the rest of the Tonight page.

const COLLAPSE_KEY = 'astrocat.tonight.seeing.collapsed';

const VERDICT_LABELS = { GO: 'GO', MAYBE: 'MAYBE', NO_GO: 'NO GO', CLOUD: 'CLOUD' };

const MODEL_LABELS = {
    ecmwf_ifs025: 'ECMWF',
    icon_seamless: 'ICON',
    gfs_seamless: 'GFS',
};

const WARNING_LABELS = {
    dew_risk: 'Dew risk',
    gusty: 'Gusty',
    low_dispersion: 'Low: dispersion (use an ADC)',
    jet_overhead: 'Jet overhead',
    models_disagree: 'Models disagree',
};

const NOTE_LABELS = { imaging_in_daylight: 'Imaging in daylight' };

const REASON_LABELS = {
    no_data: 'Forecast unavailable',
    out_of_range: 'No forecast for this date (7 days ahead at most)',
    disabled: 'The seeing forecast is turned off',
};

const CHECK_LABELS = {
    surface_wind: 'Surface wind ≤ 1.5 m/s',
    temp_humidity: 'Cold and humid (≤ 3 °C, RH ≥ 85%) or shallow boundary layer',
    upper_cloud: 'Mid + high cloud ≤ 20%',
    seeing_index: 'meteoblue seeing index ≥ 4',
    planet_altitude: 'Planet ≥ 35° at its best',
};

// label, unit, digits for each hourly factor row (value shown on hover).
const FACTOR_ROWS = [
    { key: 'surface_wind', label: 'Surface wind', unit: 'm/s', digits: 1 },
    { key: 'jet', label: 'Jet / upper wind', unit: 'm/s', digits: 0 },
    { key: 'upper_cloud', label: 'Mid + high cloud', unit: '%', digits: 0 },
    { key: 'stability', label: 'Temp / RH', unit: '°C', digits: 1 },
    { key: 'ground_shear', label: 'Ground shear', unit: 'm/s', digits: 1 },
    { key: 'seeing_index', label: 'meteoblue index 1', unit: '', digits: 0 },
    { key: 'arcsec', label: 'meteoblue ″', unit: '″', digits: 1, shownOnly: true },
    { key: 'computed_seeing', label: 'Model ″ (experimental)', unit: '″', digits: 1, shownOnly: true },
];

function readCollapsed() {
    try {
        return window.localStorage.getItem(COLLAPSE_KEY) === '1';
    } catch {
        return false;
    }
}

function writeCollapsed(value) {
    try {
        window.localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0');
    } catch {
        // storage unavailable: the state just isn't remembered
    }
}

function titleCase(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function modelLabel(m) {
    return MODEL_LABELS[m] || m.replace(/_/g, ' ');
}

function fmtTime(iso, timeZone) {
    try {
        return new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false })
            .format(new Date(iso));
    } catch {
        return '—';
    }
}

function formatLocalRange(startIso, endIso, timeZone) {
    if (!startIso || !endIso) return '—';
    return `${fmtTime(startIso, timeZone)}–${fmtTime(endIso, timeZone)} local`;
}

function formatUTCRange(startIso, endIso) {
    if (!startIso || !endIso) return '';
    const f = (iso) => new Date(iso).toISOString().slice(11, 16);
    return `${f(startIso)}–${f(endIso)} UTC`;
}

function ageText(iso) {
    if (!iso) return null;
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    return hrs < 48 ? `${hrs} h ago` : `${Math.round(hrs / 24)} d ago`;
}

function heat(score) {
    if (score == null) return 'var(--color-surface-elevated)';
    const s = Math.max(0, Math.min(1, score));
    return `hsl(${Math.round(s * 120)} 55% 36%)`;
}

function fmtValue(v, digits, unit) {
    if (v == null) return 'n/a';
    return `${Number(v).toFixed(digits)}${unit ? ` ${unit}` : ''}`;
}

function pct(v) {
    return v == null ? '—' : `${Math.round(v * 100)}%`;
}

// ---------------------------------------------------------------------------------------------

function Header({ data, collapsed, onToggle }) {
    const overall = data?.overall;
    const tz = data?.site?.timezone || 'UTC';
    const best = overall?.best_body ? data.bodies?.find((b) => b.body === overall.best_body) : null;
    const win = best?.window;
    return (
        <button type="button" className="seeing-header" onClick={onToggle} aria-expanded={!collapsed}>
            <Orbit size={18} className="seeing-header-icon" />
            <span className="seeing-title">Planetary seeing</span>
            {overall && data.available && (
                <>
                    <span className={`seeing-grade grade-${overall.grade}`} title="Forecast seeing grade (VVP to VG)">
                        {overall.grade}
                    </span>
                    <span className={`seeing-verdict verdict-${overall.verdict}`}>{VERDICT_LABELS[overall.verdict] || overall.verdict}</span>
                    <span className="muted small">confidence {pct(overall.confidence)}</span>
                    {best && win && (
                        <span className="seeing-best small">
                            best: {titleCase(best.body)} {fmtTime(win.start_utc, tz)}–{fmtTime(win.end_utc, tz)}
                        </span>
                    )}
                </>
            )}
            <ChevronDown size={18} className={`seeing-chevron ${collapsed ? 'collapsed' : ''}`} />
        </button>
    );
}

function BodySparkline({ body }) {
    const track = body.track || [];
    if (track.length === 0) return <div className="seeing-spark-empty" />;
    const data = track.map((p) => ({ t: p.t, alt: p.alt, score: p.score }));
    const w = body.window;
    return (
        <ResponsiveContainer width="100%" height={52}>
            <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                {w && <ReferenceArea yAxisId="alt" x1={w.start_utc} x2={w.end_utc} fill="var(--color-primary)" fillOpacity={0.12} strokeOpacity={0} />}
                <XAxis dataKey="t" hide />
                <YAxis yAxisId="alt" domain={[0, 90]} hide />
                <YAxis yAxisId="score" domain={[0, 1]} hide orientation="right" />
                <Line yAxisId="alt" type="monotone" dataKey="alt" stroke="var(--color-primary)" strokeWidth={1.75} dot={false} isAnimationActive={false} name="Altitude" />
                <Line yAxisId="score" type="monotone" dataKey="score" stroke="var(--color-accent)" strokeWidth={1.5} dot={false} isAnimationActive={false} name="Score" />
            </LineChart>
        </ResponsiveContainer>
    );
}

function PlanetRows({ bodies, tz }) {
    if (!bodies || bodies.length === 0) {
        return <p className="muted small seeing-empty">No planet is up while the Sun is below −6° on this night.</p>;
    }
    return (
        <div className="seeing-planets">
            {bodies.map((b) => {
                const w = b.window;
                return (
                    <div key={b.body} className="seeing-planet">
                        <div className="seeing-planet-name">
                            <strong>{titleCase(b.body)}</strong>
                            <span className="muted small">
                                {b.diameter_arcsec != null ? `${b.body === 'moon' ? Math.round(b.diameter_arcsec / 60) + '′' : b.diameter_arcsec + '″'}` : ''}
                                {b.illum != null ? ` · ${Math.round(b.illum * 100)}% lit` : ''}
                            </span>
                        </div>
                        <div className="seeing-planet-window">
                            {w ? (
                                <>
                                    <span>{formatLocalRange(w.start_utc, w.end_utc, tz)}</span>
                                    <span className="muted small">{formatUTCRange(w.start_utc, w.end_utc)}</span>
                                </>
                            ) : (
                                <span className="muted">{b.cloud_blocked ? 'Cloud' : 'No usable window'}</span>
                            )}
                        </div>
                        <div className="seeing-planet-peak">
                            {w ? (
                                <>
                                    <span>{Math.round(w.peak_alt)}° at {fmtTime(w.peak_utc, tz)}</span>
                                    <span className="muted small">score {w.score != null ? w.score.toFixed(2) : '—'}</span>
                                </>
                            ) : null}
                        </div>
                        <div className="seeing-planet-spark"><BodySparkline body={b} /></div>
                        <div className="seeing-chips">
                            {b.cloud_blocked && <span className="seeing-chip warn">Cloud</span>}
                            {(b.warnings || []).map((k) => (
                                <span key={k} className="seeing-chip warn"><AlertTriangle size={11} /> {WARNING_LABELS[k] || k}</span>
                            ))}
                            {(b.notes || []).map((k) => (
                                <span key={k} className="seeing-chip">{NOTE_LABELS[k] || k}</span>
                            ))}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

function modelScoresTitle(h) {
    const parts = Object.entries(h.per_model || {}).map(([m, s]) => `${modelLabel(m)} ${s == null ? 'n/a' : s.toFixed(2)}`);
    return parts.join(' · ');
}

function factorTitle(row, f, hour, tz) {
    const when = fmtTime(hour.t, tz);
    let value = fmtValue(f?.value, row.digits, row.unit);
    if (row.key === 'stability' && f?.value != null) {
        value = `${fmtValue(f.value, 1, '°C')}, RH ${f.rh == null ? 'n/a' : `${Math.round(f.rh)}%`}`;
    }
    const score = f?.score != null ? ` · score ${f.score.toFixed(2)}` : '';
    return `${when} ${row.label}: ${value}${score}\n${modelScoresTitle(hour)}`;
}

function HourlyStrip({ hourly, tz }) {
    const [open, setOpen] = useState(false);
    if (!hourly || hourly.length === 0) return null;
    const labelEvery = 3;
    return (
        <div className="seeing-hourly">
            <div className="seeing-hourly-head">
                <span className="muted small">Hourly score (local time)</span>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
                    {open ? 'Hide factors' : 'Show factors'}
                </button>
            </div>
            <div className="seeing-strip" style={{ gridTemplateColumns: `repeat(${hourly.length}, minmax(0, 1fr))` }}>
                {hourly.map((h) => (
                    <div
                        key={h.t}
                        className="seeing-cell"
                        style={{ background: heat(h.score), opacity: h.confidence != null ? 0.45 + 0.55 * h.confidence : 1 }}
                        title={`${fmtTime(h.t, tz)} score ${h.score == null ? 'n/a' : h.score.toFixed(2)} · confidence ${pct(h.confidence)} · clear ${pct(h.clear)}\n${modelScoresTitle(h)}`}
                    />
                ))}
            </div>
            <div className="seeing-strip seeing-ticks" style={{ gridTemplateColumns: `repeat(${hourly.length}, minmax(0, 1fr))` }}>
                {hourly.map((h, i) => (
                    <span key={h.t} className="seeing-tick">{i % labelEvery === 0 ? fmtTime(h.t, tz).slice(0, 2) : ''}</span>
                ))}
            </div>
            {open && (
                <div className="seeing-factors">
                    {FACTOR_ROWS.map((row) => (
                        <div key={row.key} className="seeing-factor-row">
                            <span className="seeing-factor-label small">{row.label}</span>
                            <div className="seeing-strip" style={{ gridTemplateColumns: `repeat(${hourly.length}, minmax(0, 1fr))` }}>
                                {hourly.map((h) => {
                                    const f = h.factors?.[row.key];
                                    return (
                                        <div
                                            key={h.t}
                                            className={`seeing-cell small-cell ${row.shownOnly ? 'shown-only' : ''}`}
                                            style={{ background: row.shownOnly ? undefined : heat(f?.score) }}
                                            title={factorTitle(row, f, h, tz)}
                                        />
                                    );
                                })}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

function Checklist({ items }) {
    if (!items || items.length === 0) return null;
    return (
        <div className="seeing-checklist">
            <span className="muted small">Go checklist (best window)</span>
            <ul>
                {items.map((c) => (
                    <li key={c.key} className={c.pass === true ? 'ok' : c.pass === false ? 'bad' : 'na'}>
                        {c.pass === true ? <Check size={14} /> : c.pass === false ? <X size={14} /> : <Minus size={14} />}
                        <span>{CHECK_LABELS[c.key] || c.key}</span>
                        {c.pass == null && <span className="muted small">n/a</span>}
                    </li>
                ))}
            </ul>
        </div>
    );
}

function SourceChips({ data }) {
    const sources = data?.sources || [];
    if (sources.length === 0) return null;
    return (
        <div className="seeing-sources">
            {sources.map((s) => {
                if (s.id === 'open_meteo') {
                    const models = (s.models || []).map(modelLabel);
                    const age = ageText(data.fetched_at);
                    return (
                        <span key={s.id} className={`seeing-chip ${s.status === 'error' ? 'warn' : ''}`}>
                            {s.label}{models.length ? `: ${models.join(', ')}` : ''}
                            {age ? ` · updated ${age}` : ''}
                            {s.status === 'error' && s.message ? ` · ${s.message}` : ''}
                        </span>
                    );
                }
                const state = s.status === 'no_key' ? 'not configured' : s.status === 'ok' ? 'ok' : `error${s.message ? `: ${s.message}` : ''}`;
                return (
                    <span key={s.id} className={`seeing-chip ${s.status === 'error' ? 'warn' : s.status === 'no_key' ? 'muted' : ''}`}>
                        {s.label || s.id}: {state}
                    </span>
                );
            })}
        </div>
    );
}

function PanelBody({ query }) {
    const data = query.data;
    if (query.isLoading) {
        return <p className="muted small seeing-empty">Loading the seeing forecast…</p>;
    }
    if (query.isError) {
        return <p className="muted small seeing-empty">Seeing forecast unavailable: {query.error?.message || 'request failed'}</p>;
    }
    if (!data) return null;
    const tz = data.site?.timezone || 'UTC';
    if (data.available === false) {
        return (
            <>
                <p className="muted small seeing-empty">{REASON_LABELS[data.reason] || 'Forecast unavailable'}</p>
                <SourceChips data={data} />
            </>
        );
    }
    return (
        <>
            {data.stale && (
                <div className="seeing-stale">
                    <AlertTriangle size={14} /> Showing older forecast data (the weather source could not be reached
                    {data.fetched_at ? `; last updated ${ageText(data.fetched_at)}` : ''}).
                </div>
            )}
            <PlanetRows bodies={data.bodies} tz={tz} />
            <HourlyStrip hourly={data.hourly} tz={tz} />
            <Checklist items={data.overall?.checklist} />
            <SourceChips data={data} />
        </>
    );
}

class SeeingBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { failed: false };
    }

    static getDerivedStateFromError() {
        return { failed: true };
    }

    componentDidCatch(error) {
        console.error('Planetary seeing panel failed:', error);
    }

    render() {
        if (this.state.failed) {
            return (
                <section className="seeing-panel">
                    <p className="muted small seeing-empty">The planetary seeing panel could not be displayed.</p>
                </section>
            );
        }
        return this.props.children;
    }
}

function SeeingPanelInner({ siteId, date }) {
    const [collapsed, setCollapsed] = useState(readCollapsed);
    const query = useQuery({
        queryKey: ['seeingForecast', siteId, date || null],
        queryFn: () => getSeeingForecast(siteId, date || undefined),
        enabled: siteId != null,
        staleTime: 10 * 60 * 1000,
        retry: false,
    });

    if (siteId == null) return null;
    // A turned-off forecast has nothing to show: stay out of the page's way.
    if (query.data?.available === false && query.data?.reason === 'disabled') return null;

    function toggle() {
        setCollapsed((prev) => {
            writeCollapsed(!prev);
            return !prev;
        });
    }

    return (
        <section className="seeing-panel">
            <Header data={query.data} collapsed={collapsed} onToggle={toggle} />
            {!collapsed && <div className="seeing-body"><PanelBody query={query} /></div>}
        </section>
    );
}

export default function PlanetarySeeingPanel(props) {
    return (
        <SeeingBoundary>
            <SeeingPanelInner {...props} />
        </SeeingBoundary>
    );
}
