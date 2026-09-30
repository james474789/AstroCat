import { useEffect, useState } from 'react';
import {
    BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell, LabelList,
} from 'recharts';
import { fetchQualityStats } from '../../api/client';
import { useQualityUnits } from '../../context/QualityUnitsContext';
import { filterColor } from '../../utils/filterColors';
import { TOOLTIP_STYLE, AXIS, BAR } from './chartStyle';
import QualityUnitsToggle from './QualityUnitsToggle';
import './Quality.css';

// Q1d "Star Quality" section of FITS Analytics (docs/design/Q1-star-quality.md §8.5).
// One rig at a time (FWHM only compares within a rig); every chart has one y-axis.


function Card({ title, note, children, empty }) {
    return (
        <div className="chart-card">
            <div className="chart-header"><h3 className="chart-title">{title}</h3></div>
            {empty ? <p className="quality-note">{empty}</p> : children}
            {note && !empty && <p className="quality-note" style={{ marginTop: '0.5rem' }}>{note}</p>}
        </div>
    );
}

export default function QualityStatsSection() {
    const { units } = useQualityUnits();
    const [rigId, setRigId] = useState('');
    const [result, setResult] = useState({ key: null, data: null, error: null });
    const requestKey = `${rigId}|${units}`;

    useEffect(() => {
        let cancelled = false;
        const key = `${rigId}|${units}`;
        fetchQualityStats({ rig_id: rigId || undefined, units })
            .then((data) => { if (!cancelled) setResult({ key, data, error: null }); })
            .catch((e) => { if (!cancelled) setResult({ key, data: null, error: e.message }); });
        return () => { cancelled = true; };
    }, [rigId, units]);

    const d = result.data;
    const loading = result.key !== requestKey;
    const u = d?.units === 'PX' ? ' px' : '″';
    const fmt = (v, digits = 2) => (v == null ? '—' : `${Number(v).toFixed(digits)}${u}`);
    const pxNote = d && units === 'ARCSEC' && d.units === 'PX' ? 'Most of this rig’s subs have no plate scale, so sizes are in pixels.' : null;
    const hist = (d?.histogram || []).map((b) => ({ ...b, label: Number(((b.from + b.to) / 2).toFixed(2)) }));
    const medianBin = d?.median != null && hist.length
        ? hist.reduce((a, b) => (Math.abs(b.label - d.median) < Math.abs(a.label - d.median) ? b : a))
        : null;
    const filters = d?.filters || [];
    const temp = d?.temperature;
    const slopeNote = temp?.slope_px_per_degc != null
        ? `HFR changes ${temp.slope_px_per_degc > 0 ? '+' : ''}${temp.slope_px_per_degc.toFixed(3)} px per °C. `
          + (Math.abs(temp.slope_px_per_degc) >= 0.03
              ? `Focus drifts noticeably with temperature: refocus after a ${Math.max(1, Math.round(0.3 / Math.abs(temp.slope_px_per_degc)))} °C change, or use temperature compensation.`
              : 'Focus is stable with temperature.')
        : 'Needs focuser temperatures (FOCTEMP header or N.I.N.A. FTemp filename token) spanning at least 2 °C.';
    // Rig dropdown: sharpest (lowest median FWHM) first; rigs without a median go last.
    const fwhmByRig = new Map((d?.by_rig || []).map((r) => [r.rig_id, r.fwhm_median]));
    const rigsByFwhm = [...(d?.rigs || [])].sort((a, b) => {
        const fa = fwhmByRig.get(a.rig_id), fb = fwhmByRig.get(b.rig_id);
        if (fa == null || fb == null) return (fa == null) - (fb == null);
        return fa - fb;
    });
    const worstFilter =filters.filter((f) => !f.reference).sort((a, b) => Math.abs(b.offset_pct) - Math.abs(a.offset_pct))[0];

    return (
        <section className="quality-stats">
            <div className="quality-stats-header">
                <div>
                    <h2 className="section-title" style={{ margin: 0 }}>Star Quality</h2>
                    <p className="quality-note" style={{ margin: '0.25rem 0 0' }}>
                        AstroCat-measured FWHM/HFR of Light subs{d ? ` · ${d.measured.toLocaleString()} subs` : ''}
                        {pxNote ? ` · ${pxNote}` : ''}
                    </p>
                </div>
                <div className="quality-stats-controls">
                    <select className="input select" value={rigId || (d ? String(d.rig_id) : '')} onChange={(e) => setRigId(e.target.value)} aria-label="Rig">
                        {rigsByFwhm.map((r) => (
                            <option key={r.rig_id} value={String(r.rig_id)}>{r.rig_name || `Rig ${r.rig_id}`} ({r.measured})</option>
                        ))}
                        <option value="ALL">All rigs (distribution only)</option>
                    </select>
                    <QualityUnitsToggle />
                </div>
            </div>

            {result.error && !loading && <p className="quality-note quality-error">{result.error}</p>}
            {!d && loading && <div className="loading-state"><div className="spinner" /></div>}

            {d && (
                <div className="charts-grid" style={{ opacity: loading ? 0.5 : 1, transition: 'opacity 0.2s' }}>
                    <Card title="FWHM distribution" empty={!hist.length && 'Not enough measured subs yet.'}
                        note={d.rig_id === 'ALL' ? 'All rigs mixed: compare rigs on Targets or Equipment instead.' : null}>
                        <ResponsiveContainer width="100%" height={260}>
                            <BarChart data={hist} barCategoryGap={2}>
                                <CartesianGrid vertical={false} stroke="var(--color-border)" strokeOpacity={0.5} />
                                <XAxis dataKey="label" {...AXIS} tickFormatter={(v) => `${v}${u.trim()}`} />
                                <YAxis {...AXIS} width={40} allowDecimals={false} />
                                <Tooltip cursor={{ fill: 'rgba(255,255,255,0.05)' }} contentStyle={TOOLTIP_STYLE}
                                    formatter={(v) => [`${v} subs`, 'Count']}
                                    labelFormatter={(_, p) => (p?.[0] ? `${fmt(p[0].payload.from)} – ${fmt(p[0].payload.to)}` : '')} />
                                <Bar dataKey="count" fill={BAR} radius={[4, 4, 0, 0]} />
                                {medianBin && (
                                    <ReferenceLine x={medianBin.label} stroke="var(--color-text-secondary)" strokeWidth={2} strokeDasharray="4 4"
                                        label={{ value: `median ${fmt(d.median)}`, position: 'top', fontSize: 10, fill: 'var(--color-text-secondary)' }} />
                                )}
                            </BarChart>
                        </ResponsiveContainer>
                    </Card>

                    {d.rig_id !== 'ALL' && (
                        <>
                            <Card title="Median FWHM by month" empty={d.monthly.length < 2 && 'Needs at least two months of measured subs.'}
                                note="Seasonal seeing: lower is sharper. Months with fewer than 5 subs are left out.">
                                <ResponsiveContainer width="100%" height={260}>
                                    <LineChart data={d.monthly}>
                                        <CartesianGrid vertical={false} stroke="var(--color-border)" strokeOpacity={0.5} />
                                        <XAxis dataKey="month" {...AXIS} />
                                        <YAxis {...AXIS} width={48} domain={['auto', 'auto']} tickFormatter={(v) => `${Number(v).toFixed(1)}${u.trim()}`} />
                                        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v, _n, p) => [`${fmt(v)} (${p.payload.n} subs)`, 'Median FWHM']} />
                                        <Line dataKey="median" stroke="var(--color-text-secondary)" strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
                                    </LineChart>
                                </ResponsiveContainer>
                            </Card>

                            <Card title="Median FWHM by altitude" empty={d.by_altitude.length < 2 && 'Needs plate-solved subs with a known site.'}
                                note="Seeing worsens nearer the horizon (roughly airmass^0.6). A steep rise at low altitude says image higher when you can.">
                                <ResponsiveContainer width="100%" height={260}>
                                    <BarChart data={d.by_altitude.map((b) => ({ ...b, label: `${b.alt_from}–${b.alt_to}°` }))}>
                                        <CartesianGrid vertical={false} stroke="var(--color-border)" strokeOpacity={0.5} />
                                        <XAxis dataKey="label" {...AXIS} />
                                        <YAxis {...AXIS} width={48} tickFormatter={(v) => `${Number(v).toFixed(1)}${u.trim()}`} />
                                        <Tooltip cursor={{ fill: 'rgba(255,255,255,0.05)' }} contentStyle={TOOLTIP_STYLE}
                                            formatter={(v, _n, p) => [`${fmt(v)} (${p.payload.n} subs)`, 'Median FWHM']} />
                                        <Bar dataKey="median" fill={BAR} radius={[4, 4, 0, 0]} />
                                    </BarChart>
                                </ResponsiveContainer>
                            </Card>

                            <Card title="Focus offset by filter" empty={filters.length < 2 && 'Needs at least two filters with 5+ measured subs.'}
                                note={worstFilter && Math.abs(worstFilter.offset_pct) >= 8
                                    ? `${worstFilter.filter} is ${Math.abs(worstFilter.offset_pct)}% ${worstFilter.offset_pct > 0 ? 'softer' : 'sharper'} than ${filters.find((f) => f.reference)?.filter}. A consistent gap like this usually means a filter focus offset is needed in your capture software.`
                                    : 'FWHM per filter compared with the reference filter (L, or the most-used one).'}>
                                <ResponsiveContainer width="100%" height={Math.max(160, filters.length * 36)}>
                                    <BarChart data={filters} layout="vertical" margin={{ right: 48 }}>
                                        <CartesianGrid horizontal={false} stroke="var(--color-border)" strokeOpacity={0.5} />
                                        <XAxis type="number" {...AXIS} tickFormatter={(v) => `${v > 0 ? '+' : ''}${v}%`} domain={['auto', 'auto']} />
                                        <YAxis type="category" dataKey="filter" {...AXIS} width={48} />
                                        <ReferenceLine x={0} stroke="var(--color-text-muted)" />
                                        <Tooltip cursor={{ fill: 'rgba(255,255,255,0.05)' }} contentStyle={TOOLTIP_STYLE}
                                            formatter={(v, _n, p) => [`${v > 0 ? '+' : ''}${v}% · median ${fmt(p.payload.median)} · ${p.payload.n} subs`, p.payload.reference ? 'Reference' : 'vs reference']} />
                                        <Bar dataKey="offset_pct" radius={4} isAnimationActive={false}>
                                            {filters.map((f) => <Cell key={f.filter} fill={filterColor(f.filter)} />)}
                                            <LabelList dataKey="offset_pct" position="right" fill="var(--color-text-secondary)" fontSize={11}
                                                formatter={(v) => (v === 0 ? 'ref' : `${v > 0 ? '+' : ''}${v}%`)} />
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </Card>

                            <Card title="HFR vs focuser temperature" empty={!temp?.bins?.length && 'No focuser temperatures recorded for this rig.'} note={slopeNote}>
                                <ResponsiveContainer width="100%" height={260}>
                                    <LineChart data={(temp?.bins || []).map((b) => ({ ...b, label: b.temp_from }))}>
                                        <CartesianGrid vertical={false} stroke="var(--color-border)" strokeOpacity={0.5} />
                                        <XAxis dataKey="label" type="number" domain={['dataMin', 'dataMax']} {...AXIS} tickFormatter={(v) => `${v}°C`} />
                                        <YAxis {...AXIS} width={48} domain={['auto', 'auto']} tickFormatter={(v) => `${Number(v).toFixed(1)} px`} />
                                        <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(v) => `${v} to ${v + 1} °C`}
                                            formatter={(v, _n, p) => [`${Number(v).toFixed(2)} px (${p.payload.n} subs)`, 'Median HFR']} />
                                        <Line dataKey="median_hfr_px" stroke="var(--color-text-secondary)" strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
                                    </LineChart>
                                </ResponsiveContainer>
                            </Card>
                        </>
                    )}
                </div>
            )}
        </section>
    );
}
