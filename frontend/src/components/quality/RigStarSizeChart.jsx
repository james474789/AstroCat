import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, LabelList } from 'recharts';
import { fetchQualityStats } from '../../api/client';
import { useQualityUnits } from '../../context/QualityUnitsContext';
import { TOOLTIP_STYLE, AXIS, BAR } from './chartStyle';
import './Quality.css';

// Q2: median star size per rig (docs/design/Q2-per-rig-star-size.md). Independent of the
// rig picked in the Star Quality section; reads `by_rig` from /quality/stats.

const unitLabel = (u) => (u === 'PX' ? 'px' : '″');

function RigTooltip({ active, payload, metric }) {
    if (!active || !payload?.length) return null;
    const d = payload[0].payload;
    return (
        <div style={{ ...TOOLTIP_STYLE, padding: '0.5rem 0.75rem', fontSize: 12 }}>
            <div style={{ fontWeight: 600 }}>{d.rig_name || `Rig ${d.rig_id}`}</div>
            <div>Median {metric.toUpperCase()}: {d.value.toFixed(2)}{unitLabel(d.units)}</div>
            <div>{d.n} subs</div>
            <div style={{ opacity: 0.8 }}>Click to view images</div>
            {d.units === 'PX' && <div style={{ opacity: 0.8 }}>No plate scale: in pixels</div>}
        </div>
    );
}

export default function RigStarSizeChart() {
    const { units } = useQualityUnits();
    const navigate = useNavigate();
    const [metric, setMetric] = useState('fwhm');
    const { data, isLoading, error } = useQuery({
        queryKey: ['qualityStats', 'byRig', units],
        queryFn: () => fetchQualityStats({ units }),
    });

    const rows = (data?.by_rig || [])
        .map((r) => ({ ...r, value: r[`${metric}_median`] }))
        .filter((r) => r.value != null)
        .sort((a, b) => a.value - b.value);
    const hasPx = rows.some((r) => r.units === 'PX');
    const empty = error ? `Could not load star sizes: ${error.message}`
        : isLoading ? 'Loading…'
            : rows.length < 2 ? 'Need at least two rigs with measured subs' : null;

    return (
        <div className="chart-card">
            <div className="chart-header">
                <h3 className="chart-title">Median Star Size by Rig</h3>
                <div className="quality-units-toggle" role="group" aria-label="Star size metric">
                    {['fwhm', 'hfr'].map((m) => (
                        <button key={m} className={metric === m ? 'active' : ''} onClick={() => setMetric(m)}>
                            {m.toUpperCase()}
                        </button>
                    ))}
                </div>
            </div>
            {empty ? <p className="quality-note">{empty}</p> : (
                <>
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart data={rows} margin={{ top: 20 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" vertical={false} />
                            <XAxis dataKey="rig_id" hide />
                            <YAxis {...AXIS} />
                            <Tooltip cursor={{ fill: 'rgba(255,255,255,0.05)' }} content={<RigTooltip metric={metric} />} />
                            <Bar dataKey="value" radius={[4, 4, 0, 0]} style={{ cursor: 'pointer' }}
                                onClick={(d) => navigate(`/search?rig_id=${d.rig_id}&frame_type=LIGHT`)}>
                                <LabelList dataKey="value" position="top" fill="var(--color-text-secondary)" fontSize={11}
                                    formatter={(v) => v.toFixed(2)} />
                                {rows.map((r) => (
                                    <Cell key={r.rig_id} fill={BAR} fillOpacity={r.units === 'PX' ? 0.35 : 1} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                    <p className="quality-note" style={{ marginTop: '0.5rem' }}>
                        Hover a bar for the rig name; click it to view that rig's images.
                        {hasPx && ' Dimmed bars are in pixels (no plate scale) and are not directly comparable with the others.'}
                    </p>
                </>
            )}
        </div>
    );
}
