import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, ArrowUp, ArrowDown, AlertTriangle } from 'lucide-react';
import { fetchNightTimeline, fetchQualityNights } from '../api/client';
import SessionQualityChart from '../components/quality/SessionQualityChart';
import SessionSummary from '../components/quality/SessionSummary';
import QualityValue from '../components/quality/QualityValue';
import QualityUnitsToggle from '../components/quality/QualityUnitsToggle';
import { Button, EmptyState, PageHeader, Spinner } from '../components/ui';
import './TargetDetail.css'; // shared .target-section / .target-filter-table rules
import './NightReport.css';

// Q1c Night Report (docs/design/20260927-Q1-star-quality.md §8.2): how star quality
// varied through one observing night, per rig, with every sub listed.

const FLAG_LABELS = { SOFT: 'Soft', CLOUD: 'Few stars', TRAILED: 'Elongated' };

function formatNight(night) {
    if (!night) return '';
    const d = new Date(`${night}T12:00:00Z`);
    return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

function timeFmt(tz) {
    try {
        return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz || 'UTC' });
    } catch {
        return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' });
    }
}

const COLUMNS = [
    { key: 't', label: 'Time' },
    { key: 'target_key', label: 'Target' },
    { key: 'filter', label: 'Filter' },
    { key: 'fwhm_px', label: 'FWHM' },
    { key: 'hfr_px', label: 'HFR' },
    { key: 'eccentricity', label: 'Ecc.' },
    { key: 'star_count', label: 'Stars' },
    { key: 'alt_deg', label: 'Alt.' },
    { key: 'flag', label: 'Flag' },
];

export default function NightReport() {
    const { night: nightParam } = useParams();
    const [searchParams, setSearchParams] = useSearchParams();
    const navigate = useNavigate();
    const targetKey = searchParams.get('target') || '';
    const rigParam = searchParams.get('rig');

    const [nights, setNights] = useState(null);
    // The last response, tagged with the request it answers; loading is derived
    // (the tag differs from the current request) and the previous chart stays up.
    const [result, setResult] = useState({ key: null, data: null, error: null });
    const [sort, setSort] = useState({ key: 't', dir: 1 });
    const [suspectOnly, setSuspectOnly] = useState(false);

    useEffect(() => {
        fetchQualityNights(targetKey ? { target_key: targetKey } : {})
            .then((d) => setNights(d.nights))
            .catch(() => setNights([]));
    }, [targetKey]);

    // No night in the URL: open the most recent one.
    const night = nightParam || nights?.[0]?.night || null;
    useEffect(() => {
        if (!nightParam && nights?.length) {
            navigate(`/nights/${nights[0].night}${targetKey ? `?target=${encodeURIComponent(targetKey)}` : ''}`, { replace: true });
        }
    }, [nightParam, nights, navigate, targetKey]);

    const requestKey = night ? `${night}|${targetKey}` : null;
    useEffect(() => {
        if (!night) return undefined;
        let cancelled = false;
        const key = `${night}|${targetKey}`;
        fetchNightTimeline(night, targetKey ? { target_key: targetKey } : {})
            .then((data) => { if (!cancelled) setResult({ key, data, error: null }); })
            .catch((e) => { if (!cancelled) setResult({ key, data: null, error: e.message || 'No subs on this night' }); });
        return () => { cancelled = true; };
    }, [night, targetKey]);
    const loading = requestKey !== null && result.key !== requestKey;
    const timeline = result.data;
    const error = loading ? null : result.error;

    // Default rig: the one with the most subs that night.
    const rigCounts = useMemo(() => {
        const counts = new Map();
        (timeline?.points || []).forEach((p) => counts.set(p.rig_id, (counts.get(p.rig_id) || 0) + 1));
        return counts;
    }, [timeline]);
    const rigs = timeline?.rigs || [];
    const defaultRig = [...rigCounts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'ALL';
    const rigId = rigParam === 'ALL' ? 'ALL'
        : rigParam != null && rigs.some((r) => String(r.rig_id) === rigParam) ? rigs.find((r) => String(r.rig_id) === rigParam).rig_id
            : (rigs.length > 1 ? defaultRig : 'ALL');

    function setParam(key, value) {
        const next = new URLSearchParams(searchParams);
        if (value == null || value === '') next.delete(key); else next.set(key, value);
        setSearchParams(next, { replace: true });
    }

    const index = nights ? nights.findIndex((n) => n.night === night) : -1;
    const older = index >= 0 ? nights[index + 1] : null;
    const newer = index > 0 ? nights[index - 1] : null;
    const goto = (n) => navigate(`/nights/${n.night}${searchParams.toString() ? `?${searchParams}` : ''}`);

    const fmt = useMemo(() => timeFmt(timeline?.site?.timezone), [timeline]);
    const rows = useMemo(() => {
        let pts = (timeline?.points || []).filter((p) => rigId === 'ALL' || p.rig_id === rigId);
        if (suspectOnly) pts = pts.filter((p) => p.flag);
        const { key, dir } = sort;
        return [...pts].sort((a, b) => {
            const va = a[key]; const vb = b[key];
            if (va == null && vb == null) return 0;
            if (va == null) return 1;
            if (vb == null) return -1;
            return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
        });
    }, [timeline, rigId, sort, suspectOnly]);
    const flaggedCount = (timeline?.points || []).filter((p) => (rigId === 'ALL' || p.rig_id === rigId) && p.flag).length;

    return (
        <div className="page-night-report">
            <nav className="breadcrumb">
                <Link to="/nights">Nights</Link>
                <span>/</span>
                <span>{formatNight(night)}</span>
                {targetKey && (<><span>/</span><Link to={`/targets/${encodeURIComponent(targetKey)}`}>{targetKey}</Link></>)}
            </nav>

            <PageHeader
                title={`Night of ${formatNight(night) || '…'}`}
                subtitle={timeline && (
                    <>
                        {timeline.points.length} subs
                        {timeline.targets.length > 0 && <> · {timeline.targets.map((t, i) => (
                            <span key={t}>{i > 0 && ', '}<Link to={`/targets/${encodeURIComponent(t)}`}>{t}</Link></span>
                        ))}</>}
                        {timeline.site?.name && <> · {timeline.site.name}</>}
                    </>
                )}
                actions={(
                    <div className="night-nav">
                        <Button size="sm" disabled={!older} onClick={() => older && goto(older)} title="Previous night" icon={<ChevronLeft size={16} />}>
                            Older
                        </Button>
                        <select className="input select" value={night || ''} onChange={(e) => goto({ night: e.target.value })} aria-label="Night">
                            {(nights || []).map((n) => (
                                <option key={n.night} value={n.night}>
                                    {formatNight(n.night)} · {n.subs} subs{n.measured ? ` (${n.measured} measured)` : ''}
                                </option>
                            ))}
                        </select>
                        <Button size="sm" disabled={!newer} onClick={() => newer && goto(newer)} title="Next night">
                            Newer <ChevronRight size={16} />
                        </Button>
                        <QualityUnitsToggle />
                    </div>
                )}
            />

            {targetKey && (
                <div className="night-scope">
                    Showing only <strong>{targetKey}</strong>. <button type="button" className="btn-link" onClick={() => setParam('target', null)}>Show the whole night</button>
                </div>
            )}

            {rigs.length > 1 && (
                <div className="night-rigs" role="group" aria-label="Rig">
                    {rigs.map((r) => (
                        <Button key={String(r.rig_id)} size="sm" variant={rigId === r.rig_id ? 'filled' : 'tinted'}
                            onClick={() => setParam('rig', r.rig_id == null ? 'null' : String(r.rig_id))}>
                            {r.rig_name || 'Unassigned rig'} ({rigCounts.get(r.rig_id) || 0})
                        </Button>
                    ))}
                    <Button size="sm" variant={rigId === 'ALL' ? 'filled' : 'tinted'} onClick={() => setParam('rig', 'ALL')}>
                        All rigs
                    </Button>
                </div>
            )}

            {loading && !timeline && <div className="loading-state"><Spinner /><p>Loading night…</p></div>}
            {error && !loading && <EmptyState title={error} />}
            {nights && nights.length === 0 && !loading && (
                <EmptyState title="No nights with Light subs yet" />
            )}

            {timeline && (
                <div style={{ opacity: loading ? 0.5 : 1, transition: 'opacity 0.2s' }}>
                    <section className="target-section night-section">
                        <SessionQualityChart timeline={timeline} rigId={rigId} />
                        <SessionSummary summary={timeline.summary} rigId={rigId} />
                    </section>

                    <section className="target-section night-section">
                        <div className="night-table-head">
                            <h3 className="section-title">Subs</h3>
                            <label className="session-normalize">
                                <input type="checkbox" checked={suspectOnly} onChange={(e) => setSuspectOnly(e.target.checked)} />
                                Suspect only ({flaggedCount})
                            </label>
                        </div>
                        <div className="night-table-wrap">
                            <table className="target-filter-table night-table">
                                <thead>
                                    <tr>
                                        {COLUMNS.map((c) => (
                                            <th key={c.key} aria-sort={sort.key === c.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}>
                                                <button type="button" className="th-sort" onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? -s.dir : 1 }))}>
                                                    {c.label}{sort.key === c.key ? (sort.dir > 0 ? <ArrowUp size={12} style={{ verticalAlign: '-1px', marginLeft: 4 }} /> : <ArrowDown size={12} style={{ verticalAlign: '-1px', marginLeft: 4 }} />) : ''}
                                                </button>
                                            </th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map((p) => (
                                        <tr key={p.image_id} className={p.flag ? 'flagged' : ''} tabIndex={0}
                                            onClick={() => navigate(`/images/${p.image_id}`)}
                                            onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/images/${p.image_id}`); }}>
                                            <td>{p.t ? fmt.format(new Date(`${p.t}Z`)) : '—'}</td>
                                            <td>{p.target_key || <span className="text-muted">—</span>}</td>
                                            <td>{p.filter}</td>
                                            <td><QualityValue px={p.fwhm_px} arcsec={p.fwhm_arcsec} /></td>
                                            <td><QualityValue px={p.hfr_px} arcsec={p.hfr_arcsec} /></td>
                                            <td>{p.eccentricity != null ? p.eccentricity.toFixed(2) : <span className="text-muted">—</span>}</td>
                                            <td>{p.star_count ?? <span className="text-muted">—</span>}</td>
                                            <td>{p.alt_deg != null ? `${Math.round(p.alt_deg)}°` : <span className="text-muted">—</span>}</td>
                                            <td>{p.flag ? <span className="night-flag"><AlertTriangle size={12} style={{ verticalAlign: '-1px', marginRight: 4 }} />{FLAG_LABELS[p.flag] || p.flag}</span> : ''}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </section>
                </div>
            )}
        </div>
    );
}
